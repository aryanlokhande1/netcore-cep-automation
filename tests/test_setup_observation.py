"""Actual read-only JS over deterministic DOM doubles, no browser or inference."""
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from modules.whatsapp.contract import LABELS,VALUES
from modules.whatsapp.observation import OBSERVE_SETUP,observe_setup


def fixture():
    controls=[]
    for key,label in LABELS.items():
        controls.append({'tag':'label','attrs':{'for':key},'text':label})
        if key=='campaign_name':controls.append({'tag':'input','attrs':{'id':key,'value':VALUES[key]}})
        else:controls.append({'tag':'select','attrs':{'id':key,'multiple':key=='tags'},'children':[
            {'tag':'option','attrs':{'value':VALUES[key] if key=='waba' else 'fixture-option','selected':True},'text':VALUES[key]}]})
    return [{'tag':'h1','text':'WhatsApp campaign'},{'tag':'li','attrs':{'class':'active'},'text':'Setup'},
        {'tag':'form','attrs':{'id':'setup-form'},'children':controls}]


def observe(children=None,*,geometry=True):
    node=shutil.which('node')
    if not node:pytest.skip('Node is required for actual read-only JS deterministic tests')
    data={'children':fixture() if children is None else children,'geometry':geometry,
        'expression':OBSERVE_SETUP.replace('SETTINGS',json.dumps({'labels':LABELS,'values':VALUES}))}
    result=subprocess.run([node,str(Path(__file__).with_name('setup_observation_dom_double.js'))],
        input=json.dumps(data),text=True,capture_output=True,check=True,timeout=5)
    wrapped=json.loads(result.stdout)
    assert wrapped['unchanged']
    return wrapped['snapshot']


def test_visible_native_controls_have_observed_unique_selectors_without_dom_writes():
    result=observe()
    assert result['observation_complete'] and result['form_recognized'] and result['same_form']
    assert result['page_identity'] and result['setup_active'] and result['form_selector']=='#setup-form'
    assert result['closed_shadow_scope_certified'] is False
    assert 'guard_installed' not in result and 'forbidden_attempts' not in result
    for key,field in result['fields'].items():
        assert field['selected'] and field['selector_unique'] and field['visible'] and field['enabled'] and field['ownership_verified']
        assert field['unobscured'] and field['geometry_status']=='UNOBSCURED' and field['same_form'] and field['action_safe']
        assert field['selector']=='#'+key and field['form_selector']=='#setup-form'
        assert 'data-netcore' not in field['selector']


def test_campaign_form_can_be_recognized_before_other_controls_are_mapped():
    children=fixture();children[2]['children']=children[2]['children'][:2]
    result=observe(children)
    assert result['form_recognized'] and not result['same_form']
    assert result['fields']['campaign_name']['discovered'] and result['fields']['campaign_name']['ownership_verified']
    assert not result['fields']['waba']['discovered']


def test_duplicate_visible_campaign_controls_remain_ambiguous():
    children=fixture();children[2]['children'].append({'tag':'input','attrs':{'id':'another','aria-label':LABELS['campaign_name']}})
    result=observe(children)
    assert result['fields']['campaign_name']['ambiguous'] and not result['form_recognized']


def test_duplicate_private_ids_use_verified_structural_selectors():
    children=fixture();children.append({'tag':'div','attrs':{'id':'campaign_name'}})
    field=observe(children)['fields']['campaign_name']
    assert field['selector_unique'] and field['discovered'] and ':nth-of-type(' in field['selector']


def test_hidden_native_select_is_not_actionable_from_visible_wrapper():
    children=fixture();children[2]['children'][3]['attrs']['hidden']=True
    result=observe(children)
    assert not result['fields']['waba']['discovered'] and not result['same_form']
    assert result['form_recognized']


def test_waba_label_similarity_cannot_replace_the_exact_observed_identifier():
    children=fixture();option=children[2]['children'][3]['children'][0]
    option['attrs']['value']='different-fixture-identifier';option['text']='Display '+VALUES['waba']
    field=observe(children)['fields']['waba']
    assert field['exact_option_count']==0 and not field['selected'] and field['value'] is None


