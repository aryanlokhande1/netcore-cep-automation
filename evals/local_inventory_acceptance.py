"""Real local Chromium inventory complexity acceptance. No network page/model/login.

The legacy inventory is preserved here only to reproduce the former 512-command
limit. Raw snapshots, text, attributes and private node/session IDs are never saved.
"""
import argparse
import asyncio
from collections import deque
from importlib.metadata import version
import json
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab,bound_tab_observable
from evals.local_shadow_safety import sanitized_error_types
from modules.whatsapp.safety import NEGATIVES,safety_diagnostic


COMPLEX_FIXTURE=r'''(kind=>{
 document.body.replaceChildren();
 const elem=(tag,parent)=>{const e=document.createElement(tag);parent.append(e);return e};
 for(let i=0;i<220;i++){
   let p=elem('section',document.body);
   for(const tag of ['div','div','label','span','span'])p=elem(tag,p);
   elem('input',p).type='text';const b=elem('button',p);elem('span',b).textContent='Local';
 }
 const root=(p,mode='open')=>{const h=elem('div',p);h.style.cssText='display:block;min-height:8px';return h.attachShadow({mode})};
 const roots=[];
 for(let i=0;i<7;i++){
   const outer=root(document.body),inner=root(outer);roots.push(outer,inner);
   elem('div',inner).textContent='Fixture';
 }
 for(let i=0;i<6;i++)elem('input',document.body).type='text';
 if(kind==='closed')elem('input',root(document.body,'closed')).type='password';
 else if(kind==='credential_form')elem('input',roots[13]).type='password';
 else if(kind==='challenge')elem('input',roots[13]).autocomplete='one-time-code';
 else if(['rejected','expired','account_restricted'].includes(kind)){
   const alert=elem('div',roots[13]);alert.setAttribute('role','alert');
   alert.textContent={rejected:'Invalid credentials',expired:'Session expired',account_restricted:'Account locked'}[kind];
 }
 return {light_elements:document.querySelectorAll('*').length};
})(KIND)'''


async def legacy_shadow_inventory(guard,session,origins,*,max_nodes=12000,max_commands=512,seconds=5):
    """Frozen pre-optimization implementation; eval-only, never used by production."""
    counts={'inventory_nodes_inspected':0,'inventory_commands':0,'inventory_open_shadow_roots':0,
        'inaccessible_shadow_roots':0,'user_agent_shadow_roots':0,'unknown_shadow_roots':0,
        'inventory_limit_reached':False,'inventory_unavailable':False,'inventory_documents_inspected':0}
    seen=set();documents=set();pending=deque()
    async def send(method,params):
        if counts['inventory_commands']>=max_commands:raise OverflowError
        counts['inventory_commands']+=1
        return await guard.send(method,params,session)
    try:
        async with asyncio.timeout(seconds):
            response=await send('DOM.getDocument',{'depth':1,'pierce':True})
            pending.append((response['root'],0,0))
            while pending:
                node,frame_depth,tree_depth=pending.popleft()
                key=node.get('backendNodeId')
                if type(key) is not int or key<=0:raise ValueError
                if key in seen:continue
                if len(seen)>=max_nodes or tree_depth>128:raise OverflowError
                seen.add(key);counts['inventory_nodes_inspected']+=1
                if node.get('nodeType')==9:
                    if key not in documents and len(documents)>=8:raise OverflowError
                    documents.add(key);counts['inventory_documents_inspected']=len(documents)
                    if frame_depth>3:raise OverflowError
                kind=node.get('shadowRootType')
                if kind:
                    if kind=='user-agent':counts['user_agent_shadow_roots']+=1;continue
                    if kind=='closed':counts['inaccessible_shadow_roots']+=1;continue
                    if kind!='open':counts['unknown_shadow_roots']+=1;continue
                    counts['inventory_open_shadow_roots']+=1
                    if counts['inventory_open_shadow_roots']>64:raise OverflowError
                children=node.get('children',[])
                if node.get('childNodeCount',0)>len(children):
                    expanded=await send('DOM.describeNode',{'backendNodeId':key,'depth':1,'pierce':True})
                    node=expanded['node'];children=node.get('children',[])
                    if node.get('childNodeCount',0)>len(children):raise ValueError
                for child in children:
                    if len(pending)>=max_nodes:raise OverflowError
                    pending.append((child,frame_depth,tree_depth+1))
                for root in node.get('shadowRoots',[]):
                    if root.get('shadowRootType') not in {'open','closed','user-agent'}:
                        counts['unknown_shadow_roots']+=1;continue
                    pending.append((root,frame_depth,tree_depth+1))
                child_document=node.get('contentDocument')
                if child_document:
                    origin=urlsplit(child_document.get('documentURL',''))
                    allowed=origin.scheme=='about' or (origin.scheme+'://'+origin.netloc) in origins
                    if allowed:pending.append((child_document,frame_depth+1,0))
    except (TimeoutError,OverflowError):counts['inventory_limit_reached']=True
    except Exception:counts['inventory_unavailable']=True
    return counts


