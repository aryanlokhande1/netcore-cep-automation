"""Protected, bounded host observations; never a global safety certificate.

``binding`` is private controller state. Never log, persist or send it to a model.
Closed-root contents are not traversed and host location never implies safety.
"""
import asyncio
from collections import deque
import hashlib
import json
from urllib.parse import urlsplit

from browser.metadata_channel import metadata_channel
from browser.protected_tabs import bound_tab_observable


FACT_VALUES = {
    'host_kind': {'CUSTOM', 'STANDARD', 'UNKNOWN'},
    'role_category': {'DIALOG', 'ALERT', 'STATUS', 'NAVIGATION', 'FORM', 'FORM_CONTROL',
                      'CONTENT', 'PRESENTATIONAL', 'UNESTABLISHED'},
    'visibility': {'VISIBLE', 'NOT_VISIBLE', 'UNKNOWN'},
    'form_relation': {'INSIDE_FORM', 'OUTSIDE_FORM', 'INTERSECTS_FORM', 'UNKNOWN'},
    'same_form': {'YES', 'NO', 'UNKNOWN'},
    'document_scope': {'TOP_LEVEL_ALLOWED', 'PERMITTED_FRAME', 'UNAPPROVED', 'UNKNOWN'},
    'component': {'UNESTABLISHED'},
    'function': {'UNESTABLISHED'},
}

# App-owned synchronous reads only. No text/value extraction, event dispatch,
# focus, scrolling, attributes, credentials or shadow-root contents are accessed.
HOST_FACTS = r'''function(settings) {
 const e=this,d=e?.ownerDocument;
 const result={host_kind:'UNKNOWN',role_category:'UNESTABLISHED',visibility:'UNKNOWN',
   form_relation:'UNKNOWN',same_form:'UNKNOWN',document_scope:'UNKNOWN',
   component:'UNESTABLISHED',function:'UNESTABLISHED'};
 if(!e||e.nodeType!==1||!d||!e.isConnected)return result;
 const tag=(e.localName||'').toLowerCase();
 result.host_kind=tag.includes('-')?'CUSTOM':/^[a-z][a-z0-9]*$/.test(tag)?'STANDARD':'UNKNOWN';
 const roles={dialog:'DIALOG',alertdialog:'DIALOG',alert:'ALERT',status:'STATUS',
   navigation:'NAVIGATION',form:'FORM',textbox:'FORM_CONTROL',combobox:'FORM_CONTROL',
   button:'FORM_CONTROL',listbox:'FORM_CONTROL',main:'CONTENT',region:'CONTENT',
   presentation:'PRESENTATIONAL',none:'PRESENTATIONAL'};
 result.role_category=roles[(e.getAttribute('role')||'').toLowerCase()]||'UNESTABLISHED';
 let allowed=false;
 try {const u=new URL(d.URL);allowed=u.protocol==='about:'||settings.origins.includes(u.origin);}
 catch(_) {}
 if(!allowed){result.document_scope='UNAPPROVED';return result;}
 try {result.document_scope=d.defaultView===d.defaultView.top?'TOP_LEVEL_ALLOWED':'PERMITTED_FRAME';}
 catch(_) {return result;}
 const style=d.defaultView.getComputedStyle(e),rect=e.getBoundingClientRect();
 result.visibility=e.getClientRects().length>0&&rect.width>0&&rect.height>0&&
   style.display!=='none'&&style.visibility!=='hidden'&&style.visibility!=='collapse'?
   'VISIBLE':'NOT_VISIBLE';
 let matches;
 try {matches=d.querySelectorAll(settings.form_selector);} catch(_) {return result;}
 if(matches.length!==1)return result;
 const form=matches[0];
 if(!form.matches('form,[role=form]')||!form.isConnected)return result;
 result.same_form=e.closest('form,[role=form]')===form?'YES':'NO';
 if(form===e||form.contains(e))result.form_relation='INSIDE_FORM';
 else if(e.contains(form))result.form_relation='INTERSECTS_FORM';
 else {
   const f=form.getBoundingClientRect();
   if(rect.width>0&&rect.height>0&&f.width>0&&f.height>0)
     result.form_relation=rect.left<f.right&&rect.right>f.left&&rect.top<f.bottom&&rect.bottom>f.top?
       'INTERSECTS_FORM':'OUTSIDE_FORM';
 }
 return result;
}'''


