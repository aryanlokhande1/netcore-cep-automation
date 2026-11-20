"""One page policy: origin admission for reads, exact route/identity for writes."""
from urllib.parse import urlsplit
from modules.whatsapp.contract import SETUP_URL,SetupStop


def setup_fragment_class(fragment):
    """The existing reviewed fragment contract, shared by navigation and tools."""
    return 'NONE' if not fragment else 'SETUP' if fragment=='setup' else 'UNEXPECTED'


def protected_route_status(url,*,expected_origin,expected_tenant=None,origin_permitted=False,enforce_tenant=True,
                           campaign_stage=None):
    """Current-route admission, separate from form and authentication proof.

    A canonical path/query may change within the fixed tenant. Only the existing
    Setup fragment is admitted. No URL, query value or tenant name is exported.
    """
    import hashlib
    try:
        actual=urlsplit(url)
        actual.port  # Reject malformed ports without exporting the URL.
        expected_tenant=expected_tenant if expected_tenant is not None else urlsplit(SETUP_URL).path.strip('/').split('/')[0]
        tenant=actual.path.strip('/').split('/')[0]
        fragment=setup_fragment_class(actual.fragment)
        if campaign_stage in {'audience','content','schedule'} and campaign_step_route(url, campaign_stage):
            fragment=campaign_stage.upper()
        predicates={
            'url_credentials_absent':actual.username is None and actual.password is None,
            'approved_origin':f'{actual.scheme}://{actual.netloc}'==expected_origin and origin_permitted,
            'authorized_tenant_route':not enforce_tenant or bool(expected_tenant) and tenant==expected_tenant,
            'supported_task_fragment':fragment in {'NONE','SETUP'} or
                                      campaign_stage is not None and fragment==campaign_stage.upper(),
        }
        route={'page_path_sha256':hashlib.sha256(actual.path.encode()).hexdigest(),
            'tenant_path_sha256':hashlib.sha256(tenant.encode()).hexdigest(),
            'expected_tenant_sha256':hashlib.sha256(expected_tenant.encode()).hexdigest(),
            'fragment_class':fragment,'query_present':bool(actual.query)}
    except (ValueError,TypeError,AttributeError):
        return {'code':'PROTECTED_ROUTE_SYNTAX_INVALID','predicates':{},
            'failed_predicates':['url_syntax'],'route':None}
    failures=[name for name,value in predicates.items() if not value]
    codes={'url_credentials_absent':'PROTECTED_URL_CREDENTIALS_REJECTED',
        'approved_origin':'PROTECTED_ORIGIN_POLICY_REJECTED',
        'authorized_tenant_route':'PROTECTED_TENANT_ROUTE_MISMATCH',
        'supported_task_fragment':'PROTECTED_TASK_FRAGMENT_UNSUPPORTED'}
    return {'code':codes[failures[0]] if failures else None,'predicates':predicates,
        'failed_predicates':failures,'route':route}


def cdp_frame_url(frame):
    """CDP may expose the fragment separately; missing URL remains missing."""
    url=frame.get('url')
    if not isinstance(url,str) or not url:return None
    fragment=frame.get('urlFragment')
    return url+fragment if isinstance(fragment,str) and fragment and '#' not in url else url


def navigation_url_difference(left,right):
    """Component names only, never URL values or query contents."""
    if not isinstance(left,str) or not left or not isinstance(right,str) or not right:return ['missing_observation']
    a,b=urlsplit(left),urlsplit(right)
    def parts(u):return (u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80),u.path,u.query,u.fragment)
    return [name for name,x,y in zip(('scheme','host','effective_port','path','query','fragment'),parts(a),parts(b)) if x!=y]


def navigation_route_category(url,expected=SETUP_URL):
    """Fixed route facts only; never persist an untrusted URL/query."""
    if url=='about:blank':return 'ABOUT_BLANK'
    try:
        state=classify_setup_page(url,expected)
        if state['route_match']:return 'INTENDED_WHATSAPP_ROUTE'
        actual,target=urlsplit(url),urlsplit(expected)
        dashboard=target.path.split('/campaign/',1)[0]
        if '/campaign/' in target.path and actual.path in {dashboard,dashboard+'/'}:return 'TENANT_DASHBOARD'
        return 'OTHER_APPROVED_ORIGIN_ROUTE'
    except DestinationRejected:return 'UNAPPROVED_DESTINATION'


