"""Categorical failures only: never serialize exception messages or validation inputs."""
def teacher_failure(error,stage,trace):
    from openai import AuthenticationError,PermissionDeniedError,NotFoundError,RateLimitError,APIConnectionError,APIError
    from pydantic import ValidationError
    categories={AuthenticationError:'API_AUTHENTICATION',PermissionDeniedError:'API_ACCESS',NotFoundError:'API_MODEL_UNAVAILABLE',
        RateLimitError:'API_RATE_OR_QUOTA',APIConnectionError:'API_CONNECTION',APIError:'API_FAILURE',ValidationError:'STRUCTURED_OUTPUT_VALIDATION'}
    category=next((v for k,v in categories.items() if isinstance(error,k)),None)
    if isinstance(error,RuntimeError) and error.args==('MODEL_BUDGET_EXHAUSTED',):
        category='REQUEST_BUDGET_EXHAUSTED'
    if type(error).__name__ in {'StructuredOutputValidationError','MultipleStructuredOutputsError'}:
        category='STRUCTURED_OUTPUT_VALIDATION'
    if isinstance(error,TimeoutError):category='TIMEOUT'
    if stage=='PROPOSAL_VALIDATION' and category is None:category='PROPOSAL_VALIDATION'
    if stage=='PERSISTENCE' and category is None:category='PERSISTENCE'
    calls=[d for d in trace.model_diagnostics if d['role']=='teacher']
    if calls and calls[-1]['status']=='STARTED':calls[-1]['status']='FAILED'
    # Provider terminal status takes precedence over downstream parser symptoms.
    if calls and (calls[-1].get('finish_status')=='length' or calls[-1].get('incomplete_reason')=='max_output_tokens'):
        category='OUTPUT_LIMIT_INCOMPLETE'
    if isinstance(error,RuntimeError) and error.args==('OUTPUT_LIMIT_INCOMPLETE',):category='OUTPUT_LIMIT_INCOMPLETE'
    if isinstance(error,RuntimeError) and error.args==('TOTAL_TOKEN_BUDGET_EXHAUSTED',):category='TOTAL_TOKEN_BUDGET_EXHAUSTED'
    return {'stage':stage,'category':category or 'EXECUTION_FAILURE',
        'finish_status':calls[-1].get('finish_status','NOT_AVAILABLE') if calls else 'NOT_AVAILABLE',
        'model_request_started':bool(calls),'request_count':trace.role_usage['teacher']['requests']}
