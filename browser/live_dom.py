"""DOM-derived semantic roles. Only canonical labels and private selectors leave JS."""
LIVE_DOM = r'''(prefix => {
 const visible=el=>!!el && el.getClientRects().length>0 && getComputedStyle(el).visibility!=='hidden';
 const norm=s=>(s||'').toLowerCase().replace(/[_-]/g,' ').replace(/\s+/g,' ').trim();
 const controls=[...document.querySelectorAll('input:not([type=hidden]),button,[role=button]')].filter(visible).slice(0,30);
 return controls.flatMap((el,i)=>{
  const tag=el.tagName.toLowerCase(),type=norm(el.getAttribute('type')||'text');
  const label=norm([el.getAttribute('aria-label'),el.labels?.[0]?.textContent,el.getAttribute('placeholder'),el.getAttribute('name'),el.id].filter(Boolean).join(' '));
  let key=null;
  if(tag==='input' && type==='password')key='password';
  else if(tag==='input' && /\b(account|tenant|organization|company)\b/.test(label))key='account';
  else if(tag==='input' && (/\b(user\s*name|username|email|user\s*id)\b/.test(label)||el.autocomplete==='username'))key='username';
  else if((tag==='button'||el.getAttribute('role')==='button'||type==='submit') && /^(log\s*in|sign\s*in)$/.test(norm(el.textContent||el.getAttribute('value'))))key='submit';
  if(!key)return [];
  const evidence=['VISIBLE'];
  if(key==='password')evidence.push('PASSWORD_TYPE');
  else if(key==='submit')evidence.push('SUBMIT_TEXT');
  else {
   const pattern=key==='account'?/\b(account|tenant|organization|company)\b/:/\b(user\s*name|username|email|user\s*id)\b/;
   for(const [source,value] of [['ARIA_LABEL',el.getAttribute('aria-label')],['ASSOCIATED_LABEL',el.labels?.[0]?.textContent],['PLACEHOLDER',el.getAttribute('placeholder')],['NAME_HINT',el.name],['ID_HINT',el.id]])
    if(pattern.test(norm(value)))evidence.push(source);
   if(key==='username'&&el.autocomplete==='username')evidence.push('AUTOCOMPLETE_ROLE');
  }
  const token=prefix+'-'+i;el.setAttribute('data-pass1-element',token);
  const form=el.form||el.closest('form');
  if(form && !form.hasAttribute('data-login-form-ref'))form.setAttribute('data-login-form-ref',prefix+'-form-'+i);
  return [{key,role:key==='submit'?'button':'textbox',label:{account:'Account name',username:'Username',password:'Password',submit:'Login'}[key],
   selector:'[data-pass1-element="'+token+'"]',sensitive:key!=='submit',
   form_ref:form?.getAttribute('data-login-form-ref')||null,input_type:type,evidence}];
 });
})(PREFIX)'''

VALIDATE_MAPPING = r'''(mapping => {
 const visible=el=>!!el && el.getClientRects().length>0 && !el.disabled && getComputedStyle(el).visibility!=='hidden';
 const keys=['account','username','password','submit'];
 const els=Object.fromEntries(keys.map(k=>[k,document.querySelector(mapping[k].selector)]));
 if(!keys.every(k=>visible(els[k])) || new Set(keys.map(k=>els[k])).size!==4)return false;
 const forms=keys.map(k=>els[k].form||els[k].closest('form'));
 if(!forms[0] || !forms.every(f=>f===forms[0]))return false;
 if(els.password.type!=='password' || ['password','hidden'].includes(els.account.type)||['password','hidden'].includes(els.username.type))return false;
 const action=new URL(forms[0].getAttribute('action')||location.href,location.href);
 if(action.origin!==location.origin)return false;
 const norm=s=>(s||'').toLowerCase().replace(/[_-]/g,' ');
 const label=el=>norm([el.getAttribute('aria-label'),el.labels?.[0]?.textContent,el.getAttribute('placeholder'),el.name,el.id].filter(Boolean).join(' '));
 const inputs=[...forms[0].querySelectorAll('input:not([type=hidden])')].filter(visible);
 if(inputs.filter(e=>e.type==='password').length!==1 || inputs.filter(e=>e.type!=='password'&&/\b(account|tenant|organization|company)\b/.test(label(e))).length!==1 || inputs.filter(e=>e.type!=='password'&&/\b(user\s*name|username|email|user\s*id)\b/.test(label(e))).length!==1)return false;
 return /\b(account|tenant|organization|company)\b/.test(label(els.account)) && /\b(user\s*name|username|email|user\s*id)\b/.test(label(els.username))
    && /^(log\s*in|sign\s*in)$/i.test((els.submit.textContent||els.submit.getAttribute('value')||'').trim());
})(MAPPING)'''