async def probe_snapshot(guard,session):
    started=monotonic()
    raw=await guard.send('DOMSnapshot.captureSnapshot',{'computedStyles':[]},session)
    elapsed=round((monotonic()-started)*1000,3)
    docs=raw.get('documents',[]);strings=raw.get('strings',[])
    types={'open':0,'closed':0,'user-agent':0,'unknown':0};node_count=0;shadow_node_types={}
    complete_arrays=True;parent_valid=True;content_links=0;sparse_valid=True
    for doc in docs:
        nodes=doc.get('nodes',{});n=len(nodes.get('nodeType',[]));node_count+=n
        complete_arrays &= all(len(nodes.get(key,[]))==n for key in ['backendNodeId','parentIndex','nodeName','nodeValue','attributes'])
        parent_valid &= all(type(v) is int and -1<=v<i for i,v in enumerate(nodes.get('parentIndex',[])))
        rare=nodes.get('shadowRootType',{});indices=rare.get('index',[]);values=rare.get('value',[])
        sparse_valid &= len(indices)==len(values)
        for i,value in zip(indices,values):
            sparse_valid &= type(i) is int and 0<=i<n and type(value) is int and 0<=value<len(strings)
            category=strings[value] if type(value) is int and 0<=value<len(strings) else 'unknown'
            types[category if category in types else 'unknown']+=1
            if type(i) is int and 0<=i<n:
                code=str(nodes['nodeType'][i]) if nodes['nodeType'][i] in {1,3,9,11} else 'other'
                shadow_node_types[code]=shadow_node_types.get(code,0)+1
        content_links+=len(nodes.get('contentDocumentIndex',{}).get('index',[]))
    return {'documents':len(docs),'nodes':node_count,'parallel_arrays_complete':bool(complete_arrays),
        'parent_indices_valid':bool(parent_valid),'shadow_sparse_indices_valid':bool(sparse_valid),
        'content_document_links':content_links,'shadow_root_types':types,'shadow_scoped_node_type_counts':shadow_node_types,
        'payload_bytes':len(json.dumps(raw,separators=(',',':')).encode()),'duration_ms':elapsed}


