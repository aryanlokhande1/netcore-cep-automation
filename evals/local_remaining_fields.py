"""Hypothetical widget compatibility in actual headed loopback Chromium.

These fixtures are not representations of Netcore's unrecorded tenant DOM.
Only fixed booleans, counters and sanitized public discovery leave the browser.
"""
from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic

from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.protected_tabs import create_protected_tab,bound_tab_observable
from browser.stagehand_client import StagehandClient
from evals.local_browser_baseline import settled
from modules.whatsapp.browser_baseline import BrowserBaseline,sanitized_action_error
from modules.whatsapp.contract import VALUES


VALID_CASES=('native','readonly_input','owned_hidden_display','custom_dependent','shadow_custom',
    'readonly_trigger_waba','owned_backing_tag')
NEGATIVE_CASES=('ambiguous_waba','mismatched_hidden','create_only_tag')


def _native(field,label,*,selected=False):
    approved=VALUES[field]
    value=approved if field=='waba' else 'PRIVATE_APPROVED_'+field.upper()
    return '<fieldset><label for="PRIVATE_'+field+'">'+label+'</label><select id="PRIVATE_'+field+'">'+\
        ('<option value="" selected>Select</option>' if not selected else '')+\
        '<option value="'+escape(value,quote=True)+'"'+(' selected' if selected else '')+'>'+escape(approved)+'</option></select></fieldset>'


def _custom(field,label,*,selected=False,suffix=''):
    identity='PRIVATE_'+field+suffix
    value=VALUES[field] if field=='waba' else 'PRIVATE_APPROVED_'+field.upper()
    return '<fieldset id="'+identity+'_group"><label id="'+identity+'_label" for="'+identity+'">'+label+'</label>'+\
        '<button id="'+identity+'" type="button" aria-haspopup="listbox" aria-controls="'+identity+'_options" '+\
        'aria-labelledby="'+identity+'_label" data-value="'+(escape(value,quote=True) if selected else '')+'">'+\
        (escape(VALUES[field]) if selected else 'Select')+'</button>'+\
        '<div id="'+identity+'_options" role="listbox" hidden><div id="'+identity+'_option" role="option" '+\
        'data-value="'+escape(value,quote=True)+'"'+(' data-waba-id="'+VALUES[field]+'"' if field=='waba' else '')+\
        ' aria-selected="'+('true' if selected else 'false')+'">'+escape(VALUES[field])+'</div></div></fieldset>'


