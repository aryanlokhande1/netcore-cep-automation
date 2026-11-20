// Deterministic DOM double for executing the actual evidence expressions in Node.
// No Chromium, network, credentials or model access.
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
global.CSS={escape:s=>s.replace(/[^a-zA-Z0-9_-]/g,c=>"\\"+c)};
global.getComputedStyle=e=>({visibility:e.hidden?'hidden':'visible'});
class Element {
 constructor(data,parent=null){
  this.tagName=(data.tag||'div').toUpperCase();this.localName=this.tagName.toLowerCase();this.parentElement=parent;
  this.attrs=data.attrs||{};this.id=this.attrs.id||'';this.hidden=!!data.hidden;this.text=data.text||'';
  this.children=(data.children||[]).map(d=>new Element(d,this));
  this.children.forEach(e=>e.siblings=this.children);
  this.shadowRoot=data.shadow?new ShadowRoot(data.shadowChildren||[]):null;
  this.contentDocument=data.document?new Document(data.document):null;
 }
 get firstElementChild(){return this.children[0]||null;}
 get nextElementSibling(){const a=this.siblings||[];return a[a.indexOf(this)+1]||null;}
 get textContent(){return this.text+this.children.map(e=>e.textContent).join('');}
 getAttribute(k){return this.attrs[k]??null;}
 hasAttribute(k){return k in this.attrs;}
 getClientRects(){return this.hidden?[]:[{x:0,y:0,width:100,height:20,right:100,bottom:20}];}
 getBoundingClientRect(){return this.getClientRects()[0]||{x:0,y:0,width:0,height:0,right:0,bottom:0};}
 querySelectorAll(s){return Document.prototype.querySelectorAll.call({body:this},s);}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 matches(selectors){return selectors.split(',').some(s=>{
  s=s.trim();
  if(s.startsWith('#'))return this.id===s.slice(1).replace(/\\(.)/g,'$1');
  const nth=s.match(/^([a-z]+):nth-of-type\((\d+)\)$/i);
  if(nth)return this.tagName.toLowerCase()===nth[1]&&this.parentElement?.children.filter(e=>e.tagName===this.tagName).indexOf(this)+1===Number(nth[2]);
  const attr=s.match(/^([a-z]*)\[([^=\]*]+)(\*=|=)([^\]]+)\]$/i);
  if(attr){
   const value=attr[4].replace(/^"|"$/g,'');const actual=this.getAttribute(attr[2]);
   return (!attr[1]||this.tagName.toLowerCase()===attr[1])&&actual!==null&&(attr[3]==='*='?actual.includes(value):actual===value);
  }
  if(s.startsWith('.'))return (this.attrs.class||'').split(' ').includes(s.slice(1));
  return this.tagName.toLowerCase()===s.toLowerCase();
 });}
 closest(s){for(let e=this;e;e=e.parentElement)if(e.matches(s))return e;return null;}
}
class ShadowRoot {
 constructor(children){this.mode='open';this.children=children.map(d=>new Element(d));this.children.forEach(e=>e.siblings=this.children);}
 get firstElementChild(){return this.children[0]||null;}
 querySelectorAll(s){return Document.prototype.querySelectorAll.call({body:this},s);}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
class Document {
 constructor(data){this.URL=data.url;this.body=new Element({tag:'body',children:data.children||[]});this.documentElement=this.body;}
 querySelectorAll(s){
  const all=[];const walk=e=>{for(const c of e.children){all.push(c);walk(c);}};walk(this.body);
  if(s==='body *')return all;
  if(s.includes(' > ')){
   const parts=s.split(' > ');return all.filter(e=>{let n=e;for(let i=parts.length-1;i>=0;i--){if(!n||!n.matches(parts[i]))return false;n=n.parentElement;}return true;});
  }
  return all.filter(e=>e.matches(s));
 }
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
}
global.document=new Document(input.document);
process.stdout.write(JSON.stringify(eval(input.expression)));