async def local_inventory_acceptance(executable,*,probe_only=False,headless=False,output=None):
    executable=Path(executable)
    if not executable.is_file():raise ValueError('Installed Chromium executable required')
    trace=Trace();trace.reasoner_mode='LOCAL_COMPLEX_INVENTORY_ACCEPTANCE_NO_MODEL'
    config=Config(str(executable),headless=headless,login_url='about:blank',expected_host='127.0.0.1',
        timeout_seconds=180,max_actions=100)
    browser=StagehandClient(config,OriginPolicy({'http://127.0.0.1:9000'}),trace,fixture=True)
    report={'scope':'LOCAL_COMPLEX_ABOUT_FIXTURE_ONLY','headless':headless,'probe_only':probe_only,
        'paid_inference':0,'credential_access':False,'netcore_navigation_attempts':0,'passed':False,'cases':[]}
    started=monotonic();stage='BROWSER_START'
    try:
        await browser.start();report['browser_started']=True
        report['chromium_product']=(await browser.guard.send('Browser.getVersion'))['product']
        report['stagehand_version']=version('stagehand');stage='PROTECTED_ABOUT_TABS'
        original=browser.page;context=browser.browser.context;process=browser.process
        await original.goto('about:blank')
        async with asyncio.timeout(5):
            while True:
                browser.guard.lifecycle_changed.clear();await browser.guard.ensure_observable()
                source=browser.guard.session_states[browser.guard.primary_session]
                frame=(await browser.guard.send('Page.getFrameTree',session=source.session))['frameTree']['frame']
                if (frame.get('url')=='about:blank' and frame.get('id')==source.frame_id
                    and (frame.get('loaderId') or None)==source.loader_id):break
                await browser.guard.lifecycle_changed.wait()
        page,binding=await create_protected_tab(browser);browser.page=page;browser.setup_binding=binding
        stage='LEGACY_INVENTORY';report['fixture']=await page.evaluate(COMPLEX_FIXTURE.replace('KIND',json.dumps('benign')))
        legacy_started=monotonic();report['legacy']=await legacy_shadow_inventory(browser.guard,binding.session,browser.policy.origins)
        report['legacy']['duration_ms']=round((monotonic()-legacy_started)*1000,3)
        stage='PROTOCOL_PROBE';report['snapshot_probe']=await probe_snapshot(browser.guard,binding.session)
        await page.evaluate(COMPLEX_FIXTURE.replace('KIND',json.dumps('closed')))
        report['closed_snapshot_probe']=await probe_snapshot(browser.guard,binding.session)
        report['snapshot_probe']['suitable_for_exact_shadow_root_inventory']=False
        report['closed_snapshot_probe']['suitable_for_exact_shadow_root_inventory']=False
        if not probe_only:
            from modules.whatsapp.safety import read_safety_snapshot
            for name in ['stable_1','stable_2','stable_3',*NEGATIVES,'closed','dynamic_incoherence']:
                stage='OPTIMIZED_'+name.upper()
                fixture='benign' if name.startswith('stable') or name=='dynamic_incoherence' else name
                await page.evaluate(COMPLEX_FIXTURE.replace('KIND',json.dumps(fixture)))
                original_evaluate=page.evaluate;safety_reads=0
                async def changing_evaluate(expression,*args,**kwargs):
                    nonlocal safety_reads
                    response=await original_evaluate(expression,*args,**kwargs)
                    if 'const snapshot=' in expression and '_scope_diagnostics:snapshot.coverage' in expression:
                        safety_reads+=1
                        if safety_reads==2:
                            await original_evaluate(r'''(() => {
                              const old=[...document.body.children].find(e=>e.shadowRoot);
                              const host=document.createElement('div');host.style.cssText=old.style.cssText;
                              const outer=host.attachShadow({mode:'open'}),inner=document.createElement('div');
                              inner.style.cssText=old.shadowRoot.firstElementChild.style.cssText;outer.append(inner);
                              const root=inner.attachShadow({mode:'open'}),leaf=document.createElement('div');
                              leaf.textContent='Fixture';root.append(leaf);old.replaceWith(host);return true;
                            })()''')
                    return response
                if name=='dynamic_incoherence':page.evaluate=changing_evaluate
                began=monotonic()
                try:snapshot=await read_safety_snapshot(browser)
                finally:page.evaluate=original_evaluate
                elapsed=round((monotonic()-began)*1000,3);diagnostic=safety_diagnostic(snapshot)
                counts=diagnostic.get('scope_reason_counts',{});flags=diagnostic.get('negative_flags',{})
                if name.startswith('stable'):
                    expected=(diagnostic['code'] is None and snapshot['negative_scope_complete'] is True
                        and counts.get('open_shadow_roots_inspected')==14 and counts.get('inventory_open_shadow_roots')==14
                        and counts.get('remaining_coverage_gaps')==0 and counts.get('inventory_commands',513)<512
                        and counts.get('user_agent_shadow_roots',0)>=6 and counts.get('inventory_nodes_inspected',0)>=1750)
                elif name in NEGATIVES:
                    expected=diagnostic['code']=='AUTHENTICATION_OR_SECURITY_STOP' and flags.get(name) is True
                elif name=='closed':
                    expected=diagnostic['code']=='SAFETY_OBSERVATION_INCOMPLETE' and counts.get('inaccessible_shadow_roots')==1
                else:
                    expected=(snapshot['negative_scope_complete'] is False and diagnostic['code']=='SAFETY_OBSERVATION_INCOMPLETE'
                        and counts.get('inventory_changed') is True and safety_reads>=2
                        and counts.get('open_shadow_roots_inspected')==14)
                report['cases'].append({'fixture':name,'passed':expected,'duration_ms':elapsed,'code':diagnostic['code'],
                    'negative_flags':flags,'negative_scope_complete':snapshot.get('negative_scope_complete'),'scope_reason_counts':counts})
        stage='FINAL_OWNERSHIP_CHECK';pages=await context.pages()
        checks={'fixture_at_least_1750_light_elements':report['fixture']['light_elements']>=1750,
            'legacy_hits_512_cap':report['legacy']['inventory_commands']==512 and report['legacy']['inventory_limit_reached'] is True,
            'all_cases_passed':all(c['passed'] for c in report['cases']),
            'same_owned_process':browser.process is process,'same_context':browser.browser.context is context,
            'exactly_two_same_context_pages':len(pages)==2 and {p.page_id for p in pages}=={original.page_id,page.page_id},
            'original_available':await original.evaluate('document.documentElement!==null') is True,
            'setup_tab_protected':bound_tab_observable(browser.guard,binding),'guard_healthy':not browser.guard.failed,
            'no_premature_close':not browser.close_started and process.status_now()=='ALIVE',
            'zero_login_submissions':browser.login_submissions==0,'zero_model_calls':trace.model_calls==trace.openai_api_calls==0}
        report['checks']=checks;report['passed']=all(checks.values())
    except Exception as error:
        report.update(error_category=type(error).__name__,error_code=getattr(error,'code',None),failure_stage=stage,
            error_causes=sanitized_error_types(error),browser_created=browser.browser is not None,
            guard_created=browser.guard is not None,owned_process_identified=browser.process is not None)
    finally:
        try:await browser.close()
        except Exception:report['cleanup_exception']=True
        report['cleanup_result']=trace.cleanup_result;report['cleanup_diagnostic']=trace.cleanup_diagnostic
        report['duration_seconds']=round(monotonic()-started,3)
        report['passed']=report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
        path=Path(output or ROOT/'outputs'/'real-local-inventory-acceptance.json')
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--executable',required=True);parser.add_argument('--probe-only',action='store_true')
    args=parser.parse_args();report=asyncio.run(local_inventory_acceptance(args.executable,probe_only=args.probe_only))
    print(json.dumps(report,indent=2));raise SystemExit(0 if report['passed'] else 1)