def widget_html(case):
    if case in {'readonly_input','readonly_trigger_waba'}:
        waba='<fieldset><label for="PRIVATE_waba">WABA ID</label><input id="PRIVATE_waba" readonly value="'+VALUES['waba']+'"></fieldset>'
        if case=='readonly_trigger_waba':
            waba=waba.replace('</fieldset>','<button id="PRIVATE_waba_trigger" type="button" aria-haspopup="listbox" '+
                'aria-controls="PRIVATE_unmounted_waba_options">Choose</button></fieldset>')
    elif case in {'owned_hidden_display','mismatched_hidden'}:
        actual=VALUES['waba'] if case=='owned_hidden_display' else 'PRIVATE_DIFFERENT_WABA'
        waba='<fieldset><legend id="PRIVATE_waba_label">WABA ID</legend>'+\
            '<output id="PRIVATE_waba" for="PRIVATE_hidden_waba" aria-labelledby="PRIVATE_waba_label">'+VALUES['waba']+'</output>'+\
            '<input type="hidden" id="PRIVATE_hidden_waba" value="'+actual+'"></fieldset>'+\
            '<p aria-hidden="true">'+VALUES['waba']+'</p>'
    elif case=='ambiguous_waba':
        waba=_custom('waba','WABA ID',selected=True)+_custom('waba','WABA ID',selected=True,suffix='_second')
    elif case in {'custom_dependent','shadow_custom'}:
        waba=_custom('waba','WABA ID')
    else:waba=_native('waba','WABA ID',selected=True)
    business='' if case=='custom_dependent' else _native('business_number','Business number')
    if case=='native':tags=_native('tags','Add tags')
    elif case=='owned_backing_tag':
        tags='<fieldset id="PRIVATE_tags_group"><label id="PRIVATE_tag_label" for="PRIVATE_tag_backing">Add tags</label>'+\
            '<select id="PRIVATE_tag_backing" hidden><option value="" selected>Select</option>'+\
            '<option value="PRIVATE_EXISTING_TAG_ID">test</option><option value="PRIVATE_OTHER_TAG_ID">Test</option></select>'+\
            '<input id="PRIVATE_tags" type="text" role="combobox" aria-labelledby="PRIVATE_tag_label" '+\
            'aria-controls="PRIVATE_tag_options" aria-expanded="false">'+\
            '<div id="PRIVATE_tag_options" role="listbox" hidden>'+\
            '<div id="PRIVATE_tag_option" role="option" data-value="PRIVATE_EXISTING_TAG_ID" aria-selected="false">test</div>'+\
            '<div role="option" data-value="PRIVATE_OTHER_TAG_ID" aria-selected="false">Test</div></div></fieldset>'
    else:
        creation=' data-create="true"' if case=='create_only_tag' else ' data-existing="true"'
        typed=' value="test"' if case=='readonly_input' else ''
        tags='<fieldset id="PRIVATE_tags_group"><label for="PRIVATE_tags">Add tags</label>'+\
            '<input id="PRIVATE_tags" type="text" role="combobox" aria-controls="PRIVATE_tag_options" aria-expanded="false"'+typed+'>'+\
            '<div id="PRIVATE_tag_options" role="listbox" hidden>'+\
            '<div id="PRIVATE_tag_option" role="option"'+creation+' aria-selected="false">test</div>'+\
            '<div role="option" data-existing="true" aria-selected="false">Test</div></div></fieldset>'
    body='''<!doctype html><meta charset="utf-8"><title>Local remaining-field widgets</title>
    <style>body{font:15px sans-serif;margin:18px}fieldset{border:0;padding:5px;margin:3px}
    label,legend{display:block}input,select,button{padding:5px} [role=option]{padding:5px;cursor:pointer}
    [hidden]{display:none!important}</style><h1>Create campaign</h1><h2 aria-current="step">Setup</h2>
    <section id="PRIVATE_editor"><label for="PRIVATE_campaign">Campaign name</label><input id="PRIVATE_campaign" type="text">
    <div id="PRIVATE_waba_mount">WABA</div><div id="PRIVATE_business_mount">BUSINESS</div><div id="PRIVATE_tags_mount">TAGS</div>
    <label>PRIVATE_LABEL_CANARY<input id="PRIVATE_UNRELATED_INPUT" value="PRIVATE_VALUE_CANARY"></label>
    <button id="PRIVATE_save" type="button">Save</button></section><script>
    history.replaceState(null,'',location.pathname+'#setup');
    window.__widgetCounts={campaign_input:0,waba_interactions:0,business_choices:0,tag_choices:0,save:0};
    window.__widgetEvents=[];
    const deep=(id,root=document)=>{
      const found=root.querySelector('#'+CSS.escape(id));if(found)return found;
      for(const host of root.querySelectorAll('*'))if(host.shadowRoot){const e=deep(id,host.shadowRoot);if(e)return e;}
      return null;
    };
    window.__localDeep=deep;
    const wireCustom=(field,suffix='')=>{
      const id='PRIVATE_'+field+suffix,button=deep(id),list=deep(id+'_options'),option=deep(id+'_option');
      if(!button||button.tagName!=='BUTTON')return;
      button.addEventListener('click',()=>{list.hidden=false;button.setAttribute('aria-expanded','true');
        if(field==='waba')window.__widgetCounts.waba_interactions++;});
      option.addEventListener('click',()=>{
        button.textContent=option.textContent;button.setAttribute('data-value',option.getAttribute('data-value'));
        option.setAttribute('aria-selected','true');
        if(field==='waba'){
          window.__widgetCounts.waba_interactions++;window.__widgetEvents.push('WABA_SELECTED');
          if(CASE==='custom_dependent'){
            setTimeout(()=>{document.getElementById('PRIVATE_business_mount').innerHTML=BUSINESS_HTML;
              wireCustom('business_number');},650);
          }
        }else{window.__widgetCounts.business_choices++;window.__widgetEvents.push('BUSINESS_SELECTED');}
      });
    };
    if(CASE==='shadow_custom'){
      for(const name of ['waba','business','tags']){
        const mount=document.getElementById('PRIVATE_'+name+'_mount'),root=mount.attachShadow({mode:'open'});
        while(mount.firstChild)root.append(mount.firstChild);
      }
    }
    document.getElementById('PRIVATE_campaign').addEventListener('input',()=>window.__widgetCounts.campaign_input++);
    const waba=deep('PRIVATE_waba');
    if(['SELECT','INPUT'].includes(waba?.tagName))for(const event of ['input','change','click'])waba.addEventListener(event,()=>window.__widgetCounts.waba_interactions++);
    deep('PRIVATE_waba_trigger')?.addEventListener('click',()=>window.__widgetCounts.waba_interactions++);
    wireCustom('waba');wireCustom('waba','_second');
    const business=deep('PRIVATE_business_number');
    if(business?.tagName==='SELECT')business.addEventListener('change',()=>{
      window.__widgetCounts.business_choices++;window.__widgetEvents.push('BUSINESS_SELECTED')});
    const tags=deep('PRIVATE_tags');
    if(tags?.tagName==='SELECT')tags.addEventListener('change',()=>window.__widgetCounts.tag_choices++);
    else if(tags){
      const choices=deep('PRIVATE_tag_options'),choice=deep('PRIVATE_tag_option');
      tags.addEventListener('click',()=>{choices.hidden=false;tags.setAttribute('aria-expanded','true')});
      choice.addEventListener('click',()=>{
        tags.value=choice.textContent;choice.setAttribute('aria-selected','true');window.__widgetCounts.tag_choices++;
        const backing=deep('PRIVATE_tag_backing');
        if(backing){backing.value=choice.getAttribute('data-value');backing.dispatchEvent(new Event('change',{bubbles:true}));}
      });
    }
    document.getElementById('PRIVATE_save').addEventListener('click',()=>window.__widgetCounts.save++);
    </script>'''
    return body.replace('>WABA<','>'+waba+'<').replace('>BUSINESS<','>'+business+'<').replace('>TAGS<','>'+tags+'<').replace(
        'CASE',json.dumps(case)).replace('BUSINESS_HTML',json.dumps(_custom('business_number','Business number')))


