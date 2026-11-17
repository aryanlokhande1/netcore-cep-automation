"""Single production review lifecycle, shared unchanged by archived replay."""
from time import monotonic
from deep_agents.evidence import compile_evidence,canonical,digest
from deep_agents.review_contract import EvidenceError,ReviewDecision
from deep_agents.teacher import create_teacher
from deep_agents.diagnostics import teacher_failure

async def review_evidence(result,trace,model,budget,offline=False,history=None,adapter=None,persist_callback=None):
    if adapter is None:raise EvidenceError('REVIEW_ADAPTER_REQUIRED')
    result['teacher_proposal']=None;result['teacher_error']=None
    stage='EVIDENCE_COMPILATION';started=monotonic();proposal=None;decision=None
    try:
        package=compile_evidence(result,trace.events,adapter,history)
        result['teacher_evidence']={'schema_version':package.schema_version,'digest':digest(package.model_dump()),
            'serialized_bytes':len(canonical(package.model_dump()).encode()),'source_event_count':package.source_event_count,
            'offered_event_indices':sorted({i for c in package.evidence for i in c.events})}
        trace.review_package=package.model_dump()
        result['teacher_review_context']={'comparison_history':package.comparison,'previous_strategy':package.previous_strategy}
        stage='CONSTRUCTION'
        if hasattr(model,'bind_meter'):model.bind_meter(trace,budget,'teacher')
        teacher,_=create_teacher(model,trace,budget,offline=offline)
        stage='MODEL_EXECUTION'
        from langsmith import tracing_context
        with tracing_context(enabled=False):
            state=await teacher.ainvoke({'messages':[{'role':'user','content':canonical(package.model_dump())}]},
            {'configurable':{'thread_id':trace.run_id+'-teacher'},'recursion_limit':8})
        stage='PROPOSAL_VALIDATION'
        response=state.get('structured_response')
        if response is None:raise EvidenceError('MISSING_STRUCTURED_PROPOSAL')
        if offline:
            # Legacy fixture compatibility only; never used for actual replay acceptance.
            proposal=adapter.offline_proposal(response)
            citations=proposal.evidence_events
            if proposal.outcome!=package.outcome:raise EvidenceError('OUTCOME_MISMATCH')
        else:
            decision=ReviewDecision.model_validate(response)
            adapter.validate(decision,package)
            citations=decision.evidence_events
            result['teacher_decision']=decision.model_dump()
            proposal=adapter.accepted_proposal(decision)
        offered={i for card in package.evidence for i in card.events}
        if not set(citations)<=offered:raise EvidenceError('EVIDENCE_REFERENCE_NOT_OFFERED')
        result['teacher_validation_summary']={'required_outcome':package.outcome,'proposed_outcome':proposal.outcome,
            'event_count':package.source_event_count,'citation_count':len(citations),'out_of_range_citations':0,
            'canonical_citations_valid':True}
        stage='PERSISTENCE'
        if persist_callback:persist_callback(proposal,trace.events,result['verification'],trace.run_id)
        result['teacher_proposal']=proposal.model_dump()
        trace.teacher_result='ANALYZED'
        trace.add('teacher',result=result['verification'])
    except Exception as error:
        result['teacher_diagnostic']=teacher_failure(error,stage,trace)
        if isinstance(error,EvidenceError):result['teacher_diagnostic']['validation_code']=error.args[0]
        from pydantic import ValidationError
        if isinstance(error,ValidationError):
            fields=set(ReviewDecision.model_fields)|adapter.proposal_fields
            result['teacher_diagnostic']['validation_errors']=[{'type':item['type'],
                'location':[part if type(part) is int or part in fields else 'unknown_field' for part in item['loc']]}
                for item in error.errors(include_input=False,include_context=False,include_url=False)]
        if decision is not None:
            result['teacher_rejected_decision']=decision.model_dump()
            result['teacher_diagnostic']['decision_citation_count']=len(decision.evidence_events)
        result.pop('teacher_decision',None)
        trace.teacher_result='ANALYSIS_UNAVAILABLE';result['teacher_error']='TEACHER_ANALYSIS_UNAVAILABLE'
    result['teacher_status']=trace.teacher_result
    result['teacher_review_seconds']=round(monotonic()-started,3)
    result['teacher_boundary']=list(getattr(trace,'boundary_diagnostics',[]))
    return result