def test_existing_tag_matching_remains_case_sensitive():
    children=fixture();children[2]['children'][7]['children'][0]['text']='Test'
    field=observe(children)['fields']['tags']
    assert field['exact_option_count']==0 and not field['selected'] and field['has_other_tags']


def test_disabled_native_option_cannot_become_an_actionable_choice():
    children=fixture();children[2]['children'][3]['children'][0]['attrs']['disabled']=True
    field=observe(children)['fields']['waba']
    assert field['exact_option_count']==0 and not field['selected'] and field['option_selector'] is None


@pytest.mark.parametrize('attribute,value',[('disabled',True),('readonly',True),('aria-disabled','true'),('inert',True)])
def test_enabled_evidence_reflects_actual_control_restrictions(attribute,value):
    children=fixture();children[2]['children'][1]['attrs'][attribute]=value
    field=observe(children)['fields']['campaign_name']
    assert field['discovered'] and not field['enabled'] and field['disabled']


def combo_fixture():
    children=fixture();form=children[2]
    form['children'][7]={'tag':'div','attrs':{'id':'tags','role':'combobox','aria-label':LABELS['tags'],'aria-controls':'tag-options','aria-expanded':'true'},'text':'test'}
    children.append({'tag':'div','attrs':{'id':'tag-options','role':'listbox'},'children':[
        {'tag':'input','attrs':{'id':'tag-search','type':'search'}},
        {'tag':'div','attrs':{'id':'existing-tag','role':'option','data-existing':'true','aria-selected':'true'},'text':'test'}]})
    return children


def test_actual_unique_aria_ownership_yields_existing_option_and_search_selectors():
    field=observe(combo_fixture())['fields']['tags']
    assert field['kind']=='OWNED_COMBOBOX' and field['ownership_verified'] and field['selected']
    assert field['open_selector']=='#tags' and field['search_selector']=='#tag-search'
    assert field['option_selector']==field['value']=='#existing-tag' and field['exact_option_count']==1


def test_collapsed_existing_selected_option_is_verification_evidence_not_an_action_target():
    children=combo_fixture();children[3]['attrs']['hidden']=True
    children[2]['children'][7]['attrs']['aria-expanded']='false'
    field=observe(children)['fields']['tags']
    assert field['selected'] and field['ownership_verified'] and field['action_safe']
    assert field['existing_exact_option_count']==1 and field['exact_option_count']==0
    assert field['option_selector'] is None and field['search_selector'] is None and field['value'] is None


@pytest.mark.parametrize('case',['href','submit','password','file','contenteditable','Save','Next Step','Finish Later','Send','Schedule','Publish','Activate'])
def test_misleading_aria_label_does_not_authorize_dangerous_controls(case):
    children=combo_fixture();control=children[2]['children'][7]
    if case=='href':control['tag']='a';control['attrs']['href']='/not-authorized'
    elif case in {'submit','password','file'}:control['tag']='input';control['attrs']['type']=case
    elif case=='contenteditable':control['attrs']['contenteditable']='true'
    else:control['text']=case
    field=observe(children)['fields']['tags']
    assert not field.get('action_safe',False)
    assert field.get('kind')!='OWNED_COMBOBOX'
    assert field.get('open_selector') is None


def test_visible_non_submit_button_proxy_requires_actual_unique_owned_popup():
    children=combo_fixture();control=children[2]['children'][7]
    control['tag']='button';control['attrs']['type']='button'
    field=observe(children)['fields']['tags']
    assert field['action_safe'] and field['kind']=='OWNED_COMBOBOX' and field['ownership_verified']


@pytest.mark.parametrize('case',['option_href','option_submit','option_contenteditable','search_contenteditable','search_href'])
def test_owned_popup_relationship_cannot_authorize_dangerous_option_or_search_targets(case):
    children=combo_fixture();option=children[3]['children'][1];search=children[3]['children'][0]
    if case=='option_href':option['tag']='a';option['attrs']['href']='/not-authorized'
    elif case=='option_submit':option['tag']='button';option['attrs']['type']='submit'
    elif case=='option_contenteditable':option['attrs']['contenteditable']='true'
    elif case=='search_contenteditable':search['attrs']['contenteditable']='true'
    else:search['attrs']['href']='/not-authorized'
    field=observe(children)['fields']['tags']
    if case.startswith('option_'):assert field['exact_option_count']==0 and field['option_selector'] is None and not field['selected']
    else:assert field['search_selector'] is None


