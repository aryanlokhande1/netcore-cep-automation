// Read-only observation fixture; mutation APIs throw rather than emulate writes.
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
global.CSS={escape:s=>s.replace(/[^a-zA-Z0-9_-]/g,c=>'\\'+c)};
global.getComputedStyle=e=>({visibility:e.attrs.hidden?'hidden':'visible'});
let measured=null;
global.window={innerWidth:800,innerHeight:600};
class Element{
 constructor(d,parent=null){this.tagName=(d.tag||'div').toUpperCase();this.attrs={...(d.attrs||{})};this.text=d.text||'';this.parentElement=parent;this.labels=[];this.children=(d.children||[]).map(c=>new Element(c,this));this.shadowRoot=d.shadow?{}:null;}
 get id(){return this.attrs.id||'';}
 get type(){return this.attrs.type||(this.tagName==='INPUT'?'text':this.tagName==='SELECT'?'select-one':this.tagName==='BUTTON'?'submit':undefined);}
 get disabled(){return !!this.attrs.disabled;}
 get readOnly(){return !!this.attrs.readonly;}
 get multiple(){return !!this.attrs.multiple;}
 get classList(){return (this.attrs.class||'').split(' ').filter(Boolean);}
 get firstElementChild(){return this.children[0]||null;}
 get nextElementSibling(){if(!this.parentElement)return null;return this.parentElement.children[this.parentElement.children.indexOf(this)+1]||null;}
 get textContent(){return this.text+this.children.map(e=>e.textContent).join('');}
 get innerText(){return this.text+this.children.filter(e=>!e.attrs.hidden).map(e=>e.innerText).join('');}
 get options(){return this.descendants().filter(e=>e.tagName==='OPTION');}
 get selectedOptions(){return this.options.filter(e=>e.attrs.selected);}
 get value(){return this.tagName==='SELECT'?(this.selectedOptions[0]?.value||''):(this.attrs.value??(this.tagName==='OPTION'?this.textContent:''));}
 get form(){return document.getElementById(this.attrs.form)||this.closest('form');}
 getAttribute(k){return this.attrs[k]??null;}
 getClientRects(){for(let n=this;n;n=n.parentElement)if(n.attrs.hidden)return [];return [{}];}
 getBoundingClientRect(){measured=this;return {left:10,top:this.attrs.offscreen?2000:10,width:100,height:20};}
 descendants(){const out=[];const walk=e=>{for(const c of e.children){out.push(c);walk(c);}};walk(this);return out;}
 contains(other){for(let n=other;n;n=n.parentElement)if(n===this)return true;return false;}
 matches(selectors){return selectors.split(',').some(s=>{
  s=s.trim();if(s===':disabled')return this.disabled||!!this.closest('fieldset[disabled]');
  if(s.startsWith('#'))return this.id===s.slice(1).replace(/\\(.)/g,'$1');
  const nth=s.match(/^([a-z]+):nth-of-type\((\d+)\)$/i);
  if(nth){const siblings=this.parentElement?.children.filter(e=>e.tagName===this.tagName)||[this];return this.tagName.toLowerCase()===nth[1]&&siblings.indexOf(this)+1===Number(nth[2]);}
  const attr=s.match(/^([a-z]*)\[([^=\]]+)(?:=([^\]]+))?\]$/i);
  if(attr)return (!attr[1]||this.tagName.toLowerCase()===attr[1])&&this.getAttribute(attr[2])!==null&&(attr[3]===undefined||this.getAttribute(attr[2])===attr[3].replace(/^"|"$/g,''));
  return this.tagName.toLowerCase()===s.toLowerCase();
 });}
 closest(s){for(let n=this;n;n=n.parentElement)if(n.matches(s))return n;return null;}
 setAttribute(){throw Error('OBSERVATION_MUTATED_ATTRIBUTE');}
 addEventListener(){throw Error('OBSERVATION_INSTALLED_LISTENER');}
}
const html=new Element({tag:'html',children:[{tag:'body',children:input.children}]});
const all=[html,...html.descendants()];
global.document={documentElement:html,body:html.children[0],
 querySelectorAll:s=>all.filter(e=>s.split(',').some(part=>{
  const pieces=part.trim().split(/\s*>\s*/);let node=e;
  for(let i=pieces.length-1;i>=0;i--){if(!node||!node.matches(pieces[i]))return false;node=node.parentElement;}
  return true;
 })),getElementById:id=>all.find(e=>e.id===id)||null,
 addEventListener(){throw Error('OBSERVATION_INSTALLED_LISTENER');}};
if(input.geometry!==false)document.elementFromPoint=()=>measured.attrs.covered?new Element({tag:'div'}):measured;
for(const l of all.filter(e=>e.tagName==='LABEL')){const c=document.getElementById(l.attrs.for);if(c){l.control=c;c.labels.push(l);}}
const tree=e=>({tag:e.tagName,attrs:e.attrs,text:e.text,children:e.children.map(tree)});
const before=JSON.stringify(tree(html));
const snapshot=eval(input.expression);
process.stdout.write(JSON.stringify({snapshot,unchanged:before===JSON.stringify(tree(html))}));
