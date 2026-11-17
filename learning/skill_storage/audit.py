"""Read-only validation of a generated procedure against saved sanitized evidence."""
import json
import re
from pathlib import Path
from app.config import ROOT
from app.state import Result
from learning.skill_storage.registry import validate_skill_directory

def audit_candidate(directory,outputs=None):
    directory=Path(directory);outputs=Path(outputs or ROOT/'outputs')
    validate_skill_directory(directory)
    provenance=json.loads((directory/'provenance.json').read_text())
    evaluation=json.loads((directory/'evaluation.json').read_text())
    graduation=json.loads((directory/'graduation.json').read_text())
    text=(directory/'SKILL.md').read_text();history=(directory/'execution-history.md').read_text()
    records={};details=[]
    for filename in provenance['source_traces']:
        if not re.fullmatch('[a-f0-9]{32}\\.json',filename):raise ValueError('Invalid source trace identifier')
        run_id=filename[:-5]
        events=json.loads((outputs/filename).read_text())
        result=json.loads((outputs/(run_id+'-result.json')).read_text())
        if result.get('source')!='synthetic' or not result.get('mocked') or result.get('actual_api_requests')!=0:
            raise ValueError('Synthetic candidate source was mislabeled')
        if provenance['step_evidence'][run_id]!=result['step_facts']:
            raise ValueError('Step facts differ from the saved execution result')
        records[run_id]=(events,result)
        details.append({'run_id':run_id,'source':result['source'],'verification':result['verification'],
            'fact_context_events':sorted({f['event_index'] for f in result['step_facts']}),
            'positive_verifier_events':[i for i,e in enumerate(events) if e.get('observation') is not None and e['step_result']==str(Result.AUTHENTICATED_AND_VERIFIED)]})
    references=[]
    for run_id,index in re.findall(r'([a-f0-9]{32}) event (\d+)',text):
        if run_id not in records:raise ValueError('Skill cites an unknown source')
        events,result=records[run_id];index=int(index)
        facts=[f['kind'] for f in result['step_facts'] if f['event_index']==index]
        if not 0<=index<len(events) or not facts:raise ValueError('Skill event reference lacks supporting step facts')
        references.append({'run_id':run_id,'event_index':index,'facts':facts,'event_category':events[index]['action_category']})
    for run_id,status in re.findall(r'([a-f0-9]{32}) -> ([A-Z_]+)',text):
        if run_id not in records or records[run_id][1]['verification']!=status:raise ValueError('Outcome differs from source')
    current=None;history_references=0
    for line in history.splitlines():
        if line.startswith('## Run '):
            current=line[len('## Run '):]
            if current not in records:raise ValueError('Unknown history source')
        if line.startswith('Role evidence (canonical attribution only): '):
            actual=json.loads(line.split(': ',1)[1])
            if actual!=records[current][1].get('role_evidence',{}):raise ValueError('Role evidence differs from saved result')
        match=re.match(r'- Event (\d+): ([^;]+); result ([A-Z_]+)(.*)',line)
        if match:
            index=int(match[1]);events,result=records[current]
            if not 0<=index<len(events):raise ValueError('History event out of range')
            event=events[index]
            if match[2]!=(event.get('tool_name') or event['action_category']) or match[3]!=event['step_result']:
                raise ValueError('History event differs from saved trace')
            if event.get('observation') is not None:
                encoded=json.dumps(event['observation'],sort_keys=True)
                if not match[4].endswith('; '+encoded):raise ValueError('History observation differs from saved trace')
            history_references+=1
    if not references or not history_references:raise ValueError('Missing procedural evidence references')
    if provenance.get('evidence_scope')!='synthetic_only' or provenance.get('live_verified') is not False or graduation.get('live_certified') is not False:
        raise ValueError('Synthetic evidence was represented as live certification')
    for run_id in provenance['verified_run_ids']:
        events,result=records[run_id]
        if result['verification']!=str(Result.AUTHENTICATED_AND_VERIFIED):raise ValueError('Unverified source cited as positive')
        if not any(all((e.get('observation') or {}).get(k) for k in ('allowed','expected_host','dashboard','tenant_matches','readonly_access','evidence_configured')) for e in events):
            raise ValueError('No independent positive source observation')
    return {'status':'PASS','name':'netcore-login','scope':'synthetic_only','live_certified':False,
        'sources':details,'step_references':references,'history_references':history_references,
        'fact_reference_semantics':'Context markers link to trusted source-result facts; they are not standalone validation events.',
        'evaluation_passed':evaluation['passed'],'graduation_state':graduation['state']}