class ScopeUnavailable(Exception):
    pass


def unknown_facts():
    return {key: 'UNESTABLISHED' if key in {'component', 'function', 'role_category'}
            else 'UNKNOWN' for key in FACT_VALUES}


def canonical_facts(value):
    if not isinstance(value, dict) or set(value) != set(FACT_VALUES):
        raise ScopeUnavailable
    if any(type(value[key]) is not str or value[key] not in choices
           for key, choices in FACT_VALUES.items()):
        raise ScopeUnavailable
    return {key: value[key] for key in FACT_VALUES}


async def investigate_closed_scope(browser, form_selector):
    """Investigate author-root hosts, retaining unestablished component/function.

    ``complete`` means these bounded observations completed coherently. It does
    not mean closed contents were inspected, or that mutations are authorized.
    At most 64 application read commands, 12k nodes, 8 documents and depth 128;
    the channel's owned attach/detach and object release are separate cleanup.
    """
    guard = browser.guard
    binding = getattr(browser, 'setup_binding', None)
    result = {'complete': False, 'risks': [], 'binding': None, 'closed_roots': 0,
              'ambient_mutation_observed': False}
    identity = None
    hosts = []
    commands = 0

    def current_identity():
        if (guard is None or browser.guard is not guard or browser.close_started or
                guard.failed or guard.closing or guard.blocking_hosts or binding is None or
                not bound_tab_observable(guard, binding)):
            raise ScopeUnavailable
        owner = guard.session_states.get(binding.session)
        if owner is None or owner.kind != 'page':
            raise ScopeUnavailable
        return (owner.target, owner.session, owner.generation, owner.document_generation,
                owner.frame_id, owner.loader_id)

    def assert_current():
        if current_identity() != identity:
            raise ScopeUnavailable

    async def read(send, method, params):
        nonlocal commands
        assert_current()
        if commands >= 64:
            raise ScopeUnavailable
        commands += 1
        response = await send(method, params, binding.session)
        assert_current()
        return response

    try:
        async with asyncio.timeout(3):
            if type(form_selector) is not str or not 1 <= len(form_selector) <= 1024:
                raise ScopeUnavailable
            await guard.ensure_observable()
            identity = current_identity()
            async with metadata_channel(browser, binding.session) as reader:
                assert_current()
                epoch = reader.mutation_epoch
                response = await read(reader.send, 'DOM.getDocument', {'depth': 12, 'pierce': True})
                root = response['root']
                if root.get('nodeType') != 9:
                    raise ScopeUnavailable
                root_id = root.get('backendNodeId')
                pending = deque([(root, None, 0, 0)])
                seen = set()
                documents = 0
                author_roots = 0
                while pending:
                    node, host, frame_depth, depth = pending.popleft()
                    key = node.get('backendNodeId')
                    if type(key) is not int or key <= 0 or key in seen or len(seen) >= 12000 or depth > 128:
                        raise ScopeUnavailable
                    seen.add(key)
                    kind = node.get('shadowRootType')
                    if kind:
                        if kind == 'user-agent':
                            continue
                        author_roots += 1
                        if author_roots > 64:
                            raise ScopeUnavailable
                        if kind == 'closed':
                            if host is None:
                                raise ScopeUnavailable
                            attributes = host.get('attributes', [])
                            role = next((attributes[i + 1] for i in range(0, len(attributes) - 1, 2)
                                         if attributes[i] == 'role'), None)
                            # Actual component tag/role remain transient and private;
                            # public observations contain only fixed categories.
                            hosts.append((host['backendNodeId'], key, host.get('nodeName'), role))
                            result['closed_roots'] = len(hosts)
                            result['risks'].append(unknown_facts())
                            continue
                        if kind != 'open':
                            raise ScopeUnavailable
                    if node.get('nodeType') == 9:
                        documents += 1
                        if documents > 8 or frame_depth > 3:
                            raise ScopeUnavailable
                    children = node.get('children', [])
                    if node.get('childNodeCount', 0) > len(children):
                        expanded = (await read(reader.send, 'DOM.describeNode',
                            {'backendNodeId': key, 'depth': 12, 'pierce': True}))['node']
                        if expanded.get('backendNodeId') != key:
                            raise ScopeUnavailable
                        node = expanded
                        children = node.get('children', [])
                        if node.get('childNodeCount', 0) > len(children):
                            raise ScopeUnavailable
                    for child in children:
                        pending.append((child, node, frame_depth, depth + 1))
                    for shadow in node.get('shadowRoots', []):
                        if not isinstance(shadow, dict) or shadow.get('shadowRootType') not in {'open', 'closed', 'user-agent'}:
                            raise ScopeUnavailable
                        pending.append((shadow, node, frame_depth, depth + 1))
                    child_document = node.get('contentDocument')
                    if child_document:
                        location = urlsplit(child_document.get('documentURL', ''))
                        if location.scheme == 'about' or location.scheme + '://' + location.netloc in browser.policy.origins:
                            pending.append((child_document, node, frame_depth + 1, 0))
                    if len(pending) > 12000:
                        raise ScopeUnavailable
                for index, (host_id, _, _, _) in enumerate(hosts):
                    remote = None
                    released = True
                    try:
                        resolved = await read(guard.send, 'DOM.resolveNode', {'backendNodeId': host_id})
                        remote = resolved.get('object', {}).get('objectId')
                        if type(remote) is not str or not remote:
                            raise ScopeUnavailable
                        observation = await read(guard.send, 'Runtime.callFunctionOn', {
                            'objectId': remote, 'functionDeclaration': HOST_FACTS,
                            'arguments': [{'value': {'form_selector': form_selector,
                                'origins': sorted(browser.policy.origins)}}],
                            'returnByValue': True, 'silent': True})
                        if observation.get('exceptionDetails'):
                            raise ScopeUnavailable
                        facts = canonical_facts(observation.get('result', {}).get('value'))
                        result['risks'][index] = facts
                        # A permitted frame may have no main-document Setup form.
                        # Unknown position/relationship is evidence for operator
                        # review, not an observation failure or authorization.
                        if facts['document_scope'] not in {'TOP_LEVEL_ALLOWED', 'PERMITTED_FRAME'}:
                            raise ScopeUnavailable
                    finally:
                        if remote is not None:
                            # A changed or failed guard never receives a stale command.
                            try:
                                assert_current()
                                async with asyncio.timeout(.15):
                                    await guard.send('Runtime.releaseObject', {'objectId': remote}, binding.session)
                            except Exception:
                                released = False
                    if not released:
                        raise ScopeUnavailable
                # Drain metadata mutation events after host reads without fetching
                # any additional descendants or closed-root contents.
                final = await read(reader.send, 'DOM.getDocument', {'depth': 0, 'pierce': False})
                result['ambient_mutation_observed'] = reader.mutation_epoch != epoch
                # Ambient mutations do not establish a change to an observed
                # host/form/control. Keep that uncertainty separate from the
                # protected document and actual host/root binding requirements.
                if final.get('root', {}).get('backendNodeId') != root_id:
                    raise ScopeUnavailable
                assert_current()
                result['complete'] = True
    except Exception:
        result['complete'] = False
    if identity is not None:
        # Only controller code consumes this private revocation token.
        # Ambient mutation counters are deliberately not a grant identity. A
        # clock or unrelated widget update does not reidentify this scoped set.
        proof = json.dumps([hosts, result['risks'], result['complete']], sort_keys=True, separators=(',', ':'))
        result['binding'] = (identity, hashlib.sha256(proof.encode()).hexdigest(), result['complete'])
    return result
