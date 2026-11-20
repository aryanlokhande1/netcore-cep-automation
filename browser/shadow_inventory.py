"""Depth-batched metadata inventories. No raw nodes or private fingerprints escape."""
import asyncio
from collections import deque
import hashlib
import json
from time import monotonic
from urllib.parse import urlsplit


class InventoryLimit(Exception):
    def __init__(self,reason):self.reason=reason


async def shadow_inventory(reader,session,origins,*,max_nodes=12000,max_commands=512,seconds=5,batch_depth=12,observe=None,primary_frame=None,host_observer=None):
    """Two complete bounded passes bracket an optional read-only negative scan.

    DOMSnapshot.captureSnapshot flattens author boundaries and omits UA roots in
    the installed Chromium. Bounded DOM batches preserve their explicit modes.
    A partial pass is not evidence of DOM mutation or root-count disagreement.
    """
    counts={'inventory_nodes_inspected':0,'inventory_nodes_processed':0,'inventory_commands':0,
        'inventory_open_shadow_roots':0,'inaccessible_shadow_roots':0,'user_agent_shadow_roots':0,
        'unknown_shadow_roots':0,'inventory_documents_inspected':0,'inventory_passes':0,
        'inventory_limit_reached':False,'inventory_unavailable':False,'inventory_complete':False,
        'inventory_changed':False,'inventory_command_limit_reached':False,'inventory_time_limit_reached':False,
        'inventory_node_limit_reached':False,'inventory_depth_limit_reached':False,
        'inventory_document_limit_reached':False,'inventory_payload_limit_reached':False,
        'inventory_shadow_root_limit_reached':False,'inventory_batch_depth':batch_depth}
    started=monotonic()
    document_aliases={};frame_aliases={};host_aliases={};closed={}
    def alias(mapping,key,prefix):
        if key not in mapping:mapping[key]=prefix+str(len(mapping)+1)
        return mapping[key]
    def safe_type(name):
        value=str(name).lower()
        return value if value in {'div','span','main','nav','header','article','section','dialog','iframe','form','input','button'} else 'CUSTOM_OR_OTHER'
    async def send(method,params):
        if counts['inventory_commands']>=max_commands:raise InventoryLimit('command')
        counts['inventory_commands']+=1
        return await reader.send(method,params,session)
    async def collect():
        seen=set();documents=set();pending=deque();proof=hashlib.sha256();contexts={}
        local={'inventory_nodes_inspected':0,'inventory_open_shadow_roots':0,'inaccessible_shadow_roots':0,
            'user_agent_shadow_roots':0,'unknown_shadow_roots':0,'inventory_documents_inspected':0}
        def record():
            for key,value in local.items():counts[key]=max(counts[key],value)
        try:
            response=await send('DOM.getDocument',{'depth':batch_depth,'pierce':True})
            root=response['root']
            if root.get('nodeType')!=9:raise ValueError
            pending.append((root,0,0,None))
            while pending:
                node,frame_depth,tree_depth,parent=pending.popleft()
                key=node.get('backendNodeId')
                if type(key) is not int or key<=0:raise ValueError
                if key in seen:raise ValueError
                if len(seen)>=max_nodes:raise InventoryLimit('node')
                if tree_depth>128:raise InventoryLimit('depth')
                if node.get('nodeType')==9:
                    if len(documents)>=8 or frame_depth>3:raise InventoryLimit('document')
                    documents.add(key);local['inventory_documents_inspected']=len(documents)
                seen.add(key);local['inventory_nodes_inspected']+=1;counts['inventory_nodes_processed']+=1
                kind=node.get('shadowRootType')
                inherited=contexts.get(parent,{'document_ref':'UNKNOWN','frame_ref':'UNKNOWN','semantics':[]})
                context=dict(inherited)
                if node.get('nodeType')==9:
                    frame=node.get('frameId') or (primary_frame if frame_depth==0 else None)
                    context={'document_ref':alias(document_aliases,key,'document-'),
                        'frame_ref':alias(frame_aliases,frame,'frame-') if frame else inherited.get('child_frame_ref','UNKNOWN'),
                        'semantics':[]}
                elif node.get('frameId'):
                    context['child_frame_ref']=alias(frame_aliases,node['frameId'],'frame-')
                attributes=dict(zip(node.get('attributes',[])[::2],node.get('attributes',[])[1::2]))
                role=attributes.get('role')
                semantics=list(context['semantics'])
                for semantic in (str(node.get('nodeName','')).lower(),role):
                    if semantic in {'main','application','navigation','nav','banner','header','article','region','form','alert','alertdialog','dialog'}:
                        if semantic not in semantics:semantics.append(semantic)
                context.update(semantics=semantics[-8:],element_type=safe_type(node.get('nodeName')))
                contexts[key]=context
                # The digest is private and short-lived. Values, attributes and
                # identities are never returned, logged or persisted.
                proof.update(json.dumps([key,parent,node.get('nodeType'),node.get('nodeName'),kind,
                    node.get('nodeValue'),node.get('attributes'),node.get('documentURL')],
                    separators=(',',':'),ensure_ascii=True).encode())
                if kind:
                    if kind=='user-agent':local['user_agent_shadow_roots']+=1;continue
                    if kind=='closed':
                        local['inaccessible_shadow_roots']+=1
                        host=alias(host_aliases,parent,'host-');owner=contexts.get(parent,inherited)
                        if host in closed or len(closed)<32:
                            semantic=owner['semantics']
                            classification=('UNRELATED_TO_POSITIVE_SCOPE' if any(s in semantic for s in {'alert','alertdialog','dialog'})
                                else 'RELEVANT' if any(s in semantic for s in {'main','application','article','region','navigation','nav','banner','header'}) else 'UNKNOWN')
                            root_record=closed.setdefault(host,{'host_ref':host,'document_ref':owner['document_ref'],
                                'frame_ref':owner['frame_ref'],'frame_depth':frame_depth,
                                'host_element_type':owner.get('element_type','CUSTOM_OR_OTHER'),
                                'semantic_ancestors':semantic,'scope_classification':classification,
                                'verification_scope_intersection':'UNKNOWN' if classification=='UNKNOWN' else
                                    'EXCLUDED_TRANSIENT_SCOPE' if classification=='UNRELATED_TO_POSITIVE_SCOPE' else 'POTENTIAL_NOT_VERIFIED',
                                'security_coverage':'UNKNOWN_NO_EXEMPTION','passes':[]})
                            pass_number=counts['inventory_passes']+1
                            if (root_record['document_ref']!=owner['document_ref'] or
                                    root_record['semantic_ancestors']!=semantic):
                                root_record['scope_classification']='UNKNOWN'
                                root_record['verification_scope_intersection']='UNKNOWN'
                            if pass_number not in root_record['passes']:root_record['passes'].append(pass_number)
                            if host_observer is not None and len(closed)<=8:
                                try:rendering=await host_observer(parent)
                                except Exception:rendering={'status':'UNAVAILABLE'}
                                previous=root_record.get('host_rendering')
                                root_record['host_rendering']=rendering
                                root_record['host_rendering_consistent']=previous==rendering if previous is not None else False
                        continue
                    if kind!='open':local['unknown_shadow_roots']+=1;continue
                    local['inventory_open_shadow_roots']+=1
                    if local['inventory_open_shadow_roots']>64:raise InventoryLimit('shadow_root')
                children=node.get('children',[])
                if node.get('childNodeCount',0)>len(children):
                    expanded=await send('DOM.describeNode',{'backendNodeId':key,'depth':batch_depth,'pierce':True})
                    expanded=expanded['node']
                    if expanded.get('backendNodeId')!=key:raise ValueError
                    for field in ('nodeType','nodeName','shadowRootType','nodeValue','attributes','documentURL'):
                        if node.get(field)!=expanded.get(field):counts['inventory_changed']=True
                    node=expanded;children=node.get('children',[])
                    if node.get('childNodeCount',0)>len(children):raise ValueError
                for child in children:
                    if len(pending)>=max_nodes:raise InventoryLimit('node')
                    pending.append((child,frame_depth,tree_depth+1,key))
                for root in node.get('shadowRoots',[]):
                    if root.get('shadowRootType') not in {'open','closed','user-agent'}:
                        local['unknown_shadow_roots']+=1;continue
                    if len(pending)>=max_nodes:raise InventoryLimit('node')
                    pending.append((root,frame_depth,tree_depth+1,key))
                child_document=node.get('contentDocument')
                if child_document:
                    origin=urlsplit(child_document.get('documentURL',''))
                    allowed=origin.scheme=='about' or (origin.scheme+'://'+origin.netloc) in origins
                    if allowed:pending.append((child_document,frame_depth+1,0,key))
            counts['inventory_passes']+=1
            return proof.digest()
        finally:record()
    try:
        if not 1<=batch_depth<=12:raise ValueError
        async with asyncio.timeout(seconds):
            first=await collect()
            epoch=getattr(reader,'mutation_epoch',0)
            if observe is not None:await observe()
            second=await collect()
            counts['inventory_complete']=True
            counts['inventory_changed'] |= first!=second or epoch!=getattr(reader,'mutation_epoch',0)
    except InventoryLimit as error:
        counts['inventory_limit_reached']=True;counts['inventory_'+error.reason+'_limit_reached']=True
    except TimeoutError:
        counts['inventory_limit_reached']=counts['inventory_time_limit_reached']=True
    except Exception as error:
        if getattr(error,'code',None)=='METADATA_BYTE_LIMIT_REACHED':
            counts['inventory_limit_reached']=counts['inventory_payload_limit_reached']=True
        else:counts['inventory_unavailable']=True
    counts['inventory_duration_ms']=round((monotonic()-started)*1000)
    counts['inventory_response_bytes']=getattr(reader,'bytes_received',0)
    counts['inventory_transport_commands']=getattr(reader,'protocol_commands',counts['inventory_commands'])
    counts['closed_root_ownership']=[{k:v for k,v in item.items() if k!='passes'}|
        {'observed_passes':len(item['passes']),'persistent':len(item['passes'])==2 and counts['inventory_passes']==2}
        for item in closed.values()]
    counts['closed_root_ownership_truncated']=counts['inaccessible_shadow_roots']>len(closed)
    return counts