class DestinationRejected(SetupStop):
    def __init__(self,predicate,diagnostic=None):
        super().__init__('UNEXPECTED_SETUP_DESTINATION');self.predicate=predicate
        self.diagnostic=diagnostic or {}


def classify_setup_page(url,expected=SETUP_URL,identity=None):
    """Unknown paths/steps on the approved origin are readable, never writable.

    Returns fixed classifications only. DOM evidence is supplied by the shared
    trusted observer, not by model arguments. Origin failures remain terminal.
    """
    diagnostic={'origin_match':False,'path_match':False,'query_match':False,
        'fragment_class':'UNKNOWN','setup_identity_detected':False,'active_step':'UNKNOWN','observation_complete':False}
    def reject(predicate):raise DestinationRejected(predicate,diagnostic)
    if not isinstance(url,str) or url.strip()!=url or any(ord(c)<33 for c in url) or '\\' in url:
        reject('URL_SYNTAX')
    try:
        actual=urlsplit(url);target=urlsplit(expected)
        if actual.username is not None or actual.password is not None:reject('URL_CREDENTIALS')
        if actual.scheme!=target.scheme:reject('SCHEME')
        if actual.hostname!=target.hostname:reject('HOST')
        default=lambda scheme:443 if scheme=='https' else 80 if scheme=='http' else None
        if (actual.port or default(actual.scheme))!=(target.port or default(target.scheme)) or actual.port==0:reject('PORT')
    except DestinationRejected:raise
    except (ValueError,TypeError):reject('URL_SYNTAX')
    verified=isinstance(identity,dict) and all(identity.get(k) is True for k in ('page_identity','setup_active','observation_complete'))
    diagnostic.update(origin_match=True,path_match=actual.path==target.path,query_match=actual.query==target.query,
        fragment_class=setup_fragment_class(actual.fragment),
        setup_identity_detected=isinstance(identity,dict) and identity.get('page_identity') is True,
        active_step='SETUP' if isinstance(identity,dict) and identity.get('setup_active') is True else
            identity.get('active_step','UNKNOWN') if isinstance(identity,dict) and identity.get('active_step') in {'AUDIENCE','CONTENT','SCHEDULE'} else 'UNKNOWN',
        observation_complete=isinstance(identity,dict) and identity.get('observation_complete') is True)
    route=diagnostic['path_match'] and diagnostic['query_match'] and diagnostic['fragment_class']!='UNEXPECTED'
    confirmed=route and verified and identity.get('form_recognized') is True
    return {'page_state':'SETUP_CONFIRMED' if confirmed else 'ALLOWED_ORIGIN',
        'diagnostic':diagnostic,'identity_verified':verified,'route_match':route}


def campaign_step_route(url, stage, expected=SETUP_URL):
    if stage not in {'setup', 'audience', 'content', 'schedule'}:
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.fragment == stage and
                classify_setup_page(parsed._replace(fragment='').geturl(), expected)['route_match'])
    except (DestinationRejected, ValueError, TypeError):
        return False


def validate_setup_destination(url,expected=SETUP_URL,identity=None,*,allow_identity_probe=False):
    """Strict navigation/action policy; never a prerequisite for read admission."""
    state=classify_setup_page(url,expected,identity);diagnostic=state['diagnostic']
    for predicate,key in [('PATH','path_match'),('QUERY','query_match')]:
        if not diagnostic[key]:raise DestinationRejected(predicate,diagnostic)
    if diagnostic['fragment_class']=='UNEXPECTED':raise DestinationRejected('FRAGMENT',diagnostic)
    if identity is not None and not state['identity_verified']:raise DestinationRejected('SETUP_IDENTITY',diagnostic)
    pending=diagnostic['fragment_class']=='SETUP' and not state['identity_verified']
    if pending and not allow_identity_probe:raise DestinationRejected('SETUP_IDENTITY_REQUIRED',diagnostic)
    return {'fragment':diagnostic['fragment_class'],'identity_pending':pending,'identity_verified':state['identity_verified']}
