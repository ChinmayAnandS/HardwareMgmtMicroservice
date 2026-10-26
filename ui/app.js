'use strict';
let csrf = '', writable = false, screen = 'dashboard', page = 1, filters = {}, busy = false;
let pending = null; // Retain the identical body and key after an uncertain network result.
const $ = id => document.getElementById(id);
const node = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
function message(text) { $('message').textContent = text; }
async function api(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', ...options,
    headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...options.headers}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) { $('app').hidden = true; $('login').hidden = false; }
    const error = new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
    error.status = response.status; throw error;
  }
  return data;
}
async function mutate(path, method, body) {
  const signature = JSON.stringify([path, method, body]);
  if (!pending || pending.signature !== signature) pending = {signature, key:crypto.randomUUID()};
  try {
    const result = await api(path, {method, body:JSON.stringify(body), headers:{'Idempotency-Key':pending.key}});
    pending = null; return result;
  } catch (e) { if (e.status && e.status < 500) pending = null; throw e; }
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
function qs(extra={}) { const q=new URLSearchParams({page,size:25,...filters,...extra}); for (const [k,v] of [...q]) if(!v) q.delete(k); return q; }
function actions(...buttons) { const d=node('div'); d.append(...buttons); return d; }
function auditFor(hardware_id,project_id) { screen='transactions';page=1;filters={hardware_id,...(project_id?{project_id}:{})};return render(); }
async function render() {
  $('screen').replaceChildren(node('h2', {dashboard:'Inventory dashboard',hardware:'Hardware inventory',projects:'Project inventory',adjustments:'Inventory adjustments',checkouts:'Outstanding checkouts',transactions:'Immutable transaction history'}[screen]));
  if(screen==='dashboard') {
    const s=await api('/api/summary'), cards=node('div');cards.className='cards';
    const sum=k=>s.statuses.reduce((a,v)=>a+v[k],0), active=s.statuses.find(v=>v._id==='active');
    for(const [label,value] of [['Hardware records',sum('records')],['Total units',sum('total')],['Available active units',(active?.total||0)-(active?.out||0)],['Checked out units',sum('out')],['Damaged units',s.statuses.find(v=>v._id==='damaged')?.total||0],['Retired units',s.statuses.find(v=>v._id==='retired')?.total||0]]) {
      const card=node('div',label);card.className='card';card.append(node('strong',value));cards.append(card);
    }
    $('screen').append(cards,node('h3','Project allocation overview (first 100 project IDs)'));
    table(['Project ID','Allocated','Checked out'],s.projects,r=>[r._id,r.quantity,r.out]);return;
  }
  if(screen==='hardware'||screen==='adjustments') {
    searchToolbar([['q','Name / asset tag prefix'],['status','Status','select',['','active','damaged','under_maintenance','retired']],['sort','Sort by','select',['name_key','asset_key','status','total']]]);
    if(writable&&screen==='hardware') $('screen').append(button('Add hardware',()=>openEditor('Add hardware',{},[field('name','Name'),field('category','Category'),field('asset_tag','Unique asset tag'),field('serial','Serial','', 'text',false),field('description','Description','','text',false),field('total','Total units',1,'number')],v=>mutate('/api/hardware','POST',{...v,total:Number(v.total)}))));
    const data=await api('/api/hardware?'+qs());
    table(['Name / asset','Category / status','Total','Available / unallocated','Actions'],data.items,h=>{
      const links=[button('Audit',()=>auditFor(h._id)),button('Allocations',()=>{screen='projects';filters={hardware_id:h._id};page=1;return render();})];
      if(writable&&h.status!=='retired') {
        links.push(button('Edit',()=>openEditor('Edit hardware',h,[field('name','Name',h.name),field('category','Category',h.category),field('asset_tag','Asset tag',h.asset_tag),field('serial','Serial',h.serial,'text',false),field('description','Description',h.description,'text',false),select('status','Status',['active','damaged','under_maintenance'],h.status)],v=>mutate('/api/hardware/'+h._id,'PATCH',{...v,expected_version:h.version}))));
        links.push(button('Adjust stock',()=>{const delta=field('delta','Change in total (negative to remove)',1,'number');delta.querySelector('input').min='-1000000';openEditor('Adjust total stock',h,[delta],v=>mutate(`/api/hardware/${h._id}/adjustments`,'POST',{...v,delta:Number(v.delta),expected_version:h.version,confirmed:true}));}));
        links.push(button('Retire',()=>openEditor('Retire hardware',h,[],v=>mutate(`/api/hardware/${h._id}/retire`,'POST',{...v,expected_version:h.version,confirmed:true}))));
      }
      return [h.name+' / '+h.asset_tag,h.category+' / '+h.status,h.total,`${h.total-h.out} / ${h.total-h.reserved}`,actions(...links)];
    });pager(data);return;
  }
  if(screen==='projects') {
    $('screen').append(node('p','Project metadata is owned by the project service. Only immutable external project IDs, reservations and outstanding quantities are stored here.'));
    searchToolbar([['project_id','External project ID'],['hardware_id','Hardware ID']]);
    const known=await api('/api/summary'), choices=node('datalist');choices.id='project-ids';
    for(const project of known.projects){const option=node('option');option.value=project._id;choices.append(option);}
    $('screen').append(choices);document.querySelector('[name=project_id]').setAttribute('list','project-ids');
    if(writable) $('screen').append(button('Set project allocation',()=>openEditor('Set allocation',{},[field('hardware_id','Hardware ID'),field('project_id','External project ID'),field('quantity','New allocated total',1,'number')],async v=>{const h=await api('/api/hardware/'+encodeURIComponent(v.hardware_id));if(!confirm(`Current total: ${h.total}; reserved: ${h.reserved}; checked out: ${h.out}. Confirm new allocation ${v.quantity}?`))throw new Error('Change cancelled');return mutate(`/api/hardware/${encodeURIComponent(v.hardware_id)}/allocations/${encodeURIComponent(v.project_id)}`,'PUT',{quantity:Number(v.quantity),reason:v.reason,expected_version:h.version});})));
    const data=await api('/api/allocations?'+qs());
    table(['Project ID','Hardware ID','Allocated','Out','Actions'],data.items,a=>[a.project_id,a.hardware_id,a.quantity,a.out,actions(button('Audit',()=>auditFor(a.hardware_id,a.project_id)),...(writable?[button('Modify',async()=>{const h=await api('/api/hardware/'+encodeURIComponent(a.hardware_id));openEditor('Modify allocation',{allocation:a,hardware:h},[field('quantity','New allocated total',a.quantity,'number')],v=>mutate(`/api/hardware/${a.hardware_id}/allocations/${encodeURIComponent(a.project_id)}`,'PUT',{...v,quantity:Number(v.quantity),expected_version:h.version}));})]:[]))]);pager(data);return;
  }
  if(screen==='checkouts') {
    searchToolbar([['project_id','Project ID'],['hardware_id','Hardware ID'],['user_id','Borrower ID'],['outstanding','Show','select',['true','false']]]);
    $('screen').append(node('p','Checkout creation belongs to the integrating service. Administrators can process returns and correct recorded return quantities here. Set Show=false to include fully returned checkouts.'));
    const data=await api('/api/checkouts?'+qs());
    table(['Checkout / hardware ID','Project / borrower ID','Quantity / returned','Actions'],data.items,c=>[c._id+' / '+c.hardware_id,c.project_id+' / '+c.user_id,`${c.quantity} / ${c.returned}`,actions(button('Audit',()=>auditFor(c.hardware_id,c.project_id)),...(writable?[button('Return',()=>openEditor('Process partial or full return',c,[field('quantity','Quantity to return',c.quantity-c.returned,'number')],v=>mutate(`/api/checkouts/${c._id}/returns`,'POST',{...v,quantity:Number(v.quantity),expected_version:c.version}))),button('Correct return',()=>openEditor('Correct cumulative returned quantity',c,[field('returned','Correct cumulative quantity returned',c.returned,'number')],v=>mutate(`/api/checkouts/${c._id}/corrections`,'POST',{...v,returned:Number(v.returned),expected_version:c.version,confirmed:true})))]:[]))]);pager(data);return;
  }
  searchToolbar([['project_id','Project ID'],['hardware_id','Hardware ID'],['actor_id','Administrator actor ID'],['user_id','Borrower ID'],['start','Start (ISO 8601 with timezone)'],['end','End (ISO 8601 with timezone)']]);
  const data=await api('/api/transactions?'+qs());
  table(['Timestamp','Actor / operation','Entity / project','Reason / changes'],data.items,a=>{const d=node('details');d.append(node('summary',a.reason),node('pre',JSON.stringify({before:a.before,after:a.after},null,2)));return [a.timestamp,a.actor+' / '+a.operation,a.entity+' / '+(a.project_id||''),d];});pager(data);
}
async function initialize() {
  const me=await api('/api/me');csrf=me.csrf;writable=me.roles.includes('Hardware.Admin');
  $('identity').textContent=me.name; $('signout').hidden=false;$('login').hidden=true;$('app').hidden=false;await render();
}
$('cancel').onclick=()=>$('editor').close();
$('signout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'});location.assign('/');}catch(e){message(e.message);}};
for(const b of document.querySelectorAll('[data-screen]'))b.onclick=()=>{screen=b.dataset.screen;page=1;filters={};render().catch(e=>message(e.message));};
$('local-login').hidden=!new URLSearchParams(location.search).has('local');
$('local-login').onsubmit=async e=>{e.preventDefault();const input=e.target.elements.code;const code=input.value;input.value='';try{await api('/auth/dev',{method:'POST',body:JSON.stringify({code})});history.replaceState({},'','/');await initialize();}catch(err){message(err.message);}};
initialize().catch(e=>{if(e.status!==401)message(e.message);});