@contextmanager
def widget_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/regular':self.send_error(404);return
            data=widget_html(self.server.case).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):self.server.posts+=1;self.send_error(405)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.case='native';server.posts=0
    worker=Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True);worker.start()
    try:yield server,'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();server.server_close();worker.join(2)


async def widget_facts(page):
    return await page.evaluate('''(values=>{
      const find=window.__localDeep,waba=find('PRIVATE_waba'),hidden=find('PRIVATE_hidden_waba'),business=find('PRIVATE_business_number'),tags=find('PRIVATE_tags');
      const wabaMatches=waba.tagName==='SELECT'||waba.tagName==='INPUT'?waba.value===values.waba:
        waba.tagName==='OUTPUT'?waba.textContent===values.waba&&hidden?.value===values.waba:
        waba.getAttribute('data-value')===values.waba&&find('PRIVATE_waba_option')?.getAttribute('aria-selected')==='true';
      const businessMatches=!!business&&(business.tagName==='SELECT'?business.selectedOptions[0]?.textContent===values.business_number:
        business.textContent===values.business_number&&find('PRIVATE_business_number_option')?.getAttribute('aria-selected')==='true');
      const backing=find('PRIVATE_tag_backing'),choice=find('PRIVATE_tag_option');
      const sameBacking=!!backing&&backing.selectedOptions.length===1&&backing.selectedOptions[0].value==='PRIVATE_EXISTING_TAG_ID'&&
        backing.selectedOptions[0].value===choice?.getAttribute('data-value')&&backing.selectedOptions[0].textContent===values.tags;
      const tagMatches=tags.tagName==='SELECT'?tags.selectedOptions[0]?.textContent===values.tags:
        tags.value===values.tags&&choice?.getAttribute('aria-selected')==='true'&&(choice?.getAttribute('data-existing')==='true'||sameBacking);
      return {campaign_matches:find('PRIVATE_campaign').value===values.campaign_name,waba_matches:!!wabaMatches,
        business_matches:!!businessMatches,tag_matches:!!tagMatches,same_existing_backing_tag_selected:sameBacking,counts:{...window.__widgetCounts},
        dependency_order:window.__widgetEvents.indexOf('WABA_SELECTED')>=0&&window.__widgetEvents.indexOf('BUSINESS_SELECTED')>window.__widgetEvents.indexOf('WABA_SELECTED')};
    })(VALUES)'''.replace('VALUES',json.dumps(VALUES)))


