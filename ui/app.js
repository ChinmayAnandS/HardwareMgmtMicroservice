'use strict';
// The session token lives only in memory: reloading the page signs the administrator out, and nothing
// credential-like is ever written to web storage.
let token = '', writable = false, screen = 'dashboard', page = 1, filters = {}, busy = false;
let pending = null; // Retain the identical body and request number after an uncertain network result.
const UNCERTAIN = [4, 13, 14]; // DEADLINE_EXCEEDED, INTERNAL, UNAVAILABLE: the change may or may not have happened
const $ = id => document.getElementById(id);
const node = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
function message(text) { $('message').textContent = text; }
function showSignedOut() { token = ''; $('app').hidden = true; $('login').hidden = false; $('signout').hidden = true; $('identity').textContent = ''; }
async function api(method, request = {}) {
  try { return await rpc.call(method, request, token ? {Authorization: 'Bearer ' + token} : {}); }
  catch (e) { if (e.code === 16 && token) showSignedOut(); throw e; }
}
async function mutate(method, body) {
  const signature = JSON.stringify([method, body]);
  if (!pending || pending.signature !== signature) pending = {signature, key:crypto.randomUUID()};
  try {
    const result = await api(method, {...body, request_id:pending.key});
    pending = null; return result;
  } catch (e) { if (!UNCERTAIN.includes(e.code)) pending = null; throw e; }
}
function button(label, action) { const b = node('button', label); b.type = 'button'; b.onclick = () => Promise.resolve(action()).catch(e => message(e.message)); return b; }
function field(name, label, value = '', type = 'text', required = true) {
  const l = node('label', label), i = node('input'); i.name = name; i.type = type; i.value = value;
  i.required = required; if (type === 'number') { i.step = '1'; i.min = '0'; i.max = '1000000'; }
  else i.maxLength = name === 'actor_id' ? 300 : name === 'reason' ? 1000 : name === 'description' ? 2000 : 160;
  if (name === 'reason') i.minLength = 3;
  if (['hardware_id','project_id','user_id'].includes(name)) { i.maxLength = 100; i.pattern = '[A-Za-z0-9_.:@-]{1,100}'; }
  l.append(i); return l;
}
function select(name, label, values, value='') {
  const l = node('label', label), i = node('select'); i.name = name;
  for (const v of values) { const o = node('option', v || 'All'); o.value = v; i.append(o); }
  i.value = value; l.append(i); return l;
}
function openEditor(title, current, fields, submit) {
  $('edit-title').textContent = title;
  $('current').replaceChildren(node('strong','Current values'), node('pre', JSON.stringify(current, null, 2)));
  $('fields').replaceChildren(...fields, field('reason','Reason (required)'));
  $('edit-form').onsubmit = async event => {
    event.preventDefault(); if (busy || !confirm('Confirm this administrative change? Current values are shown above.')) return;
    busy = true; $('save').disabled = true;
    try { await submit(Object.fromEntries(new FormData(event.target))); $('editor').close(); message('Change saved with audit record.'); await render(); }
    catch (e) { message(e.message); } finally { busy = false; $('save').disabled = false; }
  };
  $('editor').showModal();
}
function table(columns, rows, mapper) {
  const wrap=node('div'), t=node('table'), head=node('tr'), body=node('tbody'); wrap.className='table-wrap';
  for (const c of columns) head.append(node('th',c)); t.append(head);
  for (const row of rows) { const tr=node('tr'); for(const value of mapper(row)) {const td=node('td'); if(value instanceof Node) td.append(value); else td.textContent=String(value ?? ''); tr.append(td);} body.append(tr);}
  if (!rows.length) { const tr=node('tr'), td=node('td','No matching records.'); td.colSpan=columns.length; tr.append(td); body.append(tr); }
  t.append(body); wrap.append(t); $('screen').append(wrap);
}
function pager(data) {
  const p=node('p',`Page ${page} · ${data.total} records `), prev=button('Previous',()=>{page--;return render();}), next=button('Next',()=>{page++;return render();});
  prev.disabled=page===1; next.disabled=page*data.size>=data.total; p.append(prev,next); $('screen').append(p);
}
function searchToolbar(spec) {
  const f=node('form'); f.className='toolbar';
  for (const [name,label,type,values] of spec) f.append(type==='select'?select(name,label,values,filters[name]||''):field(name,label,filters[name]||'',type||'text',false));
  const s=node('button','Search / filter'); f.append(s);
  f.onsubmit=e=>{e.preventDefault(); filters=Object.fromEntries(new FormData(f));page=1;render().catch(e=>message(e.message));}; $('screen').append(f);
}
// Form values are strings; list requests need numbers, booleans and timestamps.
function listArgs() {
  const args = {page, size:25, ...filters};
  for (const key of Object.keys(args)) if (args[key] === '' || args[key] === undefined) delete args[key];
  if ('outstanding' in args) args.outstanding = args.outstanding === 'true';
  for (const key of ['start', 'end']) if (key in args) {
    args[key] = rpc.toTimestamp(args[key]);
    if (!args[key]) throw new Error(`${key} must be an ISO 8601 timestamp, e.g. 2026-01-31T00:00:00Z`);
  }
  return args;
}
function actions(...buttons) { const d=node('div'); d.append(...buttons); return d; }
function auditFor(hardware_id,project_id) { screen='transactions';page=1;filters={hardware_id,...(project_id?{project_id}:{})};return render(); }
async function render() {
  $('screen').replaceChildren(node('h2', {dashboard:'Inventory dashboard',hardware:'Hardware inventory',projects:'Project inventory',adjustments:'Inventory adjustments',checkouts:'Outstanding checkouts',transactions:'Immutable transaction history'}[screen]));
  if(screen==='dashboard') {
    const s=await api('GetSummary'), cards=node('div');cards.className='cards';
    const sum=k=>s.statuses.reduce((a,v)=>a+v[k],0), active=s.statuses.find(v=>v.status==='active');
    for(const [label,value] of [['Hardware records',sum('records')],['Total units',sum('total')],['Available active units',(active?.total||0)-(active?.out||0)],['Checked out units',sum('out')],['Damaged units',s.statuses.find(v=>v.status==='damaged')?.total||0],['Retired units',s.statuses.find(v=>v.status==='retired')?.total||0]]) {
      const card=node('div',label);card.className='card';card.append(node('strong',value));cards.append(card);
    }
    $('screen').append(cards,node('h3','Project allocation overview (first 100 project IDs)'));
    table(['Project ID','Allocated','Checked out'],s.projects,r=>[r.project_id,r.quantity,r.out]);return;
  }
  if(screen==='hardware'||screen==='adjustments') {
    searchToolbar([['q','Name / asset tag prefix'],['status','Status','select',['','active','damaged','under_maintenance','retired']],['sort','Sort by','select',['name_key','asset_key','status','total']]]);
    if(writable&&screen==='hardware') $('screen').append(button('Add hardware',()=>openEditor('Add hardware',{},[field('name','Name'),field('category','Category'),field('asset_tag','Unique asset tag'),field('serial','Serial','', 'text',false),field('description','Description','','text',false),field('total','Total units',1,'number')],v=>mutate('CreateHardware',{...v,total:Number(v.total)}))));
    const data=await api('ListHardware',listArgs());
    table(['Name / asset','Category / status','Total','Available / unallocated','Actions'],data.items,h=>{
      const links=[button('Audit',()=>auditFor(h.id)),button('Allocations',()=>{screen='projects';filters={hardware_id:h.id};page=1;return render();})];
      if(writable&&h.status!=='retired') {
        links.push(button('Edit',()=>openEditor('Edit hardware',h,[field('name','Name',h.name),field('category','Category',h.category),field('asset_tag','Asset tag',h.asset_tag),field('serial','Serial',h.serial,'text',false),field('description','Description',h.description,'text',false),select('status','Status',['active','damaged','under_maintenance'],h.status)],v=>mutate('UpdateHardware',{...v,hardware_id:h.id,expected_version:h.version}))));
        links.push(button('Adjust stock',()=>{const delta=field('delta','Change in total (negative to remove)',1,'number');delta.querySelector('input').min='-1000000';openEditor('Adjust total stock',h,[delta],v=>mutate('AdjustHardware',{...v,hardware_id:h.id,delta:Number(v.delta),expected_version:h.version,confirmed:true}));}));
        links.push(button('Retire',()=>openEditor('Retire hardware',h,[],v=>mutate('RetireHardware',{...v,hardware_id:h.id,expected_version:h.version,confirmed:true}))));
      }
      return [h.name+' / '+h.asset_tag,h.category+' / '+h.status,h.total,`${h.available} / ${h.unallocated}`,actions(...links)];
    });pager(data);return;
  }
  if(screen==='projects') {
    $('screen').append(node('p','Project metadata is owned by the project service. Only immutable external project IDs, reservations and outstanding quantities are stored here.'));
    searchToolbar([['project_id','External project ID'],['hardware_id','Hardware ID']]);
    const known=await api('GetSummary'), choices=node('datalist');choices.id='project-ids';
    for(const project of known.projects){const option=node('option');option.value=project.project_id;choices.append(option);}
    $('screen').append(choices);document.querySelector('[name=project_id]').setAttribute('list','project-ids');
    if(writable) $('screen').append(button('Set project allocation',()=>openEditor('Set allocation',{},[field('hardware_id','Hardware ID'),field('project_id','External project ID'),field('quantity','New allocated total',1,'number')],async v=>{const h=await api('GetHardware',{hardware_id:v.hardware_id});if(!confirm(`Current total: ${h.total}; reserved: ${h.reserved}; checked out: ${h.out}. Confirm new allocation ${v.quantity}?`))throw new Error('Change cancelled');return mutate('AllocateHardware',{hardware_id:v.hardware_id,project_id:v.project_id,quantity:Number(v.quantity),reason:v.reason,expected_version:h.version});})));
    const data=await api('ListAllocations',listArgs());
    table(['Project ID','Hardware ID','Allocated','Out','Actions'],data.items,a=>[a.project_id,a.hardware_id,a.quantity,a.out,actions(button('Audit',()=>auditFor(a.hardware_id,a.project_id)),...(writable?[button('Modify',async()=>{const h=await api('GetHardware',{hardware_id:a.hardware_id});openEditor('Modify allocation',{allocation:a,hardware:h},[field('quantity','New allocated total',a.quantity,'number')],v=>mutate('AllocateHardware',{...v,hardware_id:a.hardware_id,project_id:a.project_id,quantity:Number(v.quantity),expected_version:h.version}));})]:[]))]);pager(data);return;
  }
  if(screen==='checkouts') {
    searchToolbar([['project_id','Project ID'],['hardware_id','Hardware ID'],['user_id','Borrower ID'],['outstanding','Show','select',['true','false']]]);
    $('screen').append(node('p','Checkout creation belongs to the integrating service. Administrators can process returns and correct recorded return quantities here. Set Show=false to include fully returned checkouts.'));
    const data=await api('ListCheckouts',listArgs());
    table(['Checkout / hardware ID','Project / borrower ID','Quantity / returned','Actions'],data.items,c=>[c.id+' / '+c.hardware_id,c.project_id+' / '+c.user_id,`${c.quantity} / ${c.returned}`,actions(button('Audit',()=>auditFor(c.hardware_id,c.project_id)),...(writable?[button('Return',()=>openEditor('Process partial or full return',c,[field('quantity','Quantity to return',c.outstanding,'number')],v=>mutate('ReturnCheckout',{...v,checkout_id:c.id,quantity:Number(v.quantity),expected_version:c.version}))),button('Correct return',()=>openEditor('Correct cumulative returned quantity',c,[field('returned','Correct cumulative quantity returned',c.returned,'number')],v=>mutate('CorrectCheckout',{...v,checkout_id:c.id,returned:Number(v.returned),expected_version:c.version,confirmed:true})))]:[]))]);pager(data);return;
  }
  searchToolbar([['project_id','Project ID'],['hardware_id','Hardware ID'],['actor_id','Administrator actor ID'],['user_id','Borrower ID'],['start','Start (ISO 8601 with timezone)'],['end','End (ISO 8601 with timezone)']]);
  const data=await api('ListTransactions',listArgs());
  table(['Timestamp','Actor / operation','Entity / project','Reason / changes'],data.items,a=>{const d=node('details');d.append(node('summary',a.reason),node('pre',JSON.stringify({before:a.before,after:a.after},null,2)));return [rpc.toIso(a.timestamp),a.actor+' / '+a.operation,a.entity+' / '+(a.project_id||''),d];});pager(data);
}
async function initialize() {
  const me=await api('Me');writable=me.roles.includes('Hardware.Admin');
  $('identity').textContent=me.name; $('signout').hidden=false;$('login').hidden=true;$('app').hidden=false;await render();
}
$('cancel').onclick=()=>$('editor').close();
$('signout').onclick=async()=>{try{await api('Logout');location.assign('/');}catch(e){message(e.message);}};
for(const b of document.querySelectorAll('[data-screen]'))b.onclick=()=>{screen=b.dataset.screen;page=1;filters={};render().catch(e=>message(e.message));};
async function showSignIn(){
  const m=await rpc.call('GetAuthMethods').catch(()=>({}));
  $('password-login').hidden=!m.password;
  $('local-login').hidden=!(m.local||new URLSearchParams(location.search).has('local'));
}
async function signIn(method, request) {
  try { token=(await api(method,request)).token; message(''); await initialize(); }
  catch(err) { token=''; message(err.message); }
}
$('password-login').onsubmit=e=>{e.preventDefault();const f=e.target.elements;const request={name:f.name.value.trim(),code:f.password.value};f.password.value='';return signIn('Login',request);};
$('local-login').onsubmit=e=>{e.preventDefault();const input=e.target.elements.code;const code=input.value;input.value='';return signIn('DevLogin',{code});};
showSignIn();
