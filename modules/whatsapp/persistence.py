"""Passive metadata only. No request/response values or endpoint names are retained."""
from collections import Counter
from browser.readonly_cdp import ReadOnlyCDP

class PersistenceObserver:
    def __init__(self,browser):
        self.browser=browser;self.channel=ReadOnlyCDP(self.event,diagnostic=browser.trace.lifecycle)
        self.active=False;self.methods=Counter();self.pending=set();self.successful_write_responses=0
        self.operation_source=None;self.temporal_context=Counter()
    async def start(self):await self.channel.start(self.browser.browser.cdp_url,self.browser.guard.page_targets)
    def event(self,event):
        if not self.active:return
        params=event.get('params',{});key=(event.get('sessionId'),params.get('requestId'))
        if event.get('method')=='Network.requestWillBeSent':
            method=params.get('request',{}).get('method')
            if method in {'POST','PUT','PATCH','DELETE'}:
                self.methods[method]+=1
                operations=getattr(self.operation_source,'browser_operations',None)
                writes=[op for op in operations or [] if op.get('operation') in {'fill','select','click','search'}]
                context=('UNAVAILABLE' if operations is None else 'NO_AGENT_FIELD_OPERATION_ATTEMPTED' if not writes else
                    'AGENT_OPERATION_IN_PROGRESS' if writes[-1].get('outcome')=='ATTEMPTED' else 'AFTER_AGENT_OPERATION')
                self.temporal_context[context]+=1
                if len(self.pending)<128:self.pending.add(key)
        elif event.get('method')=='Network.responseReceived' and key in self.pending:
            self.pending.discard(key)
            status=params.get('response',{}).get('status',0)
            if 200<=status<300:self.successful_write_responses+=1
        elif event.get('method') in {'Network.loadingFailed','Network.loadingFinished'}:self.pending.discard(key)
    def summary(self):
        available=self.channel.ready and not self.channel.failure and not self.channel.closed
        return {'server_persistence':'UNDETERMINED','observation_status':'AVAILABLE' if available else 'UNAVAILABLE',
            'non_get_requests_during_setup':dict(self.methods),'successful_non_get_responses':self.successful_write_responses,
            'interpretation':'NETWORK_ACTIVITY_DOES_NOT_PROVE_PERSISTENCE_OR_CAUSATION',
            'non_get_temporal_context':dict(self.temporal_context),'agent_causation':'NOT_ESTABLISHED',
            'observation_scope':'ALL_ATTACHED_OWNED_PAGE_TARGETS',
            'explicit_commit_actions':0}
    async def close(self):await self.channel.close();self.pending.clear()