def _reference(data,field):
    candidates=[c['reference'] for c in data['controls'] if c.get('field_hint')==field]
    if len(candidates)!=1:raise RuntimeError('LOCAL_CAMPAIGN_NOT_UNIQUE')
    return candidates[0]


async def accept_remaining_fields(executable,*,output=None):
    report={'scope':'HYPOTHETICAL_LOCAL_WIDGET_COMPATIBILITY','headless':False,'live_netcore':False,
        'paid_inference':0,'credential_access':False,'passed':False,'cases':[]}
    trace=Trace();started=monotonic();browser=None
    with widget_server() as (server,origin):
        destination=origin+'/regular'
        browser=StagehandClient(Config(str(executable),headless=False,login_url='about:blank',expected_host='127.0.0.1',
            timeout_seconds=180,max_actions=250*len((*VALID_CASES,*NEGATIVE_CASES))+100),OriginPolicy({origin}),trace,fixture=True)
        try:
            await browser.start();original=browser.page;context=browser.browser.context;process=browser.process
            await original.goto('about:blank');await settled(browser,browser.guard.primary_session)
            page,binding=await create_protected_tab(browser);browser.page=page;browser.setup_binding=binding
            for case in (*VALID_CASES,*NEGATIVE_CASES):
                start_actions=trace.actions
                item={'case':case,'passed':False,'expected':'PASS' if case in VALID_CASES else 'REFUSE'}
                try:
                    server.case=case;await page.goto(destination);await page.wait_for_load_state('domcontentloaded',timeout=5000)
                    await settled(browser,binding.session)
                    executor=BrowserBaseline(browser,destination);discovery=await executor.discover()
                    public=json.dumps(discovery)
                    item['private_evidence_not_logged']='PRIVATE_' not in public and VALUES['waba'] not in public and VALUES['business_number'] not in public
                    item['field_diagnostics_present']=isinstance(discovery.get('field_diagnostics'),(dict,list))
                    item['dependent_business_initially_unmapped']=case!='custom_dependent' or not any(c.get('field_hint')=='business_number' for c in discovery['controls'])
                    await executor.fill_campaign(_reference(discovery,'campaign_name'),authorized=True)
                    try:
                        result=await executor.complete_remaining(mapping=None,authorized=True)
                        item['status']=result.get('status');item['verified_fields']=result.get('verified_fields')
                    except Exception as error:item['stop']=sanitized_action_error(error,executor.stage)
                    facts=await widget_facts(page)
                    item.update(campaign_verified=facts['campaign_matches'],all_four_actual_values=all(facts[k] for k in
                        ('campaign_matches','waba_matches','business_matches','tag_matches')),
                        waba_interactions=facts['counts']['waba_interactions'],tag_option_selections=facts['counts']['tag_choices'],
                        dependency_order=case!='custom_dependent' or facts['dependency_order'],no_commit=facts['counts']['save']==0)
                    if case in VALID_CASES:
                        item['passed']=item.get('status')=='FOUR_FIELDS_VERIFIED_UNSAVED' and item['all_four_actual_values']
                        if case in {'native','readonly_input','owned_hidden_display','readonly_trigger_waba','owned_backing_tag'}:item['passed'] &= facts['counts']['waba_interactions']==0
                        if case=='readonly_input':item['passed'] &= facts['counts']['tag_choices']==1
                        if case=='readonly_trigger_waba':
                            item['preselected_waba_verified_without_opening']=facts['counts']['waba_interactions']==0
                        if case=='owned_backing_tag':
                            item['same_existing_backing_tag_selected']=facts['same_existing_backing_tag_selected']
                            item['passed'] &= facts['same_existing_backing_tag_selected'] and facts['counts']['tag_choices']==1
                        item['passed'] &= item['dependency_order'] and item['dependent_business_initially_unmapped']
                        await page.locator('#PRIVATE_save').click(timeout=2000)
                        item['save_handler_blocked']=(await widget_facts(page))['counts']['save']==0
                        item['passed'] &= item['save_handler_blocked']
                    else:
                        item['passed']='stop' in item and item.get('status')!='FOUR_FIELDS_VERIFIED_UNSAVED'
                        if case=='ambiguous_waba':
                            item['passed'] &= item.get('stop',{}).get('code')=='FIELD_CONTROL_AMBIGUOUS'
                        if case=='mismatched_hidden':
                            item['passed'] &= item.get('stop',{}).get('code') in {'BACKING_SELECTION_CONFLICT','READ_ONLY_SELECTION_NOT_VERIFIED'}
                        if case in {'ambiguous_waba','mismatched_hidden'}:item['passed'] &= 'waba' not in executor.verified and facts['counts']['waba_interactions']==0
                        if case=='create_only_tag':
                            item['passed'] &= item.get('stop',{}).get('code')=='EXACT_EXISTING_OPTION_UNESTABLISHED' and item['stop'].get('field')=='tags'
                            item['passed'] &= {'waba','business_number'}<=executor.verified and 'tags' not in executor.verified and facts['counts']['tag_choices']==0
                    item['passed'] &= item['private_evidence_not_logged'] and item['field_diagnostics_present'] and item['no_commit']
                except Exception as error:item['error']=sanitized_action_error(error,'LOCAL_FIXTURE_ACCEPTANCE')
                item['browser_action_budget_used']=trace.actions-start_actions
                report['cases'].append(item)
            report['checks']={'guard_healthy':not browser.guard.failed,'correct_tab':bound_tab_observable(browser.guard,binding),
                'original_retained':await original.evaluate('document.documentElement!==null') is True,
                'same_context':browser.browser.context is context,'same_owned_process':browser.process is process,
                'exactly_two_pages':len(await context.pages())==2,'no_login_submission':browser.login_submissions==0,
                'no_paid_requests':trace.openai_api_calls==0,'no_fixture_posts':server.posts==0}
            report['passed']=all(c['passed'] for c in report['cases']) and all(report['checks'].values())
        except Exception as error:report['error']=sanitized_action_error(error,'LOCAL_BROWSER_START')
        finally:
            try:await browser.close()
            except Exception:report['cleanup_exception']=True
            report['cleanup_result']=trace.cleanup_result
            report['passed']=bool(report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED')
            report['seconds']=round(monotonic()-started,3)
            Path(output or ROOT/'outputs/real-local-remaining-fields.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
