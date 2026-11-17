"""One page policy: origin admission for reads, exact route/identity for writes."""
from urllib.parse import urlsplit
from modules.whatsapp.contract import SETUP_URL,SetupStop


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
        fragment_class='NONE' if not actual.fragment else 'SETUP' if actual.fragment=='setup' else 'UNEXPECTED',
        setup_identity_detected=isinstance(identity,dict) and identity.get('page_identity') is True,
        active_step='SETUP' if isinstance(identity,dict) and identity.get('setup_active') is True else
            identity.get('active_step','UNKNOWN') if isinstance(identity,dict) and identity.get('active_step') in {'AUDIENCE','CONTENT','SCHEDULE'} else 'UNKNOWN',
        observation_complete=isinstance(identity,dict) and identity.get('observation_complete') is True)
    route=diagnostic['path_match'] and diagnostic['query_match'] and diagnostic['fragment_class']!='UNEXPECTED'
    confirmed=route and verified and identity.get('form_recognized') is True
    return {'page_state':'SETUP_CONFIRMED' if confirmed else 'ALLOWED_ORIGIN',
        'diagnostic':diagnostic,'identity_verified':verified,'route_match':route}


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
