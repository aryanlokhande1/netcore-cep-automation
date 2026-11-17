"""Depth-batched metadata inventories. No raw nodes or private fingerprints escape."""
import asyncio
from collections import deque
import hashlib
import json
from time import monotonic
from urllib.parse import urlsplit


class InventoryLimit(Exception):
    def __init__(self,reason):self.reason=reason


async def shadow_inventory(reader,session,origins,*,max_nodes=12000,max_commands=512,seconds=5,batch_depth=12,observe=None):
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
    async def send(method,params):
        if counts['inventory_commands']>=max_commands:raise InventoryLimit('command')
        counts['inventory_commands']+=1
        return await reader.send(method,params,session)
    async def collect():
        seen=set();documents=set();pending=deque();proof=hashlib.sha256()
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
                # The digest is private and short-lived. Values, attributes and
                # identities are never returned, logged or persisted.
                proof.update(json.dumps([key,parent,node.get('nodeType'),node.get('nodeName'),kind,
                    node.get('nodeValue'),node.get('attributes'),node.get('documentURL')],
                    separators=(',',':'),ensure_ascii=True).encode())
                if kind:
                    if kind=='user-agent':local['user_agent_shadow_roots']+=1;continue
                    if kind=='closed':local['inaccessible_shadow_roots']+=1;continue
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
    return counts
