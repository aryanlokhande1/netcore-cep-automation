import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4
from app.state import Observation, Result

class Trace:
    def __init__(self,iteration=0,strategy_version='baseline-v1',*,roles=('doer','teacher')):
        self.run_id=uuid4().hex
        self.iteration,self.strategy_version=iteration,strategy_version
        self.started=monotonic()
        self.actions=self.model_calls=self.codex_calls=self.openai_api_calls=self.reasoning_turns=0
        self.reasoner_mode='not_used'
        self.events=[]
        self.verification_snapshot=None
        self.current_observation=None
        self.final_outcome=None
        self.cleanup_result='NOT_RUN'
        self.cleanup_diagnostic=None
        self.teacher_result='NOT_RUN'
        self.token_usage_available=False
        self.token_usage={'input_tokens':0,'output_tokens':0,'total_tokens':0}
        self.step_facts=[]
        self.role_evidence={}
        self.model_diagnostics=[]
        self.boundary_diagnostics=[]
        self.model_limits={}
        if not set(roles)<={'doer','teacher','observer'}:raise ValueError('Unknown model role')
        self.role_usage={r:{'requests':0,'input_tokens':0,'output_tokens':0,'total_tokens':0,'usage_available':False} for r in roles}
        self.agent_trace_enabled=False

    def enable_agent_trace(self,*,private_values=()):
        """Opt-in V2 reporting through the existing event store; never log prompts."""
        self.agent_trace_enabled=True
        self.agent_stage='login';self.agent_phase='login_doer'
        self._agent_private=tuple(sorted({v for v in private_values if isinstance(v,str) and v},key=len,reverse=True))

    def redact_agent(self,value):
        import re
        forbidden={'password','credential','credentials','cookie','cookies','jwt','token','api_key',
            'authorization','headers','selector','selected_selector','search_selector','option_selector',
            'payload','expected','html','raw_dom','raw_image','image_url','content','messages','prompt','value'}
        if isinstance(value,dict):
            return {str(k):'[redacted]' if str(k).lower() in forbidden else self.redact_agent(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)):return [self.redact_agent(v) for v in value]
        if isinstance(value,str):
            for secret in getattr(self,'_agent_private',()):value=value.replace(secret,'[private]')
            value=re.sub(r'data:image/[^\s]+|https?://[^\s]+|Bearer\s+[^\s]+','[redacted]',value,flags=re.I)
            value=re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b','[redacted]',value)
            value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\+\d[\d ()-]{6,}|\b\d{7,15}\b','[private]',value)
            return value[:12000]
        if isinstance(value,float):
            import math
            return value if math.isfinite(value) else None
        return value if isinstance(value,(bool,int)) or value is None else '[unsupported data omitted]'

    def safe_tool_result(self,response):
        """Existing Login tools return fixed-category JSON, never credential values."""
        import re
        content=getattr(response,'content',None)
        if not isinstance(content,str) or len(content)>64000:return {'content_omitted':True,'reason':'NON_JSON_OR_OVERSIZED'}
        try:data=json.loads(content)
        except (TypeError,ValueError):return {'content_omitted':True,'reason':'NON_JSON'}
        if not isinstance(data,dict):return {'content_omitted':True,'reason':'NON_OBJECT'}
        result={}
        for key in ('ok','state','error','verification','terminal','retry_safe','submitted'):
            value=data.get(key)
            if isinstance(value,bool) or value is None:result[key]=value
            elif isinstance(value,str) and re.fullmatch('[A-Za-z_]{1,100}',value):result[key]=value
        if isinstance(data.get('observation'),dict):
            result['observation']={k:v for k,v in data['observation'].items() if type(v) is bool}
        result['other_content_omitted']=True
        return result

    def agent_event(self,kind,*,role=None,**data):
        if not self.agent_trace_enabled:return None
        identifier=f'{self.run_id}:{len(self.events)}'
        self.add('diagnostic')
        self.events[-1]['agent_execution']=self.redact_agent({'id':identifier,'kind':kind,
            'run_id':self.run_id,'iteration':self.iteration,'iteration_id':f'{self.run_id}:{self.iteration}','stage':self.agent_stage,
            'phase':self.agent_phase,'role':role,'timestamp':self.events[-1]['timestamp'],**data})
        return identifier

    def safe_tool_arguments(self,name,arguments):
        """Only fixed browser contract enums, opaque IDs and numeric coordinates."""
        import re
        if name!='browser_action' or not isinstance(arguments,dict):
            return {'values':'REDACTED','argument_count':len(arguments) if isinstance(arguments,dict) else None}
        safe={}
        for key,value in arguments.items():
            if key in {'action','field'} and isinstance(value,str) and re.fullmatch('[a-z_]{1,40}',value):safe[key]=value
            elif key in {'reference','screenshot_id'} and isinstance(value,str) and re.fullmatch('[a-f0-9]{32}',value):safe[key]=value
            elif key in {'x','y','delta_y'} and type(value) in {int,float}:safe[key]=value
            else:safe[key if key in {'action','field','reference','screenshot_id','x','y','delta_y'} else 'unsupported_argument']='[redacted]'
        return safe

    def write_agent_artifacts(self,output_root,*,result):
        """Render only recorded observable events, not reconstructed reasoning."""
        if not self.agent_trace_enabled:return None
        output_root=Path(output_root);output_root.mkdir(parents=True,exist_ok=True)
        events=[e['agent_execution'] for e in self.events if 'agent_execution' in e]
        result=self.redact_agent(result)
        document={'schema_version':1,'run_id':self.run_id,'iteration':self.iteration,'iteration_id':f'{self.run_id}:{self.iteration}',
            'started_at':events[0]['timestamp'] if events else None,
            'recorded_through':events[-1]['timestamp'] if events else None,
            'final_status':result.get('status'),'events':events,'outcome':result,
            'model_diagnostics':self.redact_agent(self.model_diagnostics),
            'provider_boundaries':self.redact_agent(self.boundary_diagnostics),
            'missing_evidence_policy':'null/unavailable; no reconstructed reasoning; images transient, no raw DOM',
            'usage':result.get('session_usage_roles')}
        structured=output_root/f'{self.run_id}-agent-trace.json'
        report=output_root/f'{self.run_id}-agent-report.md'
        def cell(value):return str(value).replace('|','\\|').replace('\n',' ') if value is not None else 'unavailable'
        lines=[f'# Agent execution {self.run_id}',
            f'Iteration: {self.iteration}. Status: {cell(result.get("status"))}.',
            'Only observable calls, structured outputs and readbacks are recorded. Model reasoning is not captured.',
            '', '## Chronological tool-call timeline', '',
            '| UTC timestamp | Stage / role | Event / call | Safe arguments | Duration ms | Result / error | Evidence |',
            '| --- | --- | --- | --- | ---: | --- | --- |']
        for event in events:
            if event['kind'] not in {'tool_invocation','tool_result','browser_action','browser_action_started','controller_action_started','controller_action_result'}:continue
            detail=event.get('error') or event.get('result') or event.get('status')
            lines.append('| '+' | '.join(cell(v) for v in (event['timestamp'],event['stage']+' / '+str(event.get('role')),
                event['kind']+' / '+str(event.get('tool',''))+' / '+str(event.get('action',''))+' / '+str(event.get('field','')),
                json.dumps(event.get('arguments')) if event.get('arguments') is not None else None,event.get('duration_ms'),
                json.dumps(detail,sort_keys=True) if isinstance(detail,dict) else detail,event.get('evidence_refs',event['id'])))+' |')
        lines+=['','## Observer checks and graph routing','']
        for event in events:
            if event['kind'] in {'observer_verdict','deterministic_readback','graph_route'}:
                lines.append('- '+cell(event['timestamp'])+' '+cell(event['kind'])+': '+cell(json.dumps(event,sort_keys=True)))
        lines+=['','## Teacher assessment','']
        teachers=[e for e in events if e['kind']=='teacher_assessment']
        if not teachers:lines.append('Unavailable: no recorded structured Teacher assessment.')
        for event in teachers:lines.append('```json\n'+json.dumps(event,indent=2)+'\n```')
        lines+=['','## Candidate lineage and loading','']
        for event in events:
            if event['kind'] in {'skill_selected','skill_loaded','candidate_update'}:
                lines.append('- '+cell(json.dumps(event,sort_keys=True)))
        lines+=['','## Usage and final outcome','', '```json',json.dumps({
            'usage':document['usage'],'verified':result.get('verified'),
            'remaining_uncertainty':result.get('remaining_uncertainty'),
            'failure':result.get('failure'),'cleanup':result.get('cleanup')},indent=2),'```','']
        # Replace each file atomically; failure is surfaced, never reported as saved.
        for path,body in ((structured,json.dumps(document,indent=2)+'\n'),(report,'\n'.join(lines))):
            temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(body,encoding='utf-8');temporary.replace(path)
        return {'trace':structured.name,'report':report.name}

    def add(self,category,observation=None,result=Result.NOT_TESTED,error=None):
        if category not in {'initialize','navigate','observe','reason','entry','handoff','verify','close','error','tool','recover','deep_agent','teacher','outcome','diagnostic','skill'}:
            raise ValueError('Unknown action category')
        if error not in {None,'BUDGET','BROWSER','MODEL','CANCELLED','POLICY'}:
            raise ValueError('Unknown error category')
        result=Result(result)
        data=observation.model_dump() if observation is not None else None
        if data is not None:
            self.current_observation=data
        timestamp=datetime.now(timezone.utc).isoformat()
        if result==Result.AUTHENTICATED_AND_VERIFIED and data is not None:
            self.verification_snapshot={'run_id':self.run_id,'timestamp':timestamp,'result':str(result),
                'observation':data,'basis':['EXPECTED_HOST','DASHBOARD','TENANT_MATCH','AUTHENTICATED_READ_ONLY_ACCESS']}
        self.events.append(dict(run_id=self.run_id,iteration=self.iteration,strategy_version=self.strategy_version,
            action_category=category,observation=data,observation_kind='current' if data is not None else 'none',
            timestamp=timestamp,error_category=error,step_result=str(result),
            total_browser_actions=self.actions,model_calls=self.model_calls,codex_calls=self.codex_calls,
            openai_api_calls=self.openai_api_calls,reasoning_turns=self.reasoning_turns,
            reasoner_mode=self.reasoner_mode,elapsed_seconds=round(monotonic()-self.started,3),verification_result=str(result)))
        if self.agent_trace_enabled and data is not None:
            # Observation's existing strict schema contains fixed booleans only.
            self.events[-1]['agent_execution']=self.redact_agent({'id':f'{self.run_id}:{len(self.events)-1}',
                'kind':'browser_observation','run_id':self.run_id,'iteration':self.iteration,
                'iteration_id':f'{self.run_id}:{self.iteration}','stage':self.agent_stage,'phase':self.agent_phase,
                'timestamp':timestamp,'role':'doer','result':data,'verification':str(result)})

    def fact(self,kind):
        allowed={'LOGIN_RECOGNIZED','FIELD_ROLES_VALIDATED','SUBMITTED_ONCE','DESTINATION_VERIFIED','REINSPECTED','SKILL_READ','READ_ONLY_CHECK','STABLE_CONTROLS'}
        allowed.update({'HUMAN_CONFIRMED_FOR_POC','WHATSAPP_DESTINATION_OPENED','SETUP_FORM_DETECTED',
            'EXISTING_SESSION_VERIFIED','WHATSAPP_TAB_PROTECTED',
            'CAMPAIGN_NAME_VERIFIED','WABA_VERIFIED','BUSINESS_NUMBER_VERIFIED','EXISTING_TAG_VERIFIED',
            'SETUP_FIELDS_VERIFIED','NO_FORBIDDEN_TRANSITION_ATTEMPTED'})
        if kind not in allowed:
            raise ValueError('Unknown step fact')
        self.step_facts.append({'kind':kind,'event_index':max(0,len(self.events)-1)})

    def lifecycle(self,component,data):
        self.add('diagnostic')
        self.events[-1].update(diagnostic_stage=component,diagnostic=data)

    def tool(self,name,role,phase):
        names={'navigate_approved','inspect_page','interact_observed','submit_login','verify_dashboard',
            'read_approved_skill','DoerReport','TeacherProposal'}
        names.update({'inspect_whatsapp_setup','fill_setup_field','verify_whatsapp_setup','SetupReport'})
        names.update({'observe_page','inspect_control','scroll_to_control','click_observed_control','fill_text',
            'select_observed_option','verify_field','read_module_skill'})
        names.update({'browser_action','V2Turn','GoalFinding','V2Lesson'})
        if role not in self.role_usage or phase not in {'requested','started','returned','failed','denied'}:
            raise ValueError('Invalid tool event')
        self.add('tool')
        self.events[-1].update(tool_name=name if name in names else 'FORBIDDEN',tool_role=role,tool_phase=phase)

    def summary(self):
        return {'run_id':self.run_id,'strategy_version':self.strategy_version,'verification_snapshot':self.verification_snapshot,
            'final_outcome':self.final_outcome,'cleanup_result':self.cleanup_result,'cleanup_diagnostic':self.cleanup_diagnostic,'teacher_result':self.teacher_result,
            'step_facts':self.step_facts,'role_evidence':self.role_evidence,'model_diagnostics':self.model_diagnostics,
            'boundary_diagnostics':self.boundary_diagnostics,'model_output_limits':self.model_limits,'role_usage':self.role_usage,'token_usage':self.token_usage,'token_usage_available':self.token_usage_available,
            'actual_api_requests':None if self.agent_trace_enabled else self.openai_api_calls,
            **({'provider_dispatch_ready_requests':self.openai_api_calls} if self.agent_trace_enabled else {})}

    def write(self,directory:Path):
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/f'{self.run_id}.json'
        path.write_text(json.dumps(self.events,indent=2),encoding='utf-8')
        (directory/f'{self.run_id}-summary.json').write_text(json.dumps(self.summary(),indent=2),encoding='utf-8')
        return path
