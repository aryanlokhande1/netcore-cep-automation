"""Private light-DOM node identity, independent of model references/selector text."""
from modules.whatsapp.contract import SetupStop
from browser.protected_tabs import bound_tab_observable
from browser.navigation_guard import GuardProtocolError


def lost_observed_node(error):
    cause = error.__cause__
    return (isinstance(error, SetupStop) and error.code == 'CONTROL_IDENTITY_UNAVAILABLE'
            and isinstance(cause, GuardProtocolError) and cause.protocol_reason == 'NODE_ID_NOT_FOUND')


def protected_document(browser):
    binding=getattr(browser,'setup_binding',None);guard=browser.guard
    if binding is None or not guard or guard.failed or guard.blocking_hosts or not bound_tab_observable(guard,binding):
        raise SetupStop('PROTECTED_SETUP_TARGET_UNAVAILABLE')
    owner=guard.session_states[binding.session]
    return (owner.target,owner.session,owner.generation,owner.document_generation,owner.frame_id,owner.loader_id)


async def observed_node(browser,selector):
    """Selectors originate only in the trusted readonly observer; IDs stay private."""
    before=protected_document(browser);session=before[1]
    try:
        root=await browser.guard.send('DOM.getDocument',{'depth':0,'pierce':False},session)
        found=await browser.guard.send('DOM.querySelectorAll',{'nodeId':root['root']['nodeId'],'selector':selector},session)
        nodes=found.get('nodeIds',[])
        if len(nodes)!=1:raise SetupStop('CONTROL_IDENTITY_NOT_UNIQUE')
        data=await browser.guard.send('DOM.describeNode',{'nodeId':nodes[0],'depth':0,'pierce':False},session)
        backend=data['node']['backendNodeId']
        if type(backend) is not int or backend<=0:raise SetupStop('CONTROL_IDENTITY_UNAVAILABLE')
        if protected_document(browser)!=before:raise SetupStop('SETUP_DOCUMENT_CHANGED')
        return (*before,backend)
    except SetupStop:raise
    except GuardProtocolError as error:raise SetupStop('CONTROL_IDENTITY_UNAVAILABLE') from error
    except Exception:raise SetupStop('CONTROL_IDENTITY_UNAVAILABLE') from None


async def stagehand_locator(browser,selector,identity):
    # The actual SDK's selector engine may inspect additional roots. Its unique
    # match must agree with the proven document selector before any action.
    if await observed_node(browser,selector)!=identity:raise SetupStop('STALE_CONTROL_REFERENCE')
    locator=browser.page.locator(selector)
    if await locator.count(timeout=2000)!=1:raise SetupStop('ACTION_LOCATOR_NOT_UNIQUE')
    if await observed_node(browser,selector)!=identity:raise SetupStop('STALE_CONTROL_REFERENCE')
    return locator
