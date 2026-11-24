"""Validated Page 1 inputs and fixed authority shared by both browser paths."""
from dataclasses import dataclass
from typing import Literal
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, model_validator
from app.config import Config, ConfigurationError

class Page1Values(BaseModel):
    """Reviewed value allowlist; configuration cannot expand task authorization."""
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    campaign_name:Literal['agent_test']
    waba:Literal['1730519403808933']
    business_number:Literal['Bajaj Markets (+91 8422973254)']
    tags:Literal['test']


class Page1Task(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    workflow:Literal['whatsapp_campaign_page1']
    tenant:Literal['bajajfinservmarket']
    entry_url:str
    values:Page1Values

    @model_validator(mode='after')
    def approved_destination(self):
        u=urlsplit(self.entry_url)
        if (self.entry_url.strip()!=self.entry_url or any(ord(c)<33 for c in self.entry_url)
            or '\\' in self.entry_url or u.scheme!='https' or u.netloc!=Config.expected_host
            or '?' in self.entry_url or '#' in self.entry_url
            or u.path!=f'/{self.tenant}/admin/index.php/campaign/whatsapp_campaign/create/whatsapp/regular'):
            raise ValueError('Unapproved WhatsApp entry point')
        return self


def load_page1_task(path=None):
    """Read only public module inputs. Never load .env or fall back on bad config."""
    try:
        return Page1Task.model_validate_json(Path(__file__).with_name('page1.json').read_text(encoding='utf-8')
            if path is None else Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError,TypeError):
        raise ConfigurationError('Invalid WhatsApp Page 1 configuration') from None


PAGE1_TASK=load_page1_task()
# Compatibility aliases: existing executors consume exactly the validated inputs.
SETUP_URL=PAGE1_TASK.entry_url
VALUES=PAGE1_TASK.values.model_dump()
LABELS={'campaign_name':'Campaign name','waba':'WABA ID','business_number':'Business number','tags':'Add tags'}
FIELD_FACTS={'campaign_name':'CAMPAIGN_NAME_VERIFIED','waba':'WABA_VERIFIED','business_number':'BUSINESS_NUMBER_VERIFIED','tags':'EXISTING_TAG_VERIFIED'}
ORDER=tuple(VALUES)
FORBIDDEN_ACTIONS=('Save','Finish Later','Next Step','Audience modification','Schedule',
    'Publish','Activate','Send','Campaign launch','Create tag')
FIELD_VERIFICATION={
    'campaign_name':'Exact current input value after fill, with current owned control identity.',
    'waba':'Exact selected WABA identifier; displayed label or search text alone is insufficient.',
    'business_number':'Exact selected business label and phone, after independently verified WABA.',
    'tags':'Exact existing selected tag; no additional tags, creation, or search-only evidence.',
}


def page1_contract():
    """Offline review surface; contains no authentication evidence or live claims."""
    from learning.skill_storage.registry import module_baseline
    snapshot=module_baseline('whatsapp')
    return {'workflow':PAGE1_TASK.workflow,'entry_url':SETUP_URL,
        'fields':{field:{'label':LABELS[field],'value':VALUES[field],'fact':FIELD_FACTS[field],
            'verification':FIELD_VERIFICATION[field],
            'actions':[name for name,c in CAPABILITIES.items() if c.exposed and field in c.fields]}
            for field in ORDER},
        'forbidden_actions':list(FORBIDDEN_ACTIONS),
        'completion_status':'SETUP_FILLED_UNSAVED','failure_status':'BLOCKED',
        'progress':'Only independently verified FIELD_FACTS establish completed fields.',
        'failure':'Retain failed predicate, phase, verified fields, retry safety and sanitized capture status before cleanup.',
        'authentication':'Independent certification or separately authorized operator-confirmed handoff; redirect is insufficient.',
        'skill':{'path':'skills/whatsapp/SKILL.md','content_sha256':snapshot.digest,
            'approved':snapshot.approved,'status':snapshot.metadata['status']},
        'live_certified':False,'teacher_mutation':'NOT_IMPLEMENTED_IN_PHASE_A'}

class SetupReport(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['SETUP_FILLED_UNSAVED','BLOCKED']
    reason:Literal['fields_verified','unexpected_page','unsupported_control','option_unavailable','tool_error','budget_exhausted']

@dataclass(frozen=True)
class Phase1Limits:
    login_requests:int=6
    setup_requests:int=8
    teacher_requests:int=2
    total_requests:int=16
    checkpoint_seconds:int=90
    setup_seconds:int=120
    hold_seconds:int=120
    def validate(self):
        if not 2<=self.login_requests<=6 or not 2<=self.setup_requests<=8 or not 1<=self.teacher_requests<=2:
            raise ConfigurationError('Phase 1 requires login 2-6, Setup 2-8 and Teacher 1-2 requests')
        if self.total_requests!=self.login_requests+self.setup_requests+self.teacher_requests or self.total_requests>16:
            raise ConfigurationError('Total Phase 1 request budget must equal its explicit stage budgets, maximum 16')
        if not 1<=self.checkpoint_seconds<=120 or not 1<=self.setup_seconds<=180 or not 1<=self.hold_seconds<=300:
            raise ConfigurationError('Invalid bounded Phase 1 checkpoint, Setup or inspection window')
        return self

class SetupStop(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


@dataclass(frozen=True)
class Page1Limits(Phase1Limits):
    """One Login/Setup Doer run; no reserved or executable Teacher requests."""
    setup_requests:int=18
    teacher_requests:int=0
    total_requests:int=24

    def validate(self):
        if (not 2<=self.login_requests<=6 or not 2<=self.setup_requests<=20 or self.teacher_requests!=0
            or self.total_requests!=self.login_requests+self.setup_requests or self.total_requests>24):
            raise ConfigurationError('Page 1 Doer-only requires login 2-6, Setup 2-20, zero Teacher and total <=24')
        if not 1<=self.checkpoint_seconds<=120 or not 1<=self.setup_seconds<=180 or not 1<=self.hold_seconds<=300:
            raise ConfigurationError('Invalid bounded Page 1 checkpoint, Setup or inspection window')
        return self

# Single action boundary for both field-named and opaque-reference tools.
from types import MappingProxyType
from functools import lru_cache
from pydantic import StrictBool,create_model,model_validator

@dataclass(frozen=True)
class Capability:
    fields:tuple[str,...]
    verification:str
    exposed:bool=True

_REMAINING=ORDER[1:]
CAPABILITIES=MappingProxyType({
    'observe_page':Capability((), 'protected current page observation'),
    'inspect_field':Capability(ORDER, 'current scoped field evidence, including unresolved state'),
    'scroll_to_field':Capability(ORDER, 'same observed target retained after scrolling'),
    'open_dropdown':Capability(_REMAINING, 'native/selected no-op or owned expanded popup'),
    'inspect_options':Capability(_REMAINING, 'owned options and exact-match counts; never selection'),
    'select_existing_option':Capability(_REMAINING, 'independent exact selected state'),
    'fill_approved_field':Capability(('campaign_name',), 'independent exact input readback'),
    'verify_field':Capability(ORDER, 'exact state or explicit FIELD_NOT_VERIFIED'),
    'capture_failure':Capability((), 'redacted capture status or explicit privacy/transport skip'),
    'search_existing_option':Capability(_REMAINING, 'search input readback only, not selected state',False),
})
FIELD_ALIASES=MappingProxyType({'campaign name':'campaign_name','campaign title':'campaign_name',
    'waba':'waba','waba id':'waba','whatsapp business account':'waba','whatsapp business account id':'waba',
    'business number':'business_number','add tags':'tags','tags':'tags'})

class CapabilityError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

class _CapabilityArgs(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    @model_validator(mode='after')
    def scope_flags(self):
        if getattr(self,'initialize_dependency',False) and self.field!='waba':
            raise ValueError('Dependency initialization supports only WABA')
        return self

def capability_schema(action):
    if not isinstance(action,str) or action not in CAPABILITIES:raise CapabilityError('TOOL_FORBIDDEN')
    return _capability_schema(action)

@lru_cache(None)
def _capability_schema(action):
    if action not in CAPABILITIES:raise CapabilityError('TOOL_FORBIDDEN')
    c=CAPABILITIES[action];fields={}
    if c.fields:fields['field']=(Literal[c.fields],...)
    if action=='select_existing_option':fields['initialize_dependency']=(StrictBool,False)
    return create_model('Args_'+action,__base__=_CapabilityArgs,**fields)

def validate_capability(action,**arguments):
    from pydantic import ValidationError
    schema=capability_schema(action)
    try:schema.model_validate(arguments)
    except ValidationError:raise CapabilityError('INVALID_TOOL_ARGUMENTS') from None
    return CAPABILITIES[action]

# Opaque references cannot carry field enums; validate their resolved field using
# the canonical action registry. These aliases never expand canonical authority.
REFERENCE_TOOLS=MappingProxyType({
    'observe_page':('observe_page',()),
    'inspect_control':('inspect_field',('reference',)),
    'scroll_to_control':('scroll_to_field',('reference',)),
    'click_observed_control':('open_dropdown',('reference',)),
    'fill_text':('fill_approved_field',('reference',)),
    'select_observed_option':('select_existing_option',('reference','option_reference')),
    'verify_field':('verify_field',('reference',)),
})

@lru_cache(None)
def reference_schema(tool):
    from pydantic import StrictStr,Field
    if tool not in REFERENCE_TOOLS:raise CapabilityError('TOOL_FORBIDDEN')
    return create_model('ReferenceArgs_'+tool,__base__=_CapabilityArgs,
        **{name:(StrictStr,Field(min_length=1,max_length=160)) for name in REFERENCE_TOOLS[tool][1]})

def resolved_reference_action(tool,field,role='control'):
    if tool not in REFERENCE_TOOLS:raise CapabilityError('TOOL_FORBIDDEN')
    action='search_existing_option' if tool=='fill_text' and role=='search' else REFERENCE_TOOLS[tool][0]
    validate_capability(action,**({'field':field} if CAPABILITIES[action].fields else {}))
    return action


# V2 authority; legacy Page 1 types above remain for historical imports only.
from datetime import datetime, timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pydantic import Field, StrictBool, StrictStr
import hashlib
import json

STAGES=('setup','audience','content','schedule','preview')
UTM_FIELDS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')
GA_SETUP_FIELDS = ('campaign_name', 'ga_tracking', *UTM_FIELDS)
STAGE_FIELDS={
    'setup':('campaign_name','waba','business_number','tag','ga_tracking'),
    'audience':('audience_type','include_audience','exclude_audience'),
    'content':('template',),
    'schedule':('send_type','schedule_datetime','timezone','frequency_cap_enabled','contact_limit_enabled'),
    'preview':(),
}
PAGE1_SETUP_FIELDS = ('campaign_name', 'waba', 'business_number', 'tag')
NEVER_ACTIONS=('publish','activate','send','send_test','save_and_publish')
@dataclass(frozen=True)
class V2BrowserCapability:
    required:tuple=()
    allowed:tuple=()
    targets:tuple|None=None
    read_only:bool=False


# Authoritative V2 registry. Tool action enums, JSON argument constraints and
# dispatch target validation are all derived from these entries.
V2_BROWSER_CONTRACT=MappingProxyType({
    'observe':V2BrowserCapability(read_only=True),
    'dom':V2BrowserCapability(read_only=True),
    'screenshot':V2BrowserCapability(read_only=True),
    'wait':V2BrowserCapability(read_only=True),
    'inspect':V2BrowserCapability(('reference',),('reference',),read_only=True),
    'click':V2BrowserCapability(('reference',),('reference','field','screenshot_id','x','y'),
        (('text','control'),('combobox','control'),('combobox','search'),('native_select','control'))),
    'type':V2BrowserCapability(('reference','field'),('reference','field'),
        (('text','control'),('checkbox','control'),('datetime_split','control'))),
    'scroll':V2BrowserCapability(('reference',),('reference','delta_y'),read_only=True),
    'options':V2BrowserCapability(('reference',),('reference','field'),
        (('native_select','control'),('combobox','control')),read_only=True),
    'search':V2BrowserCapability(('reference','field'),('reference','field'),(('combobox','search'),('combobox','control'))),
    'select':V2BrowserCapability(('reference','field'),('reference','field'),
        (('native_select','control'),('combobox','option'),('radio','control'))),
    'verify':V2BrowserCapability(('reference','field'),('reference','field'),
        tuple((kind,'control') for kind in ('text','checkbox','native_select','combobox','selected_text','datetime_split','radio')),read_only=True),
    'navigate':V2BrowserCapability(('reference',),('reference',),read_only=True),
})
V2_CAPABILITIES=MappingProxyType({action:spec.targets for action,spec in V2_BROWSER_CONTRACT.items() if spec.targets is not None})
V2_ACTION_ARGUMENTS=MappingProxyType({action:(spec.required,spec.allowed) for action,spec in V2_BROWSER_CONTRACT.items()})
_V2_ACTION_NAME=Literal.__getitem__(tuple(action for action in V2_BROWSER_CONTRACT if action!='navigate'))
_V2_DISCOVERY_ACTION_NAME=Literal.__getitem__(tuple(action for action,spec in V2_BROWSER_CONTRACT.items() if spec.read_only))
_V2_FIELD_NAME=Literal.__getitem__(tuple(dict.fromkeys((*UTM_FIELDS, *(field for fields in STAGE_FIELDS.values() for field in fields)))))
_V2_SETUP_FIELD_NAME=Literal.__getitem__(STAGE_FIELDS['setup'])


def _v2_action_json_schema(schema):
    actions=schema['properties']['action'].get('enum',[])
    branches=[]
    for action in actions:
        required,allowed=V2_ACTION_ARGUMENTS[action]
        branch={'properties':{'action':{'const':action}}}
        if required:branch['required']=list(required)
        for key in required:
            branch['properties'][key]={'type':'string','minLength':1,'maxLength':160}
        # Nullable defaults are allowed by transport, but cannot carry an
        # unrelated non-null argument. The Python validator applies the same rule.
        for key in set(V2Action.model_fields)-{'action'}-set(allowed):
            branch['properties'][key]={'type':'null'}
        if action=='click':
            branch['oneOf']=[
                {'properties':{key:{'type':'null'} for key in ('screenshot_id','x','y')}},
                {'required':['screenshot_id','x','y'],'properties':{
                    'screenshot_id':{'type':'string','minLength':1},'x':{'type':'number'},'y':{'type':'number'}}},
            ]
        if action=='scroll':branch['properties']['delta_y']={'type':['number','null'],'minimum':-1000,'maximum':1000}
        branches.append(branch)
    schema['oneOf']=branches

def validate_v2_capability(action,kind,part):
    if action in V2_CAPABILITIES and (kind,part) not in V2_CAPABILITIES[action]:
        raise V2Stop('UNSUPPORTED_FIELD_ACTION',owner='authorization')

class V2Stop(RuntimeError):
    def __init__(self,code,*,owner='controller',unsafe=False):
        self.code,self.owner,self.unsafe=code,owner,unsafe
        super().__init__(code)

class V2Payload(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    campaign_name:StrictStr
    waba:StrictStr
    business_number:StrictStr
    tag:StrictStr
    ga_tracking:StrictBool
    utm_source:StrictStr|None=None
    utm_medium:StrictStr|None=None
    utm_campaign:StrictStr|None=None
    utm_content:StrictStr|None=None
    utm_term:StrictStr|None=None
    audience_type:Literal['Lists/Segments']
    include_audience:StrictStr | tuple[StrictStr, ...]
    exclude_audience:StrictStr | tuple[StrictStr, ...]
    template:StrictStr
    send_type:Literal['Send Later']
    schedule_datetime:StrictStr|None
    timezone:StrictStr
    frequency_cap_enabled:StrictBool
    contact_limit_enabled:StrictBool
    final_action:Literal['SAVE_AND_PREVIEW']
    publish_allowed:Literal[False]
    send_test_allowed:Literal[False]

    @model_validator(mode='after')
    def bounded_values(self):
        for field, value in self.model_dump().items():
            if isinstance(value,str) and ((not value.strip() and not (field=='utm_content' and value==''))
                                           or len(value)>256 or any(ord(c)<32 for c in value)):
                raise ValueError('Invalid task value')
        utm = [getattr(self, field) for field in UTM_FIELDS]
        if any(value is not None for value in utm) and (not self.ga_tracking or any(value is None for value in utm)):
            raise ValueError('UTM fields require enabled tracking and all five explicit values')
        for field in ('include_audience', 'exclude_audience'):
            values = self.audience_values(field)
            if (field == 'include_audience' and not values or len(values) > 15 or len(set(values)) != len(values)
                    or any(not v.strip() or len(v) > 256 or any(ord(c) < 32 for c in v) for v in values)):
                raise ValueError(f'Invalid {field}')
        try:ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:raise V2Stop('TIMEZONE_DATA_UNAVAILABLE',owner='environment') from None
        return self

    def audience_values(self, field):
        if field not in {'include_audience', 'exclude_audience'}:
            raise ValueError('Unsupported Audience selection field')
        value = getattr(self, field)
        return (value,) if isinstance(value, str) else value

    def check_schedule(self):
        if self.schedule_datetime is None:raise V2Stop('SCHEDULE_DATETIME_REQUIRED',owner='authorization')
        try:
            value=datetime.fromisoformat(self.schedule_datetime)
            zone=ZoneInfo(self.timezone)
            if value.tzinfo is None:value=value.replace(tzinfo=zone)
            local=value.astimezone(zone)
            if local.replace(fold=0).utcoffset()!=local.replace(fold=1).utcoffset():raise ValueError()
            if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=local.replace(tzinfo=None):raise ValueError()
            if value.astimezone(timezone.utc)<=datetime.now(timezone.utc):raise ValueError()
        except ZoneInfoNotFoundError:raise V2Stop('TIMEZONE_DATA_UNAVAILABLE',owner='environment') from None
        except (ValueError,TypeError):raise V2Stop('SCHEDULE_DATETIME_INVALID_OR_EXPIRED',owner='authorization') from None

    def scheduled_local(self):
        self.check_schedule()
        value=datetime.fromisoformat(self.schedule_datetime)
        zone=ZoneInfo(self.timezone)
        return (value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone))

def load_v2_payload(path,*,approved_digest,model=V2Payload):
    raw=Path(path).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=approved_digest:raise V2Stop('PAYLOAD_APPROVAL_DIGEST_MISMATCH',owner='authorization')
    try:return model.model_validate_json(raw),digest
    except ValueError:raise V2Stop('INVALID_V2_PAYLOAD',owner='authorization') from None


def ga_setup_fields(payload):
    if payload.ga_tracking and any(getattr(payload, field) is None for field in UTM_FIELDS):
        raise V2Stop('GA_TEST_REQUIRES_ALL_FIVE_UTM_VALUES', owner='authorization')
    return GA_SETUP_FIELDS if payload.ga_tracking else GA_SETUP_FIELDS[:2]


def ga_strategy_scope(payload):
    """A fresh name changes the run approval digest, but not this experiment's scope."""
    scope = {'origin': SETUP_URL, 'actions': 'GA_SETUP_ONLY_V1',
             'tracking': {field: getattr(payload, field) for field in ga_setup_fields(payload)[1:]}}
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()

class FieldBinding(BaseModel):
    """Selectors are separately reviewed evidence, never model arguments."""
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    selector:StrictStr
    kind:Literal['text','checkbox','native_select','combobox','selected_text','datetime_split','radio']
    selected_selector:StrictStr|None=None
    option_selector:StrictStr|None=None
    search_selector:StrictStr|None=None
    date_selector:StrictStr|None=None
    time_selector:StrictStr|None=None
    date_format:StrictStr='%Y-%m-%d'
    time_format:StrictStr='%H:%M'
    datetime_format:StrictStr='%Y-%m-%dT%H:%M'

class ReviewedUI(BaseModel):
    """Incremental evidence. Missing evidence blocks its action, not observation."""
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    reviewed:StrictBool=False
    evidence_reference:StrictStr|None=None
    origin:StrictStr=f'{urlsplit(SETUP_URL).scheme}://{urlsplit(SETUP_URL).netloc}'
    tenant:StrictStr|None=None
    stage_selectors:dict[str,StrictStr]=Field(default_factory=dict)
    fields:dict[str,FieldBinding]=Field(default_factory=dict)
    next_selectors:dict[str,StrictStr]=Field(default_factory=dict)
    preview_selector:StrictStr|None=None
    visual_scope_selector:StrictStr|None=None
    content_preview_selector:StrictStr|None=None
    record_path:StrictStr|None=None
    records_key:StrictStr|None=None
    authenticated_key:StrictStr|None=None
    tenant_key:StrictStr|None=None
    record_id_key:StrictStr|None=None
    record_name_key:StrictStr|None=None
    record_status_key:StrictStr|None=None
    draft_status:StrictStr|None=None
    record_fields:dict[str,StrictStr]=Field(default_factory=dict)

    @model_validator(mode='after')
    def scope(self):
        target=urlsplit(SETUP_URL)
        path=urlsplit(self.record_path or '')
        required=set().union(*map(set,STAGE_FIELDS.values()), UTM_FIELDS)
        if (self.origin!=f'{target.scheme}://{target.netloc}' or (self.tenant is not None and self.tenant!=PAGE1_TASK.tenant)
            or not set(self.stage_selectors)<=set(STAGES) or not set(self.fields)<=required
            or not set(self.record_fields)<=required or not set(self.next_selectors)<=set(STAGES[:3])
            or (self.record_path is not None and (not self.record_path.startswith('/') or self.record_path.startswith('//')
            or path.scheme or path.netloc or path.query or path.fragment or '\\' in self.record_path))):
            raise ValueError('Incomplete or out-of-scope reviewed UI evidence')
        if self.reviewed and not (self.evidence_reference and self.evidence_reference.strip()):raise ValueError('Reviewed evidence needs actual provenance')
        for key,binding in self.fields.items():
            if key in {'ga_tracking','frequency_cap_enabled','contact_limit_enabled'} and binding.kind!='checkbox':raise ValueError('Boolean field requires checkbox evidence')
            if key in ('campaign_name', *UTM_FIELDS) and binding.kind!='text':raise ValueError('Text field requires text control')
            if key=='schedule_datetime' and binding.kind not in {'text','datetime_split'}:raise ValueError('Schedule requires reviewed date/time inputs')
            if binding.kind=='datetime_split' and (key!='schedule_datetime' or not binding.date_selector or not binding.time_selector):raise ValueError('Incomplete split date/time evidence')
            if key in {'waba','business_number','tag'} and binding.kind not in {'native_select','combobox','selected_text'}:raise ValueError('Dropdown field requires selection evidence')
        return self

    def require_record(self,fields=()):
        needed=('tenant','record_path','records_key','authenticated_key','tenant_key','record_id_key',
            'record_name_key','record_status_key','draft_status')
        if not self.reviewed or any(not getattr(self,key) for key in needed):
            raise V2Stop('REVIEWED_CAMPAIGN_RECORD_EVIDENCE_REQUIRED',owner='authorization')
        if not set(fields)<=set(self.record_fields):
            raise V2Stop('SAVED_FIELD_RECORD_MAPPING_REQUIRED',owner='authorization')

    def require_persistence(self,stage):
        self.require_record(STAGE_FIELDS[stage])
        if stage not in self.stage_selectors or not set(STAGE_FIELDS[stage])<=set(self.fields):
            raise V2Stop('CURRENT_STAGE_FIELD_EVIDENCE_REQUIRED',owner='recognition')
        control=self.preview_selector if stage=='schedule' else self.next_selectors.get(stage)
        if not control:raise V2Stop('CURRENT_PERSISTENCE_CONTROL_REQUIRED',owner='recognition')

class V2Authorization(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)
    live:bool=False
    inference:bool=False
    edits:bool=False
    manual_form_test:StrictBool=False
    persistence:bool=False
    vision:bool=False
    learning:bool=False
    fresh_iterations:bool=False
    candidate_evaluation:bool=False

    @model_validator(mode='after')
    def bounded_manual_form_test(self):
        if self.manual_form_test and not all((self.live,self.inference,self.edits)):
            raise ValueError('Manual form test requires live, inference and edit authorization')
        if self.manual_form_test and self.persistence:
            raise ValueError('Manual form test cannot authorize persistence')
        return self

    def require_run(self,iterations):
        if not all((self.live,self.inference)):raise V2Stop('EXPLICIT_V2_AUTHORIZATION_REQUIRED',owner='authorization')
        if not 1<=iterations<=5:raise V2Stop('ITERATION_LIMIT')
        if iterations>1 and not all((self.learning,self.fresh_iterations)):raise V2Stop('FRESH_ITERATIONS_NOT_AUTHORIZED',owner='authorization')

class V2Action(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,json_schema_extra=_v2_action_json_schema)
    action:_V2_ACTION_NAME
    reference:StrictStr|None=None
    field:_V2_FIELD_NAME|None=None
    screenshot_id:StrictStr|None=None
    x:float|None=None
    y:float|None=None
    delta_y:float|None=None

    @model_validator(mode='after')
    def combinations(self):
        required,allowed=V2_ACTION_ARGUMENTS[self.action]
        supplied={k for k in type(self).model_fields if k!='action' and getattr(self,k) is not None}
        if not set(required)<=supplied or not supplied<=set(allowed):
            raise ValueError('Unsupported action arguments')
        if self.reference is not None and (not self.reference.strip() or len(self.reference)>160):
            raise ValueError('Invalid observed reference')
        if self.delta_y is not None:
            import math
            if not math.isfinite(self.delta_y) or abs(self.delta_y)>1000:raise ValueError('Scroll limit exceeded')
        if self.action in {'observe','dom','screenshot'} and any(v is not None for v in
            (self.reference,self.field,self.screenshot_id,self.x,self.y,self.delta_y)):
            raise ValueError('This action accepts no target arguments')
        coordinates=self.x is not None or self.y is not None or self.screenshot_id is not None
        if coordinates and (self.action!='click' or self.reference is None or self.x is None or self.y is None or not self.screenshot_id):raise ValueError('Visual click requires observed target, screenshot and coordinates')
        if coordinates:
            import math
            if not all(math.isfinite(v) for v in (self.x,self.y)):raise ValueError('Coordinates must be finite')
        if self.action in {'inspect','click','type','scroll','options','search','select','verify'} and not self.reference:raise ValueError('Observed reference required')
        if self.action in {'type','search','select','verify'} and self.field is None:raise ValueError('Task field required')
        if self.delta_y is not None and self.action!='scroll':raise ValueError('Scroll argument on another action')
        if self.action=='navigate' and (not self.reference or self.field is not None):raise ValueError('Navigation requires only an observed link reference')
        return self

class V2DiscoveryAction(V2Action):
    """Same validated argument contract; only the action enum is restricted."""
    action:_V2_DISCOVERY_ACTION_NAME


class V2ManualAction(V2Action):
    """Manual operator scope advertises only the fixed Setup fields."""
    field:_V2_SETUP_FIELD_NAME|None=None

class V2ManualNoVisionAction(V2ManualAction):
    action: Literal.__getitem__(tuple(action for action in V2_BROWSER_CONTRACT
                                      if action not in {'navigate', 'screenshot'}))


class V2GATrackingAction(V2Action):
    action: Literal.__getitem__(tuple(action for action in V2_BROWSER_CONTRACT
                                      if action in {'observe', 'dom', 'wait', 'inspect', 'type', 'verify', 'scroll'}))
    field: Literal.__getitem__(GA_SETUP_FIELDS) | None = None


class V2GADisabledAction(V2GATrackingAction):
    field: Literal['campaign_name', 'ga_tracking'] | None = None


class V2CampaignStepAction(V2Action):
    """Deterministic field tools for the separately authorized walkthrough."""
    action: Literal.__getitem__(tuple(action for action in V2_BROWSER_CONTRACT
                                      if action in {'observe', 'dom', 'wait', 'inspect', 'click',
                                                    'options', 'search', 'select', 'verify'}))


class V2CampaignAudienceAction(V2CampaignStepAction):
    field: Literal.__getitem__(STAGE_FIELDS['audience']) | None = None


class V2CampaignContentAction(V2CampaignStepAction):
    field: Literal.__getitem__(STAGE_FIELDS['content']) | None = None


class V2DiscoveryNoVisionAction(V2DiscoveryAction):
    action: Literal.__getitem__(tuple(action for action, spec in V2_BROWSER_CONTRACT.items()
                                      if spec.read_only and action != 'screenshot'))


class V2SetupReadOnlyAction(V2DiscoveryNoVisionAction):
    action: Literal.__getitem__(tuple(action for action, spec in V2_BROWSER_CONTRACT.items()
                                      if spec.read_only and action not in {'navigate', 'screenshot'}))


V2_DIAGNOSTIC_CAPABILITIES=MappingProxyType({
    'observe':((),()),
    'inspect':(('reference',),('reference',)),
    'readback':(('reference',),('reference',)),
    'scroll':(('reference',),('reference','delta_y')),
    'navigate':(('reference',),('reference',)),
})

def _v2_diagnostic_json_schema(schema):
    branches=[]
    all_arguments={'reference','delta_y'}
    for action,(required,allowed) in V2_DIAGNOSTIC_CAPABILITIES.items():
        branch={'properties':{'action':{'const':action}}}
        if required:branch['required']=list(required)
        disallowed=all_arguments-set(allowed)
        if disallowed:branch['not']={'anyOf':[{'required':[name]} for name in sorted(disallowed)]}
        if 'reference' in required:
            branch['properties']['reference']={'type':'string','minLength':1,'maxLength':160}
        branches.append(branch)
    schema['oneOf']=branches

_V2_DIAGNOSTIC_ACTION_NAME=Literal.__getitem__(tuple(V2_DIAGNOSTIC_CAPABILITIES))

class V2DiagnosticAction(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,json_schema_extra=_v2_diagnostic_json_schema)
    action:_V2_DIAGNOSTIC_ACTION_NAME
    reference:StrictStr|None=None
    delta_y:float|None=None

    @model_validator(mode='after')
    def diagnostic_arguments(self):
        required,allowed=V2_DIAGNOSTIC_CAPABILITIES[self.action]
        supplied=set(self.model_fields_set)-{'action'}
        if not set(required)<=supplied or not supplied<=set(allowed) or any(getattr(self,k) is None for k in required):
            raise ValueError('Unsupported diagnostic action arguments')
        if self.reference is not None and (not self.reference.strip() or len(self.reference)>160):
            raise ValueError('Invalid observed reference')
        if self.delta_y is not None:
            import math
            if not math.isfinite(self.delta_y) or abs(self.delta_y)>1000:
                raise ValueError('Scroll limit exceeded')
        return self

def v2_action_arguments(schema):
    from typing import get_args

    contract = V2_DIAGNOSTIC_CAPABILITIES if schema is V2DiagnosticAction else V2_ACTION_ARGUMENTS
    return {action: contract[action] for action in get_args(schema.model_fields['action'].annotation)}


def v2_schema_rejection(schema, arguments):
    actions = v2_action_arguments(schema)
    raw_action = arguments.get('action')
    if not isinstance(raw_action, str) or raw_action not in V2_ACTION_ARGUMENTS and raw_action != 'readback':
        return {'action': 'UNRECOGNIZED', 'reason': 'UNKNOWN_ACTION'}
    if raw_action not in actions:
        return {'action': raw_action, 'reason': 'ACTION_UNAVAILABLE_IN_MODE'}
    required, allowed = actions[raw_action]
    supplied = {key for key, value in arguments.items() if value is not None} - {'action'}
    unexpected = supplied - set(allowed)
    missing = set(required) - supplied
    return {'action': raw_action,
            'reason': 'UNSUPPORTED_ACTION_ARGUMENTS' if unexpected or missing else 'INVALID_ARGUMENT_VALUE',
            'unexpected': sorted(name for name in unexpected if name in schema.model_fields)
                + (['UNSUPPORTED_KEY'] if unexpected - set(schema.model_fields) else []),
            'missing': sorted(missing)}


class V2Turn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['INCOMPLETE','VERIFY','STUCK','BLOCKED']

class GoalFinding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    goal:Literal['login','setup','audience','content','schedule','preview']
    status:Literal['COMPLETE','INCOMPLETE','STUCK','BLOCKED','UNKNOWN']
    observed_state:Literal['DETERMINISTIC_STAGE_READBACK','DROPDOWN_OPEN','CONTROL_UNRESOLVED','FORM_VISIBLE','FIELD_INTERACTABLE','OPTION_SELECTED','NO_PROGRESS','SAVED_DRAFT','PUBLISHED_OR_ACTIVE','UNKNOWN']
    expected_state:Literal['EXACT_STAGE_VALUES','VERIFIED_SAVED_DRAFT']
    progress:bool
    recoverable:bool
    blocker_category:Literal['authentication','authorization','recognition','navigation','tool','controller','browser','environment','uncertain_write']|None
    evidence_refs:list[StrictStr]=Field(max_length=16)

class TeacherExecutionFacts(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    browser_creation_observed:StrictBool
    login_tool_calls:int=Field(ge=0)
    task_tool_calls:int=Field(ge=0)
    credential_submissions_observed:int=Field(ge=0)
    observation_records:int=Field(ge=0)

class V2Lesson(BaseModel):
    model_config=ConfigDict(extra='forbid')
    owner:Literal['model','tool','controller','authentication','authorization','browser','environment','unknown']
    finding:StrictStr=Field(max_length=600)
    evidence_refs:list[StrictStr]=Field(min_length=1,max_length=16)
    code_fix_required:bool
    safe_next_iteration:bool
    instructions:list['ProcedureStep']=Field(max_length=12)
    change_summary:StrictStr=Field(max_length=400)
    recommended_code_repairs:list[StrictStr]=Field(default_factory=list,max_length=6)
    execution_facts:TeacherExecutionFacts|None=None

    @model_validator(mode='after')
    def bounded_repairs(self):
        if any(len(item)>400 for item in self.recommended_code_repairs):raise ValueError('Code repair recommendation exceeds review bound')
        return self

class ProcedureStep(BaseModel):
    """Only typed supported procedures become executable candidate guidance."""
    model_config=ConfigDict(extra='forbid',strict=True)
    goal:Literal['setup','audience','content','schedule','preview']
    when:Literal['control_unresolved','offscreen','options_missing','selection_unverified','milestone_ready',
                 'ga_scoped_control_bound']
    action:Literal['observe','dom','screenshot','inspect','click','type','scroll','options','search','select','verify']
    field:StrictStr|None=None

    @model_validator(mode='after')
    def supported_step(self):
        allowed={'control_unresolved':{'observe','dom','screenshot','inspect'},
            'offscreen':{'inspect','scroll','screenshot'},
            'options_missing':{'observe','inspect','options','click','search','screenshot'},
            'selection_unverified':{'inspect','options','select','verify'},'milestone_ready':{'verify'},
            'ga_scoped_control_bound':{'type','verify'}}
        if self.action not in allowed[self.when]:raise ValueError('Unsupported procedural combination')
        if self.when=='ga_scoped_control_bound' and (self.goal!='setup' or self.field not in ('ga_tracking', *UTM_FIELDS)):
            raise ValueError('GA procedure requires an owned Setup tracking field')
        fields=(*STAGE_FIELDS[self.goal], *UTM_FIELDS) if self.goal=='setup' else STAGE_FIELDS[self.goal]
        if self.field is not None and self.field not in fields:raise ValueError('Candidate field outside goal')
        if self.action not in {'observe','dom','screenshot','inspect','scroll'} and self.field is None:raise ValueError('Candidate field required')
        return self

V2Lesson.model_rebuild()

def v2_contract_summary():
    return {'version':2,'entrypoint':'app.cli whatsapp','stages':STAGE_FIELDS,
        'actions':list(V2Action.model_fields['action'].annotation.__args__),
        'completion':'VERIFIED_SAVED_DRAFT','forbidden':NEVER_ACTIONS,
        'schedule_required':True,'reviewed_ui_evidence_required':False,
        'evidence_mode':'INCREMENTAL_CURRENT_STAGE',
        'independent_authentication_required_before_discovery':True,
        'reviewed_record_evidence_required_before_creation_and_persistence':True,
        'read_only_actions':list(V2DiscoveryAction.model_fields['action'].annotation.__args__),
        'live_verified':False}