@pytest.mark.parametrize('case',['missing_owner','duplicate_owner_id','competing_widget','creatable_option'])
def test_combobox_ownership_and_existing_tag_contract_cannot_be_inferred(case):
    children=combo_fixture()
    if case=='missing_owner':children[2]['children'][7]['attrs'].pop('aria-controls')
    elif case=='duplicate_owner_id':children.append({'tag':'div','attrs':{'id':'tag-options','role':'listbox'}})
    elif case=='competing_widget':children.append({'tag':'div','attrs':{'id':'other-widget','role':'combobox','aria-controls':'tag-options'}})
    else:children[3]['children'][1]['attrs']['data-create']='true'
    field=observe(children)['fields']['tags']
    assert field['discovered']
    if case=='creatable_option':assert field['exact_option_count']==0 and not field['selected']
    else:assert not field['ownership_verified'] and field['option_selector'] is None and field['open_selector'] is None


def test_different_field_owner_prevents_recognizing_one_authorized_form():
    children=fixture();waba=children[2]['children'].pop(3)
    children.append({'tag':'form','attrs':{'id':'unrelated-form'},'children':[waba]})
    result=observe(children)
    assert not result['form_recognized'] and not result['same_form']


def test_global_shadow_presence_does_not_block_read_only_form_observation():
    children=fixture();children.append({'tag':'div','shadow':True})
    result=observe(children)
    assert result['form_recognized'] and result['observation_complete']
    assert result['observation_counts']['open_shadow_hosts_observed']==1
    assert not result['closed_shadow_scope_certified']


def test_read_only_observation_reports_its_actual_node_limit_without_certifying_completeness():
    children=fixture()+[{'tag':'div'} for _ in range(12001)]
    result=observe(children)
    assert not result['observation_complete']
    assert result['observation_counts']['traversal_limit_reached']
    assert result['observation_counts']['nodes_inspected']==12000


def test_wrong_step_is_observed_without_claiming_setup_identity():
    children=fixture();children.append({'tag':'li','attrs':{'class':'active'},'text':'Audience'})
    result=observe(children)
    assert result['form_recognized'] and not result['setup_active'] and not result['page_identity']


@pytest.mark.parametrize('condition,status',[('covered','COVERED'),('offscreen','OFFSCREEN')])
def test_geometry_never_authorizes_overlay_or_offscreen_targets(condition,status):
    children=fixture();children[2]['children'][1]['attrs'][condition]=True
    field=observe(children)['fields']['campaign_name']
    assert field['visible'] and field['selector_unique'] and field['unobscured'] is False
    assert field['geometry_status']==status


def test_missing_geometry_is_explicitly_not_checked():
    field=observe(geometry=False)['fields']['campaign_name']
    assert field['unobscured'] is None and field['geometry_status']=='NOT_CHECKED'


async def test_async_observer_uses_only_fixed_contract_and_returns_private_descriptor_snapshot():
    page=SimpleNamespace(evaluate=AsyncMock(return_value={'fields':{}}))
    assert await observe_setup(SimpleNamespace(page=page))=={'fields':{}}
    expression=page.evaluate.call_args.args[0]
    assert 'setAttribute(' not in expression and 'addEventListener(' not in expression
    assert VALUES['campaign_name'] in expression


@pytest.mark.parametrize('alias,field',__import__('modules.whatsapp.contract',fromlist=['FIELD_ALIASES']).FIELD_ALIASES.items())
def test_shared_label_aliases_resolve_the_same_canonical_field(alias,field):
    children=fixture()
    label=next(e for e in children[2]['children'] if e['tag']=='label' and e['attrs']['for']==field)
    label['text']=alias
    snapshot=observe(children)
    assert snapshot['fields'][field]['discovered']
    assert snapshot['fields'][field]['selected']
