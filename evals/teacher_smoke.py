"""One optional API-backed synthetic Teacher compatibility test. Never launches a browser."""
import argparse
import asyncio
import json
from pathlib import Path
from app.config import ROOT,ConfigurationError
from app.trace import Trace
from app.state import Result
from deep_agents.controller import review_completed
from deep_agents.models import openai_model,MAX_OUTPUT_TOKENS
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget

SOURCE_RUN='5b4e2f5588184f63aceda7def6c2fe05'

def synthetic_fixture():
    source=json.loads((ROOT/'outputs'/f'{SOURCE_RUN}-result.json').read_text())
    events=json.loads((ROOT/'outputs'/f'{SOURCE_RUN}.json').read_text())
    if source.get('source')!='synthetic' or source.get('mocked') is not True or source.get('actual_api_requests')!=0 or source['verification']!=str(Result.AUTHENTICATED_AND_VERIFIED):
        raise ConfigurationError('Teacher smoke test requires the fixed verified offline synthetic source')
    selected=[]
    for index,event in enumerate(events):
        if event['action_category'] in {'navigate','observe','entry','verify','outcome'}:
            selected.append({'action_category':event['action_category'],'observation':event.get('observation'),
                'step_result':event['step_result'],'source_event_index':index})
    if len(selected)>12:raise ConfigurationError('Synthetic Teacher fixture exceeds bounded context')
    return source,selected

async def run_teacher_smoke(mode='offline',authorized_api=False,api_request_budget=0,max_output_tokens=500):
    # Authorization must precede private key access, model creation and any network request.
    if mode not in {'offline','openai'}:raise ConfigurationError('Unknown Teacher smoke mode')
    if mode=='openai' and (not authorized_api or api_request_budget!=1 or max_output_tokens!=MAX_OUTPUT_TOKENS):
        raise ConfigurationError('Separate approval for exactly one API request and at most 500 output tokens is required')
    if mode=='offline' and api_request_budget:
        raise ConfigurationError('Offline Teacher smoke uses no API budget')
    source,events=synthetic_fixture()
    trace=Trace()
    # Keep source numbering in provenance only; Teacher citations use one canonical namespace.
    source_event_mapping=[e['source_event_index'] for e in events]
    trace.events=[{k:v for k,v in e.items() if k!='source_event_index'} for e in events]
    trace.reasoner_mode='deepagents_offline_test_double' if mode=='offline' else 'deepagents_openai_gpt-6-luna'
    trace.verification_snapshot=source.get('verification_snapshot')
    trace.final_outcome={'verification':source['verification'],'reason_code':'VERIFIED_SYNTHETIC_SOURCE'}
    # Facts are retained as contextual evidence, mapped to their preceding compact event.
    for fact in source['step_facts']:
        index=max((i for i,e in enumerate(events) if e['source_event_index']<=fact['event_index']),default=0)
        trace.step_facts.append({'kind':fact['kind'],'event_index':index})
    result={'run_id':trace.run_id,'source':'synthetic_teacher_smoke','source_run':SOURCE_RUN,
        'verification':source['verification'],'reason_code':'VERIFIED_SYNTHETIC_SOURCE','missing_evidence':[],
        'teacher_proposal':None,'teacher_error':None,'metrics':source.get('metrics',{}),'source_event_mapping':source_event_mapping}
    budget=InferenceBudget(1,role_limits={'doer':0,'teacher':1})
    model=SyntheticChatModel(role='teacher') if mode=='offline' else openai_model('teacher',max_output_tokens=max_output_tokens)
    await review_completed(result,trace,model,budget,mode=='offline')
    result.update(mode=mode,model='scripted-synthetic' if mode=='offline' else 'gpt-6-luna',
        actual_api_requests=trace.openai_api_calls,model_requests=budget.requests,
        max_output_tokens_per_request=0 if mode=='offline' else MAX_OUTPUT_TOKENS,
        token_usage=trace.token_usage,token_usage_available=trace.token_usage_available,
        role_usage=trace.role_usage,model_diagnostics=trace.model_diagnostics,browser_launched=False,
        model_availability_lookups=0,sdk_retries=0)
    (ROOT/'outputs'/f'{trace.run_id}-teacher-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

def main():
    parser=argparse.ArgumentParser(description='Isolated synthetic Teacher test; API execution needs separate approval')
    parser.add_argument('--mode',choices=['offline','openai'],default='offline')
    parser.add_argument('--authorized-api',action='store_true')
    parser.add_argument('--api-request-budget',type=int,default=0)
    parser.add_argument('--max-output-tokens',type=int,default=500)
    args=parser.parse_args()
    try:
        result=asyncio.run(run_teacher_smoke(args.mode,args.authorized_api,args.api_request_budget,args.max_output_tokens))
        print(json.dumps(result,indent=2))
        if result['teacher_status']!='ANALYZED':raise SystemExit(1)
    except ConfigurationError as error:
        print(str(error));raise SystemExit(1)
    except Exception:
        print('Teacher test stopped; private exception data omitted.');raise SystemExit(1)

if __name__=='__main__':main()
