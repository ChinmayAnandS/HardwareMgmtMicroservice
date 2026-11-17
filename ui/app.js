'use strict';
// The session token lives only in memory: reloading the page signs the administrator out, and nothing
// credential-like is ever written to web storage.
let token = '', writable = false, screen = 'dashboard', page = 1, filters = {}, busy = false, qrUrl = '';
let pending = null; // Retain the identical body and request number after an uncertain network result.
const UNCERTAIN = [4, 13, 14]; // DEADLINE_EXCEEDED, INTERNAL, UNAVAILABLE: the change may or may not have happened
const $ = id => document.getElementById(id);
const node = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
let toastTimer = 0;
function message(text, ok = false) {
  const m = $('message'); m.textContent = text; m.classList.toggle('ok', ok);
  clearTimeout(toastTimer); if (text) toastTimer = setTimeout(() => { m.textContent = ''; }, 7000);
}
const nf = new Intl.NumberFormat();
const ICONS = {
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  wrench: '<path d="M21 7.5a5 5 0 0 1-6.6 4.7L7 19.6a2 2 0 0 1-2.8-2.8l7.4-7.4A5 5 0 0 1 16.5 3l-3 3 .5 3 3 .5z"/>',
  alert: '<path d="M12 4 3.5 19h17z"/><path d="M12 10v4M12 16.8v.1"/>',
  archive: '<rect x="3.5" y="4.5" width="17" height="4" rx="1"/><path d="M5 8.5V18a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 18V8.5M10 12.5h4"/>',
  inbox: '<path d="M4 13.5 6.5 5h11L20 13.5v4a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z"/><path d="M4 13.5h4.5l1 2h5l1-2H20"/>',
  out: '<path d="M5 12h14m0 0-5-5m5 5-5 5"/>',
  box: '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
  layers: '<path d="m12 3.5 9 4.5-9 4.5L3 8z"/><path d="m3 12.5 9 4.5 9-4.5M3 16.5l9 4.5 9-4.5"/>',
};
// Static strings only: icons never include data from the server.
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.innerHTML = ICONS[name] || '';
  return svg;
}
const STATUS_ICON = {active: 'check', under_maintenance: 'wrench', damaged: 'alert', retired: 'archive'};
// A status is never colour alone: each pill carries an icon and its label.
function pill(status) {
  const p = node('span'); p.className = 'pill pill-' + status;
  p.append(icon(STATUS_ICON[status] || 'check'), document.createTextNode(status.replace(/_/g, ' '))); return p;
}
function stack(main, sub) { const d = node('div'); d.className = 'stack'; d.append(node('span', main)); if (sub instanceof Node) d.append(sub); else if (sub) d.append(node('small', sub)); return d; }
function meter(fraction) {
  const track = node('div'), fill = node('div'); track.className = 'meter'; track.setAttribute('aria-hidden', 'true');
  fill.style.width = Math.max(0, Math.min(1, fraction || 0)) * 100 + '%'; track.append(fill); return track;
}
// The number stays the cell's text; the bar is decoration for a quick read.
function cellMeter(text, fraction) { const d = node('div'); d.className = 'cell-meter'; d.append(node('span', text), meter(fraction)); return d; }
const mono = d => { for (const n of d.children) n.classList.add('mono'); return d; };
const primary = b => { b.classList.add('primary'); return b; };
const danger = b => { b.classList.add('danger'); return b; };
const tipEl = $('tip');
function showTip(t) {
  tipEl.textContent = t.dataset.tip; tipEl.hidden = false;
  const r = t.getBoundingClientRect(), w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  tipEl.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + 'px';
  tipEl.style.top = (r.top - h - 8 > 8 ? r.top - h - 8 : r.bottom + 8) + 'px';
}
for (const on of ['mouseover', 'focusin']) document.addEventListener(on, e => { const t = e.target.closest?.('[data-tip]'); if (t) showTip(t); });
for (const off of ['mouseout', 'focusout']) document.addEventListener(off, e => { if (e.target.closest?.('[data-tip]')) tipEl.hidden = true; });
function showSignedOut() { token = ''; $('app').hidden = true; $('login').hidden = false; $('signout').hidden = true; $('identity').textContent = ''; $('identity').removeAttribute('data-initial'); }
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
  if (['hardware_id','project_id','user_id'].includes(name)) { i.maxLength = 100; i.pattern = '[A-Za-z0-9_.:@\\-]{1,100}'; }
  l.append(i); return l;
}
const OPTION_LABELS = {name_key: 'Name', asset_key: 'Asset tag', status: 'Status', total: 'Total units', under_maintenance: 'Under maintenance', true: 'Outstanding only', false: 'Include returned'};
function select(name, label, values, value='') {
  const l = node('label', label), i = node('select'); i.name = name;
  for (const v of values) { const o = node('option', OPTION_LABELS[v] || v || 'All'); o.value = v; i.append(o); }
  i.value = value; if (i.selectedIndex < 0) i.selectedIndex = 0; l.append(i); return l;
}
function openEditor(title, current, fields, submit) {
  $('edit-title').textContent = title;
  $('current').replaceChildren(node('strong','Current values'), node('pre', JSON.stringify(current, null, 2)));
  $('fields').replaceChildren(...fields, field('reason','Reason (required)'));
  $('edit-form').onsubmit = async event => {
    event.preventDefault(); if (busy || !confirm('Confirm this administrative change? Current values are shown above.')) return;
    busy = true; $('save').disabled = true;
    try { await submit(Object.fromEntries(new FormData(event.target))); $('editor').close(); message('Change saved with audit record.', true); await render(); }
    catch (e) { message(e.message); } finally { busy = false; $('save').disabled = false; }
  };
  $('editor').showModal();
}
function table(columns, rows, mapper, host = $('screen')) {
  const wrap = node('div'), t = node('table'), thead = node('thead'), head = node('tr'), body = node('tbody'); wrap.className = 'table-wrap';
  for (const c of columns) head.append(node('th', c)); thead.append(head); t.append(thead);
  for (const row of rows) { const tr = node('tr'); for (const value of mapper(row)) { const td = node('td'); if (value instanceof Node) td.append(value); else td.textContent = String(value ?? ''); tr.append(td); } body.append(tr); }
  if (!rows.length) { const tr = node('tr'), td = node('td'); td.className = 'empty'; td.colSpan = columns.length; td.append(icon('inbox'), node('span', 'No matching records.')); tr.append(td); body.append(tr); }
  t.append(body); wrap.append(t); host.append(wrap);
}
function pager(data) {
  const d = node('div'), label = node('span', `Page ${page} · ${data.total} records`);
  const prev = button('Previous', () => { page--; return render(); }), next = button('Next', () => { page++; return render(); });
  prev.disabled = page === 1; next.disabled = page * data.size >= data.total; d.className = 'pager'; d.append(label, prev, next); $('screen').append(d);
}
function searchToolbar(spec) {
  const f=node('form'); f.className='toolbar';
  for (const [name,label,type,values] of spec) f.append(type==='select'?select(name,label,values,filters[name]||''):field(name,label,filters[name]||'',type||'text',false));
  const s=node('button','Search / filter'); s.className='primary'; f.append(s);
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
function actions(...buttons) { const d=node('div'); d.className='row-actions'; d.append(...buttons); return d; }
function checkoutEditor(prefill = {}) {
  return openEditor('Check out stock', prefill, [field('hardware_id','Hardware ID',prefill.hardware_id||''),field('project_id','External project ID',prefill.project_id||''),field('user_id','Borrower ID'),field('quantity','Quantity to check out',1,'number')],v=>mutate('CreateCheckout',{...v,quantity:Number(v.quantity)}));
}
function auditFor(hardware_id,project_id) { screen='transactions';page=1;filters={hardware_id,...(project_id?{project_id}:{})};return render(); }
function showQr(h) {
  return api('GetHardwareQr',{hardware_id:h.id}).then(qr=>{
    if(qrUrl) URL.revokeObjectURL(qrUrl);
    qrUrl=URL.createObjectURL(new Blob([qr.png],{type:qr.content_type||'image/png'}));
    $('qr-title').textContent=h.name+' / '+h.asset_tag;
    $('qr-image').src=qrUrl;
    $('qr-payload').textContent=qr.payload;
    $('qr').showModal();
  });
}
// A screen load fetches data, then fills the page. If another load starts meanwhile (a quick click on a tab,
// then a search), the older one must not append its table under the newer one when its reply arrives late:
// it stops at its next data fetch once it has been superseded.
const STALE=Symbol('stale');
let renderSeq=0;
function tile(label, value, {hero = false, glyph = '', sub = ''} = {}) {
  const t = node('div'), l = node('span'); t.className = 'tile' + (hero ? ' hero' : ''); l.className = 'label';
  if (glyph) l.append(icon(glyph)); l.append(document.createTextNode(label));
  t.append(l, node('strong', nf.format(value))); if (sub) t.append(node('small', sub)); return t;
}
function emptyNote(text) { const d = node('div', text); d.className = 'card-empty'; return d; }
function screenChip(label, tone = 'neutral') {
  const c = node('span', label); c.className = 'screen-chip ' + tone; return c;
}
function screenMeta(...chips) {
  const row = node('div'); row.className = 'screen-meta'; row.append(...chips); return row;
}
function dashboard(s) {
  const by = name => s.statuses.find(v => v.status === name) || {records: 0, total: 0, out: 0, reserved: 0};
  const sum = k => s.statuses.reduce((a, v) => a + v[k], 0), active = by('active');
  const out = active.out, reservedShelf = Math.max(0, active.reserved - active.out), free = Math.max(0, active.total - active.reserved);
  const host = $('screen');
  host.append(screenMeta(
    screenChip(`${nf.format(s.projects.length)} project allocations`, 'accent'),
    screenChip(`${nf.format(active.total)} active units`, 'good'),
    screenChip(`${nf.format(sum('out'))} checked out`, 'neutral')
  ));
  const kpis = node('div'); kpis.className = 'kpis';
  kpis.append(
    tile('Available active units', active.total - active.out, {hero: true, sub: `of ${nf.format(active.total)} active units; the rest are checked out`}),
    tile('Hardware records', sum('records'), {glyph: 'box'}), tile('Total units', sum('total'), {glyph: 'layers'}),
    tile('Checked out units', sum('out'), {glyph: 'out'}), tile('In maintenance', by('under_maintenance').total, {glyph: 'wrench'}),
    tile('Damaged units', by('damaged').total, {glyph: 'alert'}), tile('Retired units', by('retired').total, {glyph: 'archive'}));
  const grid = node('div'); grid.className = 'grid-2';

  // Active stock by use: an ordered part-to-whole, so one blue ramp (darker = more committed).
  const use = node('div'); use.className = 'card';
  use.append(node('h3', 'Active stock by use'), node('p', `${nf.format(active.total)} units in ${nf.format(active.records)} active pools`));
  if (!active.total) use.append(emptyNote('No active stock yet.'));
  else {
    const parts = [['Checked out', out, 3, 'Units out with a borrower right now'], ['Reserved, on the shelf', reservedShelf, 2, 'Allocated to a project but not checked out'], ['Free to allocate', free, 1, 'Not allocated to any project']];
    const bar = node('div'), legend = node('ul'); bar.className = 'stackbar'; legend.className = 'legend'; bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', parts.map(([n, v]) => `${n} ${v}`).join(', '));
    for (const [name, value, tone, hint] of parts) {
      const pct = Math.round(value / active.total * 100);
      if (value) {
        const seg = node('div'), text = node('span', nf.format(value)); seg.style.flex = String(value); seg.dataset.tone = tone; seg.tabIndex = 0;
        seg.dataset.tip = `${name}: ${nf.format(value)} units (${pct}%). ${hint}`; seg.append(text); bar.append(seg);
        // A label that does not fit is dropped, never clipped: the legend carries the value.
        requestAnimationFrame(() => { if (text.offsetWidth + 14 > seg.clientWidth) text.remove(); });
      }
      const li = node('li'), sw = node('i'); sw.style.background = `var(--seq-${tone})`;
      li.append(sw, node('span', name), node('b', nf.format(value)), node('span', pct + '%')); legend.append(li);
    }
    use.append(bar, legend);
  }

  // Project allocations: each bar is the project's allocation, filled by how much is checked out.
  const proj = node('div'); proj.className = 'card';
  proj.append(node('h3', 'Project allocations'), node('p', 'Units reserved for each project and how many are checked out'));
  const top = [...s.projects].sort((a, b) => b.quantity - a.quantity).slice(0, 8), max = Math.max(1, ...top.map(r => r.quantity));
  if (!top.length) proj.append(emptyNote('No project reservations yet.'));
  else {
    const key = node('div'), list = node('ul'); key.className = 'key'; list.className = 'bars';
    for (const [label, color] of [['Checked out', '--fill'], ['Allocated', '--track']]) {
      const item = node('span'), swatch = node('i'); swatch.style.background = `var(${color})`; item.append(swatch, document.createTextNode(label)); key.append(item);
    }
    for (const r of top) {
      const li = node('li'), name = node('span', r.project_id), slot = node('div'), track = meter(r.quantity ? r.out / r.quantity : 0), value = node('span', `${nf.format(r.out)} / ${nf.format(r.quantity)}`);
      name.className = 'name'; name.title = r.project_id; value.className = 'value'; track.style.width = Math.max(4, r.quantity / max * 100) + '%';
      slot.append(track); li.tabIndex = 0; li.dataset.tip = `${r.project_id}: ${nf.format(r.quantity)} allocated, ${nf.format(r.out)} checked out`;
      li.append(name, slot, value); list.append(li);
    }
    proj.append(key, list);
  }
  grid.append(use, proj); host.append(kpis, grid);

  // The same numbers as a table, for screen readers and for exact values.
  const details = node('details'); details.className = 'all-projects'; details.append(node('summary', `View all ${s.projects.length} projects as a table`));
  table(['Project ID', 'Allocated', 'Checked out'], s.projects, r => [r.project_id, r.quantity, r.out], details); host.append(details);
}
async function render() {
  const mine=++renderSeq;
  const load=async(...args)=>{const reply=await api(...args); if(mine!==renderSeq) throw STALE; return reply;};
  for (const b of document.querySelectorAll('[data-screen]')) { if (b.dataset.screen === screen) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); }
  $('screen').setAttribute('aria-busy', 'true');
  try { await renderScreen(load); } catch(e) { if(e!==STALE) throw e; } finally { if (mine === renderSeq) $('screen').removeAttribute('aria-busy'); }
}
async function renderScreen(load) {
  $('screen').replaceChildren(node('h2', {dashboard:'Inventory dashboard',hardware:'Hardware inventory',projects:'Project inventory',adjustments:'Inventory adjustments',checkouts:'Outstanding checkouts',transactions:'Immutable transaction history'}[screen]));
  const lead={hardware:'Pools of hardware this service owns. Look one up by its exact ID, or search by name or asset tag.',adjustments:'Change how many units a pool owns, for example after a purchase or a loss. Every change is audited.',transactions:'Every change made through this service. Open a row to see the values before and after.'}[screen];
  if(lead) $('screen').append(node('p',lead));
  $('screen').append(screenMeta(
    screenChip(screen === 'dashboard' ? 'Operational overview' : screen === 'hardware' ? 'Inventory registry' : screen === 'projects' ? 'Reservation ledger' : screen === 'adjustments' ? 'Audit-ready changes' : screen === 'checkouts' ? 'Borrower activity' : 'Immutable event log', 'accent'),
    screenChip(writable ? 'Admin access enabled' : 'Read-only view', writable ? 'good' : 'neutral')
  ));
  if(screen==='dashboard') { dashboard(await load('GetSummary')); return; }
  if(screen==='hardware'||screen==='adjustments') {
    searchToolbar([['hardware_id','Hardware ID (exact)'],['q','Name / asset tag prefix'],['status','Status','select',['','active','damaged','under_maintenance','retired']],['sort','Sort by','select',['name_key','asset_key','status','total']]]);
    if(writable&&screen==='hardware') $('screen').append(primary(button('Add hardware',()=>openEditor('Add hardware',{},[field('name','Name'),field('category','Category'),field('asset_tag','Unique asset tag'),field('serial','Serial','', 'text',false),field('description','Description','','text',false),field('total','Total units',1,'number')],v=>mutate('CreateHardware',{...v,total:Number(v.total)})))));
    // An exact ID looks the record up directly; the other filters apply to the list search only.
    const {hardware_id, ...listFilters}=listArgs();
    let data;
    if(hardware_id) {
      try { data={items:[await load('GetHardware',{hardware_id})],total:1,size:25}; }
      catch(e) { if(e.code!==5) throw e; data={items:[],total:0,size:25}; }
    } else data=await load('ListHardware',listFilters);
    table(['Name / asset','Category / status','Total','Available / unallocated','Actions'],data.items,h=>{
      const links=[button('Audit',()=>auditFor(h.id)),button('Allocations',()=>{screen='projects';filters={hardware_id:h.id};page=1;return render();}),button('QR code',()=>showQr(h))];
      if(writable&&h.status!=='retired') {
        links.push(button('Edit',()=>openEditor('Edit hardware',h,[field('name','Name',h.name),field('category','Category',h.category),field('asset_tag','Asset tag',h.asset_tag),field('serial','Serial',h.serial,'text',false),field('description','Description',h.description,'text',false),select('status','Status',['active','damaged','under_maintenance'],h.status)],v=>mutate('UpdateHardware',{...v,hardware_id:h.id,expected_version:h.version}))));
        links.push(button('Adjust stock',()=>{const delta=field('delta','Change in total (negative to remove)',1,'number');delta.querySelector('input').min='-1000000';openEditor('Adjust total stock',h,[delta],v=>mutate('AdjustHardware',{...v,hardware_id:h.id,delta:Number(v.delta),expected_version:h.version,confirmed:true}));}));
        links.push(danger(button('Retire',()=>openEditor('Retire hardware',h,[],v=>mutate('RetireHardware',{...v,hardware_id:h.id,expected_version:h.version,confirmed:true})))));
      }
      return [stack(h.name,h.asset_tag),stack(h.category,pill(h.status)),h.total,cellMeter(`${h.available} / ${h.unallocated}`,h.total?h.available/h.total:0),actions(...links)];
    });pager(data);return;
  }
  if(screen==='projects') {
    $('screen').append(node('p','Project metadata is owned by the project service. Only immutable external project IDs, reservations and outstanding quantities are stored here.'));
    searchToolbar([['project_id','External project ID'],['hardware_id','Hardware ID']]);
    const known=await load('GetSummary'), choices=node('datalist');choices.id='project-ids';
    for(const project of known.projects){const option=node('option');option.value=project.project_id;choices.append(option);}
    $('screen').append(choices);document.querySelector('[name=project_id]').setAttribute('list','project-ids');
    if(writable) $('screen').append(primary(button('Set project allocation',()=>openEditor('Set allocation',{},[field('hardware_id','Hardware ID'),field('project_id','External project ID'),field('quantity','New allocated total',1,'number')],async v=>{const h=await api('GetHardware',{hardware_id:v.hardware_id});if(!confirm(`Current total: ${h.total}; reserved: ${h.reserved}; checked out: ${h.out}. Confirm new allocation ${v.quantity}?`))throw new Error('Change cancelled');return mutate('AllocateHardware',{hardware_id:v.hardware_id,project_id:v.project_id,quantity:Number(v.quantity),reason:v.reason,expected_version:h.version});}))));
    const data=await load('ListAllocations',listArgs());
    table(['Project ID','Hardware ID','Allocated','Out','Actions'],data.items,a=>[a.project_id,a.hardware_id,a.quantity,a.out,actions(button('Audit',()=>auditFor(a.hardware_id,a.project_id)),...(writable?[button('Check out',()=>checkoutEditor({hardware_id:a.hardware_id,project_id:a.project_id})),button('Modify',async()=>{const h=await api('GetHardware',{hardware_id:a.hardware_id});openEditor('Modify allocation',{allocation:a,hardware:h},[field('quantity','New allocated total',a.quantity,'number')],v=>mutate('AllocateHardware',{...v,hardware_id:a.hardware_id,project_id:a.project_id,quantity:Number(v.quantity),expected_version:h.version}));})]:[]))]);pager(data);return;
  }
  if(screen==='checkouts') {
    searchToolbar([['project_id','Project ID'],['hardware_id','Hardware ID'],['user_id','Borrower ID'],['outstanding','Show','select',['true','false']]]);
    $('screen').append(node('p','Administrators can check out reserved stock, process returns and correct recorded return quantities here. A checkout needs stock already allocated to the project. Set Show=false to include fully returned checkouts.'));
    if(writable) $('screen').append(primary(button('Check out stock',()=>checkoutEditor())));
    const data=await load('ListCheckouts',listArgs());
    table(['Checkout / hardware ID','Project / borrower ID','Quantity / returned','Actions'],data.items,c=>[mono(stack(c.id,c.hardware_id)),stack(c.project_id,c.user_id),cellMeter(`${c.quantity} / ${c.returned}`,c.quantity?c.returned/c.quantity:0),actions(button('Audit',()=>auditFor(c.hardware_id,c.project_id)),...(writable?[button('Return',()=>openEditor('Process partial or full return',c,[field('quantity','Quantity to return',c.outstanding,'number')],v=>mutate('ReturnCheckout',{...v,checkout_id:c.id,quantity:Number(v.quantity),expected_version:c.version}))),button('Correct return',()=>openEditor('Correct cumulative returned quantity',c,[field('returned','Correct cumulative quantity returned',c.returned,'number')],v=>mutate('CorrectCheckout',{...v,checkout_id:c.id,returned:Number(v.returned),expected_version:c.version,confirmed:true})))]:[]))]);pager(data);return;
  }
  searchToolbar([['project_id','Project ID'],['hardware_id','Hardware ID'],['actor_id','Administrator actor ID'],['user_id','Borrower ID'],['start','Start (ISO 8601 with timezone)'],['end','End (ISO 8601 with timezone)']]);
  const data=await load('ListTransactions',listArgs());
  table(['Timestamp','Actor / operation','Entity / project','Reason / changes'],data.items,a=>{const d=node('details');d.append(node('summary',a.reason),node('pre',JSON.stringify({before:a.before,after:a.after},null,2)));return [rpc.toIso(a.timestamp),a.actor+' / '+a.operation,a.entity+' / '+(a.project_id||''),d];});pager(data);
}
async function initialize() {
  const me=await api('Me');writable=me.roles.includes('Hardware.Admin');
  $('identity').textContent=me.name; $('identity').dataset.initial=(me.name||'?').trim().charAt(0); $('signout').hidden=false;$('login').hidden=true;$('app').hidden=false;await render();
}
$('cancel').onclick=()=>$('editor').close();
$('qr').addEventListener('close',()=>{if(qrUrl){URL.revokeObjectURL(qrUrl);qrUrl='';}$('qr-image').removeAttribute('src');});
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
