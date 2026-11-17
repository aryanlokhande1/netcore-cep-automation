// Execute the actual Setup scripts in a bounded DOM double. No browser or network.
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
global.getComputedStyle=e=>({visibility:e.attrs.hidden?'hidden':'visible'});
class Element {
 constructor(d,parent=null){this.tagName=(d.tag||'div').toUpperCase();this.attrs=d.attrs||{};this.text=d.text||'';this.parentElement=parent;this.labels=[];this.children=(d.children||[]).map(c=>new Element(c,this));}
 get id(){return this.attrs.id||'';}
 get type(){return this.attrs.type||(this.tagName==='INPUT'?'text':this.tagName==='SELECT'?'select-one':this.tagName==='BUTTON'?'submit':undefined);}
 get disabled(){return !!this.attrs.disabled;}
 get readOnly(){return !!this.attrs.readonly;}
 get multiple(){return !!this.attrs.multiple;}
 get classList(){return (this.attrs.class||'').split(' ').filter(Boolean);}
 get textContent(){return this.text+this.children.map(e=>e.textContent).join('');}
 get innerText(){return this.textContent;}
 get options(){return this.querySelectorAll('option');}
 get selectedOptions(){return this.options.filter(e=>e.attrs.selected);}
 get value(){return this.tagName==='SELECT'?(this.selectedOptions[0]?.value||''):(this.attrs.value??(this.tagName==='OPTION'?this.textContent:''));}
 getAttribute(k){return this.attrs[k]??null;}
 setAttribute(k,v){this.attrs[k]=v;}
 getClientRects(){return this.attrs.hidden?[]:[{}];}
 matches(selectors){return selectors.split(',').some(s=>{
   s=s.trim();const m=s.match(/^([a-z]*)\[([^=\]]+)(?:=([^\]]+))?\]$/i);
   if(m)return (!m[1]||this.tagName.toLowerCase()===m[1])&&this.getAttribute(m[2])!==null&&(m[3]===undefined||this.getAttribute(m[2])===m[3].replace(/^"|"$/g,''));
   return this.tagName.toLowerCase()===s.toLowerCase();
 });}
 closest(s){for(let n=this;n;n=n.parentElement)if(n.matches(s))return n;return null;}
 querySelectorAll(s){const a=[];const walk=e=>{for(const c of e.children){a.push(c);walk(c);}};walk(this);return a.filter(e=>e.matches(s));}
}
const body=new Element({tag:'body',children:input.children});
const handlers={};
global.document={body,querySelectorAll:s=>body.querySelectorAll(s),getElementById:id=>[body,...body.querySelectorAll('input,select,option,label,form,div,span,li,h1,h2,button,a')].find(e=>e.id===id)||null,
 addEventListener:(name,callback)=>{(handlers[name]??=[]).push(callback);}};
for(const l of document.querySelectorAll('label')){const c=document.getElementById(l.getAttribute('for'));if(c){l.control=c;c.labels.push(l);}}
global.window=input.installed?{__netcoreSetupGuard:{attempts:0}}:{};
const result=eval(input.expression);
if(input.events){
 const denied=[];
 for(const data of input.events){const event={target:document.getElementById(data.id),key:data.key,
   preventDefault(){this.denied=true;},stopImmediatePropagation(){}};
   for(const handler of handlers[data.type]||[])handler(event);
   denied.push(!!event.denied);
 }
 process.stdout.write(JSON.stringify({result,denied,attempts:window.__netcoreSetupGuard.attempts}));
}else process.stdout.write(JSON.stringify(result));
