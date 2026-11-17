"""Offline matrix: advertised contracts, routing and no-side-effect rejection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from modules.whatsapp.contract import CAPABILITIES,capability_schema,validate_capability,CapabilityError,ORDER
from modules.whatsapp.learning_loop import BrowserTools

PUBLIC={name:c for name,c in CAPABILITIES.items() if c.exposed}
PAIRS=[(name,field) for name,c in PUBLIC.items() for field in (c.fields or (None,))]

def tools():
 browser=SimpleNamespace(page=SimpleNamespace(evaluate=AsyncMock(return_value=True)),process=object(),login_submissions=0)
 baseline=SimpleNamespace(browser=browser,protect=AsyncMock(),discover=AsyncMock(return_value={'setup_active':True,'controls':[{'field_hint':'campaign_name','visible':True,'enabled':True,'reference':'c'}]}),
  verified=set(),_target=AsyncMock(return_value=({'selected':True,'path':['#observed']},SimpleNamespace(input_value=AsyncMock(return_value='agent_test')))),fill_campaign=AsyncMock())
 t=BrowserTools(baseline);t.verify=AsyncMock(return_value=True)
 state={'public':{'exact_selected':True},'selected':True}
 t.widgets=SimpleNamespace(inspect=AsyncMock(return_value=state),scroll=AsyncMock(return_value=state),open=AsyncMock(return_value=state),complete=AsyncMock(),attempted=set())
 return t

@pytest.mark.parametrize('name,field',PAIRS)
def test_schema_advertises_only_supported_fields(name,field):
 args={} if field is None else {'field':field}
 assert capability_schema(name).model_validate(args)
 assert validate_capability(name,**args)==CAPABILITIES[name]
 for bad in ('unknown','campaign','WABA',None,0,[],{}):
  if field is not None:
   with pytest.raises((ValidationError,CapabilityError)):capability_schema(name).model_validate({'field':bad})
 with pytest.raises(ValidationError):capability_schema(name).model_validate({**args,'selector':'unobserved'})

@pytest.mark.parametrize('name,field',[(n,f) for n,c in PUBLIC.items() for f in ORDER if f not in c.fields])
async def test_unsupported_pair_rejected_before_browser_or_executor(name,field):
 t=tools();result=await t.invoke(name,field)
 assert result['ok'] is False
 t.baseline.protect.assert_not_awaited()
 for op in (t.widgets.inspect,t.widgets.scroll,t.widgets.open,t.widgets.complete):op.assert_not_awaited()

@pytest.mark.parametrize('field',ORDER)
@pytest.mark.parametrize('flag',[True,False,'true',1,None])
def test_dependency_flag_contract(field,flag):
 args={'field':field,'initialize_dependency':flag}
 allowed=field in ('waba','business_number','tags') and type(flag) is bool and (not flag or field=='waba')
 if allowed:assert capability_schema('select_existing_option').model_validate(args)
 else:
  with pytest.raises(ValidationError):capability_schema('select_existing_option').model_validate(args)

@pytest.mark.parametrize('name,field',[(n,f) for n,f in PAIRS if n!='capture_failure'])
async def test_supported_pair_dispatches_to_correct_executor(name,field):
 t=tools();r=await t.invoke(name,**({} if field is None else {'field':field}))
 assert r['ok'],r
 if name in ('inspect_field','inspect_options') and field!='campaign_name':t.widgets.inspect.assert_awaited_once_with(field)
 if name=='scroll_to_field' and field!='campaign_name':t.widgets.scroll.assert_awaited_once_with(field)
 if name=='open_dropdown':t.widgets.open.assert_awaited_once_with(field,authorized=True)
 if name=='select_existing_option':t.widgets.complete.assert_awaited_once_with(field,authorized=True,initialize_dependency=False)

@pytest.mark.parametrize('name',('verify_field','fill_approved_field','select_existing_option'))
async def test_false_verification_is_never_action_success(name):
 t=tools();t.verify=AsyncMock(return_value=False)
 result=await t.invoke(name,'campaign_name' if name!='select_existing_option' else 'waba')
 assert result['ok'] is False

async def test_direct_arguments_are_validated_without_python_typeerror_or_browser_action():
 for name,kwargs in [('open_dropdown',{'field':'campaign_name'}),('observe_page',{'field':'waba'}),('select_existing_option',{'field':'tags','initialize_dependency':True}),('fill_approved_field',{'field':'campaign_name','selector':'bad'})]:
  t=tools();result=await t.invoke(name,**kwargs)
  assert not result['ok'];t.baseline.protect.assert_not_awaited()


async def test_registered_tools_are_exact_registry_schemas():
 t=tools();registered={x.name:x for x in t.registered()}
 assert set(registered)==set(PUBLIC)
 for name,tool in registered.items():assert tool.args_schema is capability_schema(name)

@pytest.mark.parametrize('field',ORDER)
async def test_campaign_fill_executes_only_campaign_and_reads_back(field):
 t=tools();t.verify=AsyncMock(side_effect=[False,True])
 result=await t.invoke('fill_approved_field',field)
 if field=='campaign_name':
  assert result['ok'];t.baseline.fill_campaign.assert_awaited_once_with('c',authorized=True)
 else:
  assert not result['ok'];t.baseline.fill_campaign.assert_not_awaited()

async def test_missing_campaign_does_not_retain_stale_verification():
 t=tools();t.baseline.verified.add('campaign_name');t.baseline.discover.return_value={'controls':[]}
 assert await BrowserTools.verify(t,'campaign_name') is False
 assert 'campaign_name' not in t.baseline.verified

@pytest.mark.parametrize('field',('business_number','tags'))
async def test_dependency_flag_is_refused_before_any_widget_open(field):
 from modules.whatsapp.interactive_widgets import InteractiveWidgets
 from modules.whatsapp.browser_baseline import BaselineStop
 widget=InteractiveWidgets(tools().baseline);widget.open=AsyncMock()
 with pytest.raises(BaselineStop,match='INVALID_TOOL_ARGUMENTS'):
  await widget.complete(field,authorized=True,initialize_dependency=True)
 widget.open.assert_not_awaited()

async def test_unknown_dependency_evidence_never_opens_or_selects():
 from modules.whatsapp.interactive_widgets import InteractiveWidgets
 from modules.whatsapp.browser_baseline import BaselineStop
 widget=InteractiveWidgets(tools().baseline);widget.open=AsyncMock()
 widget.inspect=AsyncMock(side_effect=[{'selected':False},{'selected':False,'public':{'group_count':0,'disabled_target_count':0}}])
 with pytest.raises(BaselineStop,match='DEPENDENCY_INITIALIZATION_NOT_JUSTIFIED'):
  await widget.complete('waba',authorized=True,initialize_dependency=True)
 widget.open.assert_not_awaited()

@pytest.mark.parametrize('field',('waba','business_number','tags'))
async def test_selection_readback_failure_consumes_write_latch(field):
 from modules.whatsapp.interactive_widgets import InteractiveWidgets
 from modules.whatsapp.browser_baseline import BaselineStop
 widget=InteractiveWidgets(tools().baseline)
 state={'selected':False,'native':True,'trigger':['#observed'],'group':['#group'],'options':[{'path':['#option'],'value':'approved-internal-value'}]}
 widget.open=AsyncMock(return_value=state);widget.inspect=AsyncMock(return_value=state)
 widget.baseline._commit_block=AsyncMock();widget.baseline._identity=AsyncMock(return_value=('document',1))
 locator=SimpleNamespace(select_option=AsyncMock());widget._locator=AsyncMock(return_value=(locator,('document',1)))
 widget.readback=AsyncMock(side_effect=BaselineStop('INTERACTIVE_EXACT_SELECTION_NOT_VERIFIED'))
 with pytest.raises(BaselineStop,match='INTERACTIVE_EXACT_SELECTION_NOT_VERIFIED'):await widget.complete(field,authorized=True)
 locator.select_option.assert_awaited_once_with(['approved-internal-value'],timeout=3000)
 with pytest.raises(BaselineStop,match='INTERACTIVE_WRITE_RETRY_FORBIDDEN'):await widget.complete(field,authorized=True)
 assert locator.select_option.await_count==1

async def test_capture_failure_reports_explicit_privacy_skip(monkeypatch):
 import modules.whatsapp.learning_loop as module
 capture=AsyncMock(return_value={'status':'SCREENSHOT_SKIPPED_PRIVACY_OR_TRANSPORT'})
 monkeypatch.setattr(module,'redacted_capture',capture)
 result=await tools().invoke('capture_failure')
 assert result['ok'] and result['status']=='SCREENSHOT_SKIPPED_PRIVACY_OR_TRANSPORT'

@pytest.mark.parametrize('field',('waba','business_number','tags'))
async def test_open_dropdown_does_not_report_unobserved_expansion(field):
 from modules.whatsapp.interactive_widgets import InteractiveWidgets
 from modules.whatsapp.browser_baseline import BaselineStop
 widget=InteractiveWidgets(tools().baseline);widget.baseline._snapshot=AsyncMock(return_value={'setup_active':True})
 widget.baseline._identity=AsyncMock(return_value=('document',1));widget.baseline._commit_block=AsyncMock()
 state={'selected':False,'native':False,'expanded':False,'trigger':['#observed'],'options':[]}
 widget.scroll=AsyncMock(return_value=state);widget.inspect=AsyncMock(return_value=state)
 locator=SimpleNamespace(click=AsyncMock());widget._locator=AsyncMock(return_value=(locator,('document',1)))
 with pytest.raises(BaselineStop,match='DROPDOWN_OPEN_NOT_VERIFIED'):await widget.open(field,authorized=True)
 locator.click.assert_awaited_once()

@pytest.mark.parametrize('field',ORDER)
async def test_safe_observation_does_not_require_recognized_setup(field):
 t=tools();t.baseline.discover.return_value={'setup_active':False,'controls':[]}
 result=await t.invoke('observe_page');assert result['ok'] and not result['setup_active']

@pytest.mark.parametrize('name,field',PAIRS)
async def test_executor_exception_never_becomes_success(name,field):
 t=tools();t.baseline.protect.side_effect=RuntimeError('private browser failure text')
 r=await t.invoke(name,**({} if field is None else {'field':field}))
 assert not r['ok'] and r['error']=='BROWSER_OR_BUDGET_FAILURE'
 assert 'private' not in str(r) and t.terminal


async def test_pending_verification_does_not_prevent_first_authorized_write():
 t=tools();t.verify=AsyncMock(side_effect=[False,False,True])
 pending=await t.invoke('verify_field','campaign_name')
 assert not pending['ok'] and pending['exact_verified'] is False and not pending['terminal']
 assert not t.blocked and not t.terminal
 written=await t.invoke('fill_approved_field','campaign_name')
 assert written['ok'];t.baseline.fill_campaign.assert_awaited_once_with('c',authorized=True)


@pytest.mark.parametrize('name',(None,[],{},0,'save','execute_script','search_existing_option'))
async def test_invalid_tool_names_fail_safely_without_side_effects(name):
 t=tools();r=await t.invoke(name)
 assert not r['ok'] and r['error']=='TOOL_FORBIDDEN'
 t.baseline.protect.assert_not_awaited()


@pytest.mark.parametrize('name',('observe_page','inspect_control','scroll_to_control','click_observed_control','fill_text','select_observed_option','verify_field'))
async def test_reference_tool_schema_and_wrapper_dispatch(name):
 from modules.whatsapp.tools import SetupTools
 from modules.whatsapp.contract import REFERENCE_TOOLS,reference_schema
 executor=object.__new__(SetupTools)
 executor.references={};executor.events=[];executor.field_seconds={f:0.0 for f in ORDER}
 methods={n:AsyncMock(return_value={'ok':True}) for n in REFERENCE_TOOLS}
 for n,m in methods.items():setattr(executor,n,m)
 registered={t.name:t for t in executor.registered_tools()}
 assert registered[name].args_schema is reference_schema(name)
 arguments={key:'opaque-'+key for key in REFERENCE_TOOLS[name][1]}
 assert await registered[name].ainvoke(arguments)=={'ok':True}
 methods[name].assert_awaited_once_with(*arguments.values())
 with pytest.raises(ValidationError):reference_schema(name).model_validate({**arguments,'value':'not-allowed'})
 for key in arguments:
  for bad in (None,17,'',[],{},'x'*161):
   with pytest.raises(ValidationError):reference_schema(name).model_validate({**arguments,key:bad})

@pytest.mark.parametrize('field',ORDER)
def test_reference_fill_role_resolves_same_canonical_authority(field):
 from modules.whatsapp.contract import resolved_reference_action
 if field=='campaign_name':assert resolved_reference_action('fill_text',field)=='fill_approved_field'
 else:
  with pytest.raises(CapabilityError):resolved_reference_action('fill_text',field)
  assert resolved_reference_action('fill_text',field,'search')=='search_existing_option'


async def test_selected_collapsed_waba_opens_only_for_justified_initialization():
 from modules.whatsapp.interactive_widgets import InteractiveWidgets
 widget=InteractiveWidgets(tools().baseline);widget.baseline._snapshot=AsyncMock(return_value={'setup_active':True})
 widget.baseline._commit_block=AsyncMock();widget.baseline._identity=AsyncMock(return_value=('document',1))
 state={'selected':True,'native':False,'expanded':False,'trigger':['#waba'],'group':['#group'],'options':[]}
 async def inspect(field):
  if field=='business_number':return {'selected':False,'public':{'group_count':1,'disabled_target_count':1}}
  return dict(state)
 widget.inspect=AsyncMock(side_effect=inspect);widget.scroll=AsyncMock(side_effect=lambda field:dict(state))
 async def open_popup(**kwargs):state.update(expanded=True,options=[{'path':['#option'],'value':None}])
 trigger=SimpleNamespace(click=AsyncMock(side_effect=open_popup));option=SimpleNamespace(click=AsyncMock())
 widget._locator=AsyncMock(side_effect=lambda path:(option if path==['#option'] else trigger,('document',1)))
 widget.readback=AsyncMock(return_value=True)
 await widget.complete('waba',authorized=True,initialize_dependency=True)
 trigger.click.assert_awaited_once();option.click.assert_awaited_once()
 assert 'waba' in widget.attempted
