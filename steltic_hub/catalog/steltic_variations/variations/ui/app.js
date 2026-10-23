/* Design variations — one page, five steps: base brief → variations → design → score → results. */
'use strict';

const qs = new URLSearchParams(location.search);
const S = {
  project: (qs.get('project') || 'Project').replace(/[^A-Za-z0-9_-]/g, '') || 'Project',
  lib: null, proj: null, me: null, step: 1,
  log: [], es: null, lastSeq: 0, liveText: {},
  queue: new Set(), sort: { key: 'rank', dir: 'asc' }, sel: new Set(), cols: null,
  scoreText: null, rules: null, rates: null,
  // PEB lever study (HR Steel's deterministic auto-sizer, no LLM): status, chosen variants, options, live log
  peb: null, pebSel: null, pebOpts: null, pebLog: [], pebSeq: 0, pebEs: null, pebView: null,
};
const $ = (s, r = document) => r.querySelector(s);
const enc = encodeURIComponent;
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style') n.style.cssText = v;
    else if (k.startsWith('on')) n[k] = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k in n && k !== 'list' && k !== 'form') { try { n[k] = v; } catch (e) { n.setAttribute(k, v); } }
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : document.createTextNode(String(k)));
  return n;
}
function toast(msg, kind = '') {
  const t = el('div', { class: 'toast ' + kind }, msg);
  $('#toasts').append(t); setTimeout(() => t.remove(), kind === 'bad' ? 9000 : 4500);
}
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  let d = null; const txt = await r.text();
  try { d = JSON.parse(txt); } catch (e) { d = { detail: txt }; }
  if (!r.ok) throw new Error((d && (d.detail || d.error)) || `${r.status} ${r.statusText}`);
  return d;
}
function openModal(title, body, buttons) {
  const m = $('#modal'); $('.mh', m).textContent = title;
  $('.mb', m).replaceChildren(...body); $('.mf', m).replaceChildren(...buttons); m.hidden = false;
}
function closeModal() { $('#modal').hidden = true; }
const fmt = {
  num(v, d = 2) { if (v === null || v === undefined || v === '' || Number.isNaN(v)) return '—'; if (typeof v !== 'number') return String(v);
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + ' M'; if (Math.abs(v) >= 10000) return Math.round(v).toLocaleString(); return Number.isInteger(v) ? String(v) : v.toFixed(d); },
  money(v) { if (v === null || v === undefined) return '—'; return '₹' + (Math.abs(v) >= 1e7 ? (v / 1e7).toFixed(2) + ' cr' : Math.abs(v) >= 1e5 ? (v / 1e5).toFixed(2) + ' lakh' : Math.round(v).toLocaleString('en-IN')); },
  when(t) { return t ? new Date(t * 1000).toLocaleString() : ''; },
};
const STATUS = { pending: ['not designed', 'dim'], running: ['designing…', 'run'], done: ['done', 'ok'], failed: ['failed', 'bad'],
  np: ['not permitted', 'bad'], paused: ['paused', 'warn'], stopped: ['stopped', 'warn'] };
function statusPill(r) { const [t, k] = STATUS[r.status] || [r.status, '']; return el('span', { class: 'pill ' + k, title: r.reason || '' }, t); }

/* ---------------------------------------------------------------- load */
async function load() {
  $('#projname').textContent = S.project;
  [S.lib, S.me, S.proj, S.peb] = await Promise.all([api('/api/library'), api('/api/me'), api(`/api/project/${enc(S.project)}`),
    api(`/api/project/${enc(S.project)}/peb-study`).catch(e => ({ is_peb_job: false, reasons: [e.message], rows: [], outputs: [] }))]);
  if (S.rules === null) S.rules = { ...S.lib.default_rules, ...(S.proj.scoring.rules || {}) };
  if (S.rates === null) S.rates = { ...S.lib.default_rates, ...(S.proj.scoring.rates || {}) };
  if (S.scoreText === null) S.scoreText = S.proj.scoring.source_text || '';
  if (!S.sel.size) (S.proj.selection.ids || []).forEach(i => S.sel.add(i));
  if (!S.cols) S.cols = new Set(['modelled_cost', 'net_value', 'steel_t', 'steel_kg_m2', 'drift_utilisation', 'drift_margin',
    'drift_concentration_ratio', 'n_moment_conn', 'dc_max', 'n_open_reasons', 'T1_s', 'VB_kN', 'R']);
  if (S.step === 1 && S.proj.plan.length) S.step = S.proj.rows.some(r => r.status === 'done') ? (S.proj.scoring.scored_at ? 5 : 4) : 3;
  if (S.proj.running) attachEvents();
  if (S.peb && S.peb.running) attachPebEvents();
  header(); render();
}
async function refresh() { S.proj = await api(`/api/project/${enc(S.project)}`); header(); }
function header() {
  const n = $('#status'); n.className = 'note';
  const bits = [];
  if (!S.me.steltic_url) { bits.push('HR Steel not installed'); n.classList.add('bad'); }
  else if (!S.me.steltic_up) bits.push('HR Steel server idle');
  bits.push(S.me.llm ? `model ${S.me.model}` : 'no LLM connection — built-in library only');
  n.textContent = bits.join(' · ');
}

/* ---------------------------------------------------------------- steps */
const STEPS = [[1, 'Base brief'], [2, 'Variations'], [3, 'Design'], [4, 'Score'], [5, 'Results']];
function stepState(k) {
  const p = S.proj;
  if (k === 1) return p.base_brief ? 'set' : '';
  if (k === 2) return p.plan.length ? `${p.plan.length} planned` : '';
  if (k === 3) { const d = p.rows.filter(r => r.status === 'done').length; return p.running ? 'running…' : (d ? `${d}/${p.rows.length} designed` : ''); }
  if (k === 4) return p.scoring.scored_at ? 'applied' : '';
  if (k === 5) return p.selection.recorded_at ? `${p.selection.ids.length} selected` : '';
  return '';
}
function paintSteps() {
  $('#steps').replaceChildren(...STEPS.map(([k, t]) => el('div', { class: 'step' + (S.step === k ? ' on' : '') + (stepState(k) && S.step !== k ? ' done' : ''),
    onclick: () => { S.step = k; render(); } }, el('span', { class: 'k' }, k), el('span', {}, t), stepState(k) ? el('span', { class: 'st' }, stepState(k)) : null)),
    el('span', { class: 'sp' }), pebStep());
}
function render() {
  paintSteps();
  S.pebView = null;
  const main = $('#main'); main.replaceChildren();
  ({ 1: paneBase, 2: panePlan, 3: paneRun, 4: paneScore, 5: paneResults, peb: panePeb })[S.step](main);
  main.scrollTop = 0;
}

/* ---------------------------------------------------------------- 1. base brief */
function paneBase(main) {
  const ta = el('textarea', { style: 'min-height:260px', placeholder: 'The building brief every variation starts from — the same text you would give HR Steel (IS 800): storeys, bays (m), loads (kN/m²), IS 1893 zone / soil / occupancy, IS 875-3 Vb, lateral system and R, steel grade.' });
  ta.value = S.proj.base_brief || '';
  const save = async () => {
    try { S.proj = await api(`/api/project/${enc(S.project)}/base`, { method: 'POST', body: { brief: ta.value, source: 'typed' } }); toast('base brief saved', 'ok'); S.step = 2; render(); }
    catch (e) { toast(e.message, 'bad'); }
  };
  const fromDesign = async () => {
    try {
      const d = await api(`/api/project/${enc(S.project)}/base/from-design`);
      ta.value = d.brief; toast('brief loaded from this project’s HR Steel design — save it to continue', 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  };
  main.append(el('div', { class: 'pane' },
    el('h2', {}, 'Base brief'),
    el('p', { class: 'lead' }, 'Every variation is this brief plus one stated change, designed as its own HR Steel job named ',
      el('code', {}, `${S.project}_M0xx`), '. Paste a brief, or take the one HR Steel already designed for this project.'),
    el('div', { class: 'card' }, ta,
      el('div', { class: 'row', style: 'margin-top:10px' },
        el('button', { class: 'primary', onclick: save }, 'Save brief and continue'),
        el('button', { onclick: fromDesign, disabled: !S.me.steltic_url }, 'Load from this project’s HR Steel design'),
        el('span', { class: 'hint' }, S.proj.base_source ? `source: ${S.proj.base_source}` : '')))));
}

/* ---------------------------------------------------------------- 2. variations */
function panePlan(main) {
  const p = S.proj;
  let mode = p.mode || 'categories';
  const cats = new Set(p.categories || []);
  const nIn = el('input', { type: 'number', min: 1, max: 400, value: p.n || 10 });
  const instr = el('textarea', { placeholder: 'e.g. "Compare braced cores against perimeter moment frames, include two BRB options and one with a site-specific hazard study; keep the floor plate as briefed."' });
  instr.value = p.instructions || '';
  const catBox = el('div', { class: 'cats' });
  const instrBox = el('div', {}, el('label', { class: 'f' }, 'Instructions for the study'), instr);
  const autoBox = el('p', { class: 'hint' }, 'The model chooses across the categories that apply to this building (single-storey shed or multistorey, read from the brief) for the widest useful spread.');
  const choices = el('div', { class: 'choices' });
  const paintMode = () => {
    choices.replaceChildren(...[
      ['categories', 'Pick categories', 'Choose any number of the twelve categories; the model spreads the variations across them.'],
      ['instructions', 'Describe the study', 'Type what you want explored; the model turns it into a variation list.'],
      ['auto', 'Let the model decide', 'The model proposes the whole list from the brief alone.'],
    ].map(([id, t, s]) => el('div', { class: 'choice' + (mode === id ? ' on' : ''), onclick: () => { mode = id; paintMode(); } }, el('b', {}, t), el('span', {}, s))));
    catBox.hidden = mode !== 'categories'; instrBox.hidden = mode !== 'instructions'; autoBox.hidden = mode !== 'auto';
  };
  catBox.replaceChildren(...S.lib.categories.map(c => {
    const cb = el('input', { type: 'checkbox', checked: cats.has(c.id) });
    const n = el('label', { class: 'cat' + (cats.has(c.id) ? ' on' : '') }, cb,
      el('div', {}, el('b', {}, c.label), el('span', {}, c.blurb), el('i', {}, 'e.g. ' + c.examples.join(' · '))));
    cb.onchange = () => { cb.checked ? cats.add(c.id) : cats.delete(c.id); n.classList.toggle('on', cb.checked); };
    return n;
  }));
  paintMode();
  const go = el('button', { class: 'primary' }, 'Create variation list');
  go.onclick = async () => {
    go.disabled = true; go.textContent = S.me.llm ? 'asking the model…' : 'building the list…';
    try {
      S.proj = await api(`/api/project/${enc(S.project)}/plan`, { method: 'POST',
        body: { n: Number(nIn.value), mode, categories: [...cats], instructions: instr.value } });
      toast(`${S.proj.plan.length} variations planned`, 'ok'); render();
    } catch (e) { toast(e.message, 'bad'); go.disabled = false; go.textContent = 'Create variation list'; }
  };
  const pane = el('div', { class: 'pane' },
    el('h2', {}, 'Variations'),
    el('p', { class: 'lead' }, 'M001 is always the base brief unchanged. Every other variation changes one stated thing and says why — the design agent reads only that text.'),
    el('div', { class: 'card' },
      el('div', { class: 'row' }, el('label', { class: 'f', style: 'margin:0' }, 'Number of variations'), nIn, el('span', { class: 'hint' }, 'including M001')),
      choices, catBox, instrBox, autoBox,
      el('div', { class: 'row', style: 'margin-top:12px' }, go,
        p.plan.length ? el('span', { class: 'hint' }, 'creating a new list replaces the current one; designs already made are kept by id and flagged if their text changed') : null)));
  if (p.plan.length) pane.append(planTable());
  main.append(pane);
}
function planTable() {
  const p = S.proj;
  const rows = p.plan.map(v => ({ ...v }));
  const resultsById = Object.fromEntries(p.rows.map(r => [r.id, r]));
  const tbody = el('tbody');
  const cell = (r, k, cls = '') => el('td', { class: cls, contenteditable: 'plaintext-only', oninput: e => { r[k] = e.target.textContent; dirty(true); } }, r[k] || '');
  const paint = () => tbody.replaceChildren(...rows.map((r, i) => el('tr', {},
    el('td', { class: 'id' }, r.id), cell(r, 'group'), cell(r, 'title'), cell(r, 'change'), cell(r, 'why'),
    el('td', {}, resultsById[r.id] ? statusPill(resultsById[r.id]) : ''),
    el('td', {}, el('button', { class: 'ghost small', title: 'remove', onclick: () => { rows.splice(i, 1); renumber(); paint(); dirty(true); } }, '✕')))));
  const renumber = () => rows.forEach((r, i) => { r.id = 'M' + String(i + 1).padStart(3, '0'); });
  const save = el('button', { class: 'primary', disabled: true }, 'Save edits');
  const dirty = (on) => { save.disabled = !on; };
  save.onclick = async () => {
    try { S.proj = await api(`/api/project/${enc(S.project)}/plan`, { method: 'PUT', body: { plan: rows } }); toast('plan saved', 'ok'); render(); }
    catch (e) { toast(e.message, 'bad'); }
  };
  paint();
  return el('div', { class: 'card' },
    el('div', { class: 'row' }, el('h3', { style: 'margin:0' }, `Plan — ${rows.length} variations`),
      el('span', { class: 'hint' }, p.plan_source ? `from ${p.plan_source}` : ''), el('span', { class: 'sp' }),
      el('button', { class: 'small', onclick: () => { rows.push({ id: '', group: '', title: 'New variation', change: '', why: '' }); renumber(); paint(); dirty(true); } }, '+ Add row'),
      save, el('button', { class: 'small', onclick: () => { S.step = 3; render(); } }, 'Go to Design →')),
    p.plan_note ? el('p', { class: 'hint', style: 'margin:6px 0' }, p.plan_note) : null,
    el('p', { class: 'hint' }, 'Cells are editable — click to change a title, change or why. Ids renumber on save.'),
    el('div', { class: 'tablewrap', style: 'margin-top:8px' }, el('table', { class: 'plan' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Id'), el('th', {}, 'Group'), el('th', {}, 'Title'), el('th', {}, 'Change (relative to the base brief)'), el('th', {}, 'Why'), el('th', {}, 'Status'), el('th', {}))),
      tbody)));
}

/* ---------------------------------------------------------------- 3. design */
function paneRun(main) {
  const p = S.proj;
  const pending = p.rows.filter(r => r.status !== 'done' || r.stale);
  const list = el('div', { class: 'runlist' });
  const paintList = () => list.replaceChildren(...p.rows.map(r => {
    const cb = el('input', { type: 'checkbox', checked: S.queue.has(r.id), onchange: e => { e.target.checked ? S.queue.add(r.id) : S.queue.delete(r.id); paintButtons(); } });
    return el('label', { class: 'rv ' + r.status + (S.queue.has(r.id) ? ' q' : ''), title: (r.reason || r.change || '').slice(0, 300) },
      cb, el('span', { class: 'd' }), el('span', { class: 't' }, `${r.id} ${r.title}`), r.stale ? el('span', { class: 'pill warn', title: 'text changed since it was designed' }, 'changed') : statusPill(r));
  }));
  const runPending = el('button', { class: 'primary' });
  const runChecked = el('button', {});
  const stop = el('button', { class: 'danger' }, 'Stop');
  const paintButtons = () => {
    runPending.textContent = `Design pending (${pending.length})`; runPending.disabled = p.running || !pending.length;
    runChecked.textContent = `Design checked (${S.queue.size})`; runChecked.disabled = p.running || !S.queue.size;
    stop.disabled = !p.running;
  };
  const start = async (body) => {
    try { await api(`/api/project/${enc(S.project)}/run`, { method: 'POST', body }); S.log = []; S.liveText = {}; S.lastSeq = 0; await refresh(); attachEvents(); render(); }
    catch (e) { toast(e.message, 'bad'); }
  };
  runPending.onclick = () => start({});
  runChecked.onclick = () => start({ ids: [...S.queue] });
  stop.onclick = async () => { await api(`/api/project/${enc(S.project)}/stop`, { method: 'POST' }); toast('stopping after the current step'); };
  paintList(); paintButtons();
  const done = p.rows.filter(r => r.status === 'done').length;
  const bar = el('div', { class: 'progress' }, el('i', { style: `width:${p.rows.length ? 100 * done / p.rows.length : 0}%` }));
  const logBox = el('div', { class: 'log', id: 'log' });
  main.append(el('div', { class: 'pane' },
    el('h2', {}, 'Design the variations'),
    el('p', { class: 'lead' }, 'Each variation is sent to HR Steel as its own building (', el('code', {}, `${S.project}_M0xx`),
      ') one after another; the package is downloaded when it finishes and its metrics are read. Closing this tab does not stop a study — reopen it to re-attach.'),
    !S.me.steltic_url ? el('div', { class: 'card', style: 'border-color:var(--bad)' }, 'HR Steel is not installed. Install it from the Modules tab — it designs the variations.') : null,
    !S.me.has_creds ? el('div', { class: 'card', style: 'border-color:var(--warn)' }, 'No LLM connection: HR Steel cannot design without one. Set it with the Connection button in the title bar.') : null,
    S.me.has_creds && !S.me.llm ? el('div', { class: 'card tight' }, el('span', { class: 'hint' }, 'Model MOCK: HR Steel produces its mock design for each variation (useful to test the study); the plan and the report reading use the built-in library.')) : null,
    el('div', { class: 'card' }, el('div', { class: 'row' }, runPending, runChecked, stop, el('span', { class: 'sp' }),
      el('span', { class: 'hint' }, `${done} of ${p.rows.length} designed`)), bar, list),
    el('div', { class: 'card' }, el('div', { class: 'row' }, el('h3', { style: 'margin:0' }, 'Log'), el('span', { class: 'sp' }),
      el('button', { class: 'small', onclick: () => { S.step = 4; render(); } }, 'Go to Score →')), logBox)));
  paintLog();
}
function attachEvents() {
  if (S.es) { S.es.close(); S.es = null; }
  const es = new EventSource(`/api/project/${enc(S.project)}/events?since=${S.lastSeq}`);
  S.es = es;
  es.onmessage = async (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
    if (ev.seq) S.lastSeq = ev.seq;
    if (ev.type === 'finished') {
      es.close(); S.es = null;
      if (!ev.idle) { await refresh(); if (S.step === 3) render(); else paintLog(); }
      return;
    }
    if (ev.type === 'text') { S.liveText[ev.id] = ((S.liveText[ev.id] || '') + ev.text).slice(-800); const last = S.log[S.log.length - 1];
      if (last && last.live === ev.id) last.t = S.liveText[ev.id]; else S.log.push({ k: 'text', live: ev.id, t: S.liveText[ev.id] }); }
    else if (ev.type === 'variation') {
      if (ev.status === 'running') { S.liveText[ev.id] = ''; S.log.push({ k: 'var', t: `▶ ${ev.id} ${ev.title}  (${ev.n}/${ev.of})` }); }
      else { S.log.push({ k: ev.status === 'done' ? 'ok' : 'err', t: `■ ${ev.id} ${ev.status}${ev.reason ? ': ' + ev.reason : ''}` }); await refresh(); if (S.step === 3) { render(); return; } }
    }
    else if (ev.type === 'tool' || ev.type === 'tool_result') S.log.push({ k: 'tool', t: (ev.type === 'tool' ? '→ ' : '← ') + ev.text });
    else if (ev.type === 'usage') { /* quiet */ }
    else S.log.push({ k: /^(error|paused)/.test(ev.text || '') ? 'err' : 'l', t: (ev.id ? ev.id + ' ' : '') + (ev.text || '') });
    if (S.log.length > 600) S.log.splice(0, 100);
    paintLog();
  };
  es.onerror = () => { /* the browser retries; a finished study answers with `finished` and we close */ };
}
function paintLog() {
  const box = $('#log'); if (!box) return;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.replaceChildren(...S.log.map(l => el('div', { class: 'l ' + l.k }, l.t)));
  if (stick) box.scrollTop = box.scrollHeight;
}

/* ---------------------------------------------------------------- 4. score */
const RULES = [
  ['require_done', 'Design finished (status DONE)'],
  ['require_checks_pass', 'Every IS 800 check passing (no D/C > 1.0, no failing checks)'],
  ['require_design_complete', 'design_status COMPLETE (0 open reasons — the India gate authority)'],
  ['drift_utilisation_max', 'Storey drift utilisation (IS 1893 7.11.1.1, 0.004 h) ≤', 'num'],
  ['smf_share_min_pct', 'Moment-frame share ≥ (%, mixed moment-frame + braced systems only)', 'num'],
  ['require_gates_pass', 'Every analysis gate ok (RSA scaling, modal mass, stability, drift, wind deflection …)'],
  ['no_torsional_irregularity', 'Not torsionally irregular (IS 1893 Table 5(i), Amd 2)'],
  ['wind_comfort_max_mg', 'Wind comfort ≤ (milli-g, when reported)', 'num'],
  ['representable_only', 'Representable in the Nonlinear (SNL-IN) tools (an IS 800 Section 12 frame type: SMRF, SCBF, EBF, OMRF, OCBF)'],
  ['tapered_exempt', 'Tapered PEB frames exempt from the rule above (the nonlinear tools have no tapered section yet)'],
];
function rulesEditor() {
  return el('div', { class: 'rules' }, ...RULES.map(([k, label, kind]) => {
    if (kind === 'num') {
      const on = el('input', { type: 'checkbox', checked: S.rules[k] !== null && S.rules[k] !== undefined && S.rules[k] !== false });
      const v = el('input', { type: 'number', step: 'any', value: S.rules[k] ?? S.lib.default_rules[k], disabled: !on.checked });
      on.onchange = () => { v.disabled = !on.checked; S.rules[k] = on.checked ? Number(v.value) : null; };
      v.onchange = () => { S.rules[k] = Number(v.value); };
      return el('label', { class: 'rule' }, on, el('span', {}, label), v);
    }
    const on = el('input', { type: 'checkbox', checked: !!S.rules[k], onchange: e => { S.rules[k] = e.target.checked; } });
    return el('label', { class: 'rule' }, on, el('span', {}, label));
  }));
}
function criteriaPills(rules) {
  const r = rules || S.rules; const out = [];
  if (r.require_done) out.push('status DONE');
  if (r.require_checks_pass) out.push('every IS 800 check passing');
  if (r.require_design_complete) out.push('design_status COMPLETE');
  if (r.drift_utilisation_max != null) out.push(`drift utilisation ≤ ${r.drift_utilisation_max}`);
  if (r.smf_share_min_pct != null) out.push(`moment-frame share ≥ ${r.smf_share_min_pct} % (mixed systems)`);
  if (r.require_gates_pass) out.push('analysis gates ok');
  if (r.no_torsional_irregularity) out.push('not torsionally irregular');
  if (r.wind_comfort_max_mg != null) out.push(`wind comfort ≤ ${r.wind_comfort_max_mg} mg`);
  if (r.representable_only) out.push('representable in nonlinear tools' + (r.tapered_exempt ? ' (tapered PEB frames exempt)' : ''));
  return el('div', { class: 'crit' }, ...out.map(t => el('span', { class: 'pill ok' }, t)));
}
function paneScore(main) {
  const p = S.proj;
  const eq = S.lib.default_equation;
  const copy = el('button', { class: 'small', onclick: async () => { try { await navigator.clipboard.writeText(eq); toast('equation copied', 'ok'); } catch (e) { ta.value = eq; toast('copied into the box'); } } }, 'Copy');
  const ta = el('textarea', { class: 'mono', style: 'min-height:90px', placeholder: 'Leave empty to use the default. Paste the equation and change the weights, write your own S = … with the metric names below, or describe what matters in words (e.g. "cost matters most, then drift margin; ignore connections") and the model writes the equation.' });
  ta.value = S.scoreText || ''; ta.oninput = () => { S.scoreText = ta.value; };
  const ratesBox = el('div', { class: 'row' }, ...Object.entries(S.lib.default_rates).map(([k, d]) => {
    const inp = el('input', { type: 'number', step: 'any', value: S.rates[k] ?? d, onchange: e => { S.rates[k] = Number(e.target.value); } });
    return el('label', { class: 'rule' }, el('span', {}, { steel_per_t: '₹ per tonne of steel (fabricated, erected)', per_moment_conn: '₹ per moment connection', per_brace: '₹ per brace', revenue_per_m2: '₹ revenue per m² of floor area' }[k] || k), inp);
  }));
  const apply = el('button', { class: 'primary' }, 'Apply scoring and rank');
  const out = el('div');
  apply.onclick = async () => {
    apply.disabled = true; apply.textContent = 'scoring…';
    try {
      const d = await api(`/api/project/${enc(S.project)}/score`, { method: 'POST', body: { text: S.scoreText || '', rules: S.rules, rates: S.rates } });
      S.proj.scoring = d.scoring; S.proj.rows = d.rows;
      const sc = d.scoring;
      out.replaceChildren(el('div', { class: 'card', style: 'border-color:var(--ok)' },
        el('div', { class: 'kv' }, 'Equation used (', sc.equation_source, '):'), el('pre', { class: 'mono', style: 'color:var(--accent);margin:6px 0;white-space:pre-wrap' }, 'S = ' + sc.equation),
        sc.explanation ? el('div', { class: 'kv' }, sc.explanation) : null,
        el('div', { class: 'kv' }, `${sc.summary.n_eligible} eligible of ${d.rows.length}; ${sc.summary.n_scored} scored`),
        el('div', { class: 'row', style: 'margin-top:8px' }, el('button', { class: 'primary', onclick: () => { S.step = 5; render(); } }, 'See results →'))));
      toast('scored', 'ok');
    } catch (e) { toast(e.message, 'bad'); }
    apply.disabled = false; apply.textContent = 'Apply scoring and rank';
  };
  main.append(el('div', { class: 'pane' },
    el('h2', {}, 'Score'),
    el('p', { class: 'lead' }, 'Only eligible variations are scored and ranked; the rest are listed with the reason. The default score is the one used in the reference study — the weights are stated so you can re-weight them.'),
    el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Eligibility criteria'), rulesEditor()),
    el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Default score equation'),
      el('div', { class: 'eq' }, el('pre', {}, eq), copy),
      el('p', { class: 'hint', style: 'margin-top:6px' }, 'Weights: 0.35 cost · 0.20 drift margin · 0.15 drift concentration · 0.15 net value · 0.15 moment connections. Each term is 0–1, higher is better; *_max is taken over the eligible variations.'),
      el('h3', {}, 'Your scoring requirements'), ta,
      el('h3', {}, 'Cost model'), ratesBox,
      el('p', { class: 'hint' }, 'modelled_cost = steel tonnes × rate + moment connections × rate + braces × rate; revenue_proxy = gross floor area (m²) × rate; net_value = revenue − cost. Rates in INR are placeholders — edit them above.'),
      el('div', { class: 'row', style: 'margin-top:12px' }, apply,
        p.scoring.scored_at ? el('span', { class: 'hint' }, `last applied ${fmt.when(p.scoring.scored_at)}`) : null)),
    out,
    el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Metrics you can use'),
      el('div', { class: 'metrics' }, ...S.lib.metrics.map(m => el('div', {}, el('code', {}, m.name), ` — ${m.description} [${m.unit}]`))))));
}

/* ---------------------------------------------------------------- 5. results */
const FIXED = [['rank', '#', 'num'], ['id', 'Id'], ['title', 'Variation'], ['group', 'Group'], ['status', 'Status'], ['score', 'Score', 'num']];
function paneResults(main) {
  const p = S.proj; const sc = p.scoring;
  if (!sc.scored_at) { main.append(el('div', { class: 'pane' }, el('h2', {}, 'Results'), el('p', { class: 'lead' }, 'Apply a score first.'),
    el('button', { class: 'primary', onclick: () => { S.step = 4; render(); } }, '← Score'))); return; }
  const metricCols = [...S.cols].filter(c => S.lib.metrics.some(m => m.name === c));
  const cols = [...FIXED, ...metricCols.map(c => [c, c, 'num']), ['ineligible', 'Not eligible because'], ['links', 'Files']];
  const rows = p.rows.map(r => ({ ...r, ineligible: r.ineligible || [] }));
  const val = (r, k) => (k in r ? r[k] : r.metrics[k]);
  const sorted = [...rows].sort((a, b) => {
    const k = S.sort.key, d = S.sort.dir === 'asc' ? 1 : -1;
    let x = val(a, k), y = val(b, k);
    if (Array.isArray(x)) x = x.length; if (Array.isArray(y)) y = y.length;
    const nx = x === null || x === undefined || x === '', ny = y === null || y === undefined || y === '';
    if (nx && ny) return a.id < b.id ? -1 : 1; if (nx) return 1; if (ny) return -1;
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * d;
    return String(x).localeCompare(String(y)) * d;
  });
  const head = el('tr', {}, el('th', {}, ''), ...cols.map(([k, label, kind]) => {
    if (k === 'links') return el('th', {}, label);
    const on = S.sort.key === k;
    return el('th', { class: 'sortable' + (kind === 'num' ? ' num' : '') + (on ? ' sorted' : ''), title: 'click to sort; click again to flip',
      onclick: () => { S.sort = on ? { key: k, dir: S.sort.dir === 'asc' ? 'desc' : 'asc' } : { key: k, dir: (kind === 'num' && k !== 'rank') ? 'desc' : 'asc' }; render(); } },
      label, on ? el('span', { class: 'arrow' }, S.sort.dir === 'asc' ? '▲' : '▼') : null);
  }));
  const body = el('tbody', {}, ...sorted.map(r => {
    const cb = el('input', { type: 'checkbox', checked: S.sel.has(r.id), onchange: e => { e.target.checked ? S.sel.add(r.id) : S.sel.delete(r.id); paintSel(); tr.classList.toggle('sel', e.target.checked); } });
    const tr = el('tr', { class: (r.rank && r.rank <= 3 ? ' top' + r.rank : '') + (!r.eligible ? ' inel' : '') + (S.sel.has(r.id) ? ' sel' : '') },
      el('td', {}, cb), ...cols.map(([k, , kind]) => {
        if (k === 'links') return el('td', {}, ...(r.files || []).filter(f => f !== 'package.zip').map(f => el('a', { href: `/api/project/${enc(S.project)}/file/${r.id}/${f}`, target: '_blank', style: 'margin-right:8px' }, f === 'report.html' ? 'report' : 'viewer')),
          (r.files || []).includes('package.zip') ? el('a', { href: `/api/project/${enc(S.project)}/file/${r.id}/package.zip` }, 'zip') : null);
        if (k === 'rank') return el('td', { class: 'num' }, r.rank ? (r.rank <= 3 ? el('span', { class: 'medal m' + r.rank }, r.rank) : r.rank) : '');
        if (k === 'id') return el('td', { class: 'id' }, r.id);
        if (k === 'status') return el('td', {}, statusPill(r), r.stale ? el('span', { class: 'pill warn', style: 'margin-left:4px' }, 'changed') : null);
        if (k === 'score') return el('td', { class: 'num', title: r.score_note || '' }, r.score === null || r.score === undefined ? (r.score_note ? '?' : '—') : r.score.toFixed(3));
        if (k === 'ineligible') return el('td', { class: 'why' }, r.ineligible.join('; '));
        if (k === 'title') return el('td', { class: 'title', title: r.change || '' }, r.title);
        if (k in r) return el('td', {}, r[k] === null || r[k] === undefined ? '' : String(r[k]));
        const v = r.metrics[k];
        const est = k === 'n_moment_conn' && r.metrics.n_moment_conn_estimated;
        return el('td', { class: 'num', title: est ? 'estimated: perimeter frames, every bay, both ends, every level (no LLM to read the report)' : '' },
          (k === 'modelled_cost' || k === 'net_value' || k === 'revenue_proxy') ? fmt.money(v) : fmt.num(v), est ? el('span', { class: 'est' }, '~') : null);
      }));
    return tr;
  }));
  const selNote = el('textarea', { style: 'min-height:60px', placeholder: 'Why these (optional) — recorded with the selection.' });
  selNote.value = p.selection.note || '';
  const selInfo = el('span', { class: 'hint' });
  const record = el('button', { class: 'primary' });
  const paintSel = () => { const s = S.proj.selection; record.textContent = `Record selection (${S.sel.size})`; record.disabled = !S.sel.size;
    selInfo.textContent = s.recorded_at ? `recorded ${fmt.when(s.recorded_at)}: ${s.ids.join(', ')}` : 'nothing recorded yet'; };
  record.onclick = async () => {
    try {
      const d = await api(`/api/project/${enc(S.project)}/select`, { method: 'POST', body: { ids: [...S.sel].sort(), note: selNote.value } });
      await refresh(); paintSel();
      toast(`selection recorded — ${d.selection.selected.length} package(s) copied to variations/selected/ for the other modules`, 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  };
  paintSel();
  const colPicker = () => {
    const boxes = S.lib.metrics.map(m => { const cb = el('input', { type: 'checkbox', checked: S.cols.has(m.name), onchange: e => { e.target.checked ? S.cols.add(m.name) : S.cols.delete(m.name); } });
      return el('label', { class: 'rule' }, cb, el('span', {}, el('code', {}, m.name), ` — ${m.description}`)); });
    openModal('Columns', [el('div', { class: 'rules', style: 'grid-template-columns:1fr' }, ...boxes)],
      [el('button', { class: 'primary', onclick: () => { closeModal(); render(); } }, 'Apply'), el('button', { class: 'ghost', onclick: closeModal }, 'Cancel')]);
  };
  const top = rows.filter(r => r.rank && r.rank <= 3).sort((a, b) => a.rank - b.rank);
  main.append(el('div', { class: 'pane' },
    el('h2', {}, 'Results'),
    el('div', { class: 'card tight' },
      el('div', { class: 'row' }, el('b', { style: 'font-size:12px;color:var(--dim)' }, 'ELIGIBLE = '), criteriaPills(sc.rules)),
      el('div', { class: 'row', style: 'margin-top:6px' }, el('b', { style: 'font-size:12px;color:var(--dim)' }, 'SCORE = '), el('code', { style: 'color:var(--accent);font-size:12px' }, sc.equation),
        el('span', { class: 'hint' }, `(${sc.equation_source || 'default'}; ${sc.summary.n_eligible} eligible, ${sc.summary.n_scored} scored, applied ${fmt.when(sc.scored_at)})`))),
    top.length ? el('div', { class: 'card tight' }, el('div', { class: 'row' }, el('b', { style: 'font-size:12px;color:var(--dim)' }, 'TOP SCORERS'),
      ...top.map(r => el('span', { class: 'pill ok', style: 'text-transform:none;letter-spacing:0;font-size:12px' }, el('span', { class: 'medal m' + r.rank }, r.rank), `${r.id} ${r.title} — ${r.score.toFixed(3)}`)))) : null,
    el('div', { class: 'card' },
      el('div', { class: 'row', style: 'margin-bottom:8px' }, el('span', { class: 'hint' }, 'Click a column header to sort (max → min, click again for min → max). Dimmed rows are not eligible; ~ marks an estimated value.'),
        el('span', { class: 'sp' }), el('button', { class: 'small', onclick: colPicker }, 'Columns ▾'),
        el('a', { class: 'hint', href: '#', onclick: (e) => { e.preventDefault(); S.sort = { key: 'rank', dir: 'asc' }; render(); } }, 'reset sort')),
      el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, head), body))),
    el('div', { class: 'card' }, el('h3', { style: 'margin-top:0' }, 'Your selection'),
      el('p', { class: 'hint' }, 'Tick the variations you take forward, then record them. The selection and each package are written into this project (', el('code', {}, 'variations/selected/'), ' and ', el('code', {}, 'selected_variations.json'), ') so the Nonlinear module and the other modules can pick them up with "in project ▾".'),
      selNote, el('div', { class: 'row', style: 'margin-top:8px' }, record, selInfo))));
}

/* ---------------------------------------------------------------- PEB lever study (deterministic, no LLM)
   HR Steel's scripts/peb_lever_study.py, run by this module's server in HR Steel's own checkout and environment.
   The variants and their code status come from the runner itself (its variants_for), never from this page. */
const COMPLY = { compliant: ['compliant', 'ok'], reading: ['reading', 'warn'], 'not compliant': ['NOT COMPLIANT', 'bad'], 'n/a': ['n/a', 'dim'] };
const PEB_SPECIAL = ['strict', 'compliant', 'market', 'solar_ready'];
function complyPill(c, status) { const [t, k] = COMPLY[c] || [c || '', '']; return el('span', { class: 'pill ' + k, title: status || '' }, t); }
async function refreshPeb() {
  try { S.peb = await api(`/api/project/${enc(S.project)}/peb-study`); } catch (e) { /* keep the last answer */ }
  paintSteps();
}
function pebState() {
  const P = S.peb; if (!P) return '';
  if (P.running) return P.progress ? `running ${P.progress.n}/${P.progress.of}` : 'running…';
  if (!P.is_peb_job) return 'n/a';
  const done = (P.rows || []).filter(r => r.done && r.kg_m2 !== null && r.kg_m2 !== undefined).length;
  return done ? `${done} sized` : '';
}
function pebStep() {
  const P = S.peb || {};
  return el('div', { class: 'step peb' + (S.step === 'peb' ? ' on' : '') + (P.is_peb_job ? '' : ' na'),
    title: P.is_peb_job ? 'HR Steel’s deterministic PEB lever study (auto-sizer, no LLM)' : 'PEB lever study: does not apply to this project (open to see why)',
    onclick: () => { S.step = 'peb'; render(); } }, el('span', { class: 'k' }, '⚖'), el('span', {}, 'PEB lever study'),
    pebState() ? el('span', { class: 'st' }, pebState()) : null);
}
/* the runner's selection rules (peb_study.expand_selection, which the server applies): strict always; a ladder step
   brings the whole ladder, its first step's lever alone and 'all compliant levers'; that row alone brings the ladder up to it */
function pebExpand(vs, chosen) {
  const by = Object.fromEntries(vs.map(v => [v.name, v]));
  const want = new Set(['strict', ...chosen]);
  const ladder = vs.filter(v => v.name.startsWith('cum:') && v.applies).map(v => v.name);
  if ([...chosen].some(n => n.startsWith('cum:'))) { ladder.forEach(n => want.add(n)); if (by.compliant && by.compliant.applies) want.add('compliant'); }
  else if (want.has('compliant') && by.compliant && ladder.includes(by.compliant.alias)) ladder.slice(0, ladder.indexOf(by.compliant.alias) + 1).forEach(n => want.add(n));
  for (const n of [...want]) if (n.startsWith('cum:') && by[n] && by[n].alias) want.add(by[n].alias);
  return vs.filter(v => want.has(v.name) && v.applies);
}
function pebDefaultSel(P, vs) {
  const last = (P.run && P.run.chosen) || [];
  const ok = new Set(vs.filter(v => v.applies).map(v => v.name));
  if (last.length && last.every(n => ok.has(n))) return new Set(last);
  return new Set(vs.filter(v => v.applies && !v.name.startsWith('cum:') && !PEB_SPECIAL.includes(v.name)).map(v => v.name));
}
const LADDERS = {
  full: ['Full ladder', 'One lever added per step, each step from the previous step’s design: the waterfall of the study.'],
  short: ['Short ladder', 'Strict → every compliant lever together → the market bundle: for a study that would otherwise take too long.'],
};
function panePeb(main) {
  const P = S.peb || { is_peb_job: false, reasons: ['status not loaded'], rows: [], outputs: [] };
  const pane = el('div', { class: 'pane' }, el('h2', {}, 'PEB lever study'),
    el('p', { class: 'lead' }, 'How each Indian PEB practice or code reading changes the steel of this shed, in kg/m² of plan. HR Steel’s deterministic auto-sizer (',
      el('code', {}, 'scripts/peb_lever_study.py'), ' with ', el('code', {}, 'steel_engine/peb_autosize.py'),
      ') re-sizes the frame plates, the purlins and girts, the end plates, knee plates and base shear keys for each variant, and every variant must pass every check the engine makes. No language model is involved: the same job and switches give the same sizes. It runs in HR Steel’s own checkout and environment; nothing is sent anywhere.'));
  if (!P.is_peb_job) {
    pane.append(el('div', { class: 'card', style: 'border-color:var(--warn)' }, el('h3', { style: 'margin-top:0' }, 'Does not apply to this project'),
      el('ul', { class: 'reasons' }, ...(P.reasons || []).map(r => el('li', {}, r))),
      el('p', { class: 'hint', style: 'margin-top:8px' }, 'How to run it on your own PEB job: ', el('a', { href: '/api/peb-study/howto', target: '_blank' }, 'PEB_LEVERS.md'), '.')));
    if ((P.rows || []).length || (P.outputs || []).length) pane.append(pebResults(P));
    main.append(pane); return;
  }
  const job = P.job, running = !!P.running, cli = P.cli || { ladders: ['full'], stop_after_min: false };
  if (!S.pebOpts) S.pebOpts = { max_runs: (P.run && P.run.max_runs) || P.max_runs_default || 8, resume: true,
    ladder: (P.study && P.study.ladder) || (P.run && P.run.ladder) || 'full', stop_after_min: '' };
  if (!cli.ladders.includes(S.pebOpts.ladder)) S.pebOpts.ladder = cli.ladders[0];
  const vs = (job.variants_by_ladder || {})[S.pebOpts.ladder] || job.variants;
  if (!S.pebSel) S.pebSel = pebDefaultSel(P, vs);
  const route = { OMRF: 'ordinary moment frames (OMRF)', SMRF_IS18168: 'SMRF portals to IS 18168' }[job.route] || job.route || '?';
  pane.append(el('div', { class: 'card tight' },
    el('div', { class: 'kv' }, el('b', {}, job.name), ` · Zone ${job.zone}, I = ${job.I} · ${route} · `, job.dado_height_mm ? `brick dado ${job.dado_height_mm} mm` : 'no brick dado', job.base ? ` · ${job.base} bases` : ''),
    el('div', { class: 'kv' }, 'Job folder read: ', (P.source && P.source.label) || '—'),
    el('div', { class: 'kv' }, 'Runner: ', el('code', {}, P.runner.script || '—'), ' · CFS engine (purlins, girts): ', el('code', {}, P.runner.cfs || '—'))));
  if ((P.reasons || []).length) pane.append(el('div', { class: 'card', style: 'border-color:var(--bad)' }, el('h3', { style: 'margin-top:0' }, 'Not ready to run'),
    el('ul', { class: 'reasons' }, ...P.reasons.map(r => el('li', {}, r)))));
  if (P.busy_with) pane.append(el('div', { class: 'card tight', style: 'border-color:var(--warn)' }, `A lever study is running for ${P.busy_with}: one study at a time on this PC (every full run is a whole analysis of a shed).`));

  // ---- the variants the runner offers for this job
  const by = Object.fromEntries(vs.map(v => [v.name, v]));
  const count = el('span', { class: 'hint' });
  const runBtn = el('button', { class: 'primary' }, 'Run the PEB lever study');
  const paintCount = () => {
    const sel = pebExpand(vs, S.pebSel), sized = sel.filter(v => !v.alias);
    const nc = sel.filter(v => v.compliance === 'not compliant').length;
    count.textContent = `${sized.length} variant(s) to size (the strict baseline included; up to ${sized.length * (Number(S.pebOpts.max_runs) || 8)} full runs)` + (nc ? ` · ${nc} NOT COMPLIANT` : '');
    // a resumed study cannot switch ladder (the runner refuses; so does the server)
    const was = P.study && P.study.ladder, clash = !!(was && S.pebOpts.resume && was !== S.pebOpts.ladder);
    if (ladderWarn) ladderWarn.textContent = clash ? `The rows already done belong to the ${was} ladder: untick “Keep the rows already done” to start the study again with the ${S.pebOpts.ladder} ladder.` : '';
    runBtn.disabled = running || !P.ready || !!P.busy_with || clash;
  };
  const ladderWarn = el('p', { class: 'hint', style: 'color:var(--warn)' });
  const vrow = (v, cb) => el('label', { class: 'vrow' + (v.applies ? '' : ' na') + (v.compliance === 'not compliant' ? ' nc' : '') }, cb,
    el('div', { class: 'vt' }, el('b', {}, v.label), (v.compliance === 'reading' || v.compliance === 'not compliant') && v.status !== 'not compliant' ? el('span', { class: 'vs ' + (v.compliance === 'reading' ? 'rd' : 'nc') }, v.status) : null,
      el('span', { class: 'vn' }, v.note || '')), complyPill(v.compliance, v.status));
  const tick = v => {
    if (!v.applies) return el('input', { type: 'checkbox', disabled: true });
    return el('input', { type: 'checkbox', checked: S.pebSel.has(v.name), disabled: running, onchange: e => { e.target.checked ? S.pebSel.add(v.name) : S.pebSel.delete(v.name); paintCount(); } });
  };
  const order = job.variant_order || [];
  const rank = v => (order.indexOf(v.name) + 1) || 999;
  const levers = vs.filter(v => !v.name.startsWith('cum:') && !PEB_SPECIAL.includes(v.name)).sort((a, b) => rank(a) - rank(b));
  const ladder = vs.filter(v => v.name.startsWith('cum:'));
  const ladderOn = () => [...S.pebSel].some(n => n.startsWith('cum:'));
  const ladderCb = el('input', { type: 'checkbox', checked: ladderOn(), disabled: running || !ladder.some(v => v.applies),
    onchange: e => { ladder.filter(v => v.applies).forEach(v => e.target.checked ? S.pebSel.add(v.name) : S.pebSel.delete(v.name)); paintCount(); } });
  const preset = (label, fn) => el('button', { class: 'small', disabled: running, onclick: () => { S.pebSel = new Set(vs.filter(v => v.applies && fn(v)).map(v => v.name)); render(); } }, label);
  const sec = (title, hint, ...kids) => el('div', { class: 'vsec' }, el('h3', {}, title), hint ? el('p', { class: 'hint' }, hint) : null, ...kids);
  const comp = by.compliant;
  const ladderChoice = cli.ladders.length > 1 ? el('div', { class: 'choices' }, ...cli.ladders.map(id => {
    const [t, s] = LADDERS[id] || [id, ''];
    return el('div', { class: 'choice' + (S.pebOpts.ladder === id ? ' on' : ''), onclick: () => {
      if (running || S.pebOpts.ladder === id) return;
      S.pebOpts.ladder = id;
      const keep = new Set(((job.variants_by_ladder || {})[id] || []).filter(v => v.applies).map(v => v.name));
      S.pebSel = new Set([...S.pebSel].filter(n => keep.has(n)));
      render();
    } }, el('b', {}, t + (P.study && P.study.ladder === id ? ' (the rows already done)' : '')), el('span', {}, s));
  })) : null;
  pane.append(el('div', { class: 'card' },
    el('div', { class: 'row' }, el('h3', { style: 'margin:0' }, 'Variants'), el('span', { class: 'sp' }),
      preset('Levers alone', v => levers.includes(v)), preset('Compliant only', v => (levers.includes(v) && v.compliance === 'compliant') || v.name === 'compliant'),
      preset('Everything', () => true), preset('Strict only', () => false)),
    ladderChoice,
    el('p', { class: 'hint' }, 'The variants and their status are the study runner’s own. ', complyPill('compliant'), ' compliant with the Indian Standards as written; ',
      complyPill('reading'), ' a reading an owner ruling rejected, the old unconservative treatment, or a code conflict for the design engineer to decide; ',
      complyPill('not compliant'), ' not compliant — shown because it is common practice, so you can see what each shortcut buys; ', complyPill('n/a'), ' does not apply to this job.'),
    sec('Baseline', 'Always sized first when it is not done yet: every lever starts from it.',
      ...vs.filter(v => v.name === 'strict').map(v => vrow(v, el('input', { type: 'checkbox', checked: true, disabled: true })))),
    sec('Each lever alone', 'Re-sized from the strict design with one switch set.', ...levers.map(v => vrow(v, tick(v)))),
    comp ? sec('All compliant levers together', ladder.length
      ? `On the full ladder this is the ladder’s last compliant step (${(by[comp.alias] || {}).label || comp.alias || '—'}): ticking it runs the ladder up to that step.`
      : (comp.alias ? `Only one lever is compliant here: the same design as “${(by[comp.alias] || {}).label || comp.alias}”.` : 'Every compliant lever set at once, sized from the strict design.'),
      vrow(comp, tick(comp))) : null,
    ladder.length ? sec('Cumulative ladder (the waterfall)', 'One lever added per step, each step from the previous step’s design: the compliant levers first, then the readings, then the not-compliant ones. Runs as a whole.',
      el('label', { class: 'vrow' }, ladderCb, el('div', { class: 'vt' }, el('b', {}, 'Run the ladder'),
        el('span', { class: 'vn' }, `${ladder.filter(v => v.applies).length} steps; the first copies its lever alone.`))),
      el('div', { class: 'steps2' }, ...ladder.map(v => el('div', { class: 'vstep' + (v.applies ? '' : ' na') }, complyPill(v.compliance, v.status), el('span', {}, v.label))))) : null,
    vs.some(v => v.name === 'market') ? sec('Market practice', ladder.length
      ? 'Every lever above plus the market-practice loads. It starts from the last step of the ladder when the ladder is in the study, otherwise from the strict design.'
      : 'Every lever plus the market-practice loads, sized from the all-compliant design when that is in the study, otherwise from the strict design.',
      ...vs.filter(v => v.name === 'market').map(v => vrow(v, tick(v)))) : null,
    vs.some(v => v.name === 'solar_ready') ? sec('Solar-ready', 'IN_Ex1 only: the published design strengthened for rooftop PV (nothing lighter than the gold).',
      ...vs.filter(v => v.name === 'solar_ready').map(v => vrow(v, tick(v)))) : null));

  // ---- run
  const maxIn = el('input', { type: 'number', min: 1, max: P.max_runs_cap || 30, value: S.pebOpts.max_runs, disabled: running, onchange: e => { S.pebOpts.max_runs = Number(e.target.value); paintCount(); } });
  const resumeCb = el('input', { type: 'checkbox', checked: !!S.pebOpts.resume, disabled: running, onchange: e => { S.pebOpts.resume = e.target.checked; paintCount(); } });
  const budgetIn = cli.stop_after_min ? el('input', { type: 'number', min: 0, step: 'any', placeholder: 'no limit', value: S.pebOpts.stop_after_min, disabled: running,
    onchange: e => { S.pebOpts.stop_after_min = e.target.value; } }) : null;
  const stop = el('button', { class: 'danger', disabled: !running }, 'Stop');
  runBtn.onclick = async () => {
    runBtn.disabled = true;
    try {
      const body = { variants: [...S.pebSel], max_runs: Number(S.pebOpts.max_runs) || 8, resume: !!S.pebOpts.resume, ladder: S.pebOpts.ladder };
      if (cli.stop_after_min && String(S.pebOpts.stop_after_min).trim() !== '') body.stop_after_min = Number(S.pebOpts.stop_after_min);
      const d = await api(`/api/project/${enc(S.project)}/peb-study/run`, { method: 'POST', body });
      S.pebLog = []; S.pebSeq = 0;
      toast(`PEB lever study started: ${d.expected} variant(s) to size`, 'ok');
      await refreshPeb(); attachPebEvents(); render();
    } catch (e) { toast(e.message, 'bad'); paintCount(); }
  };
  stop.onclick = async () => { try { await api(`/api/project/${enc(S.project)}/peb-study/stop`, { method: 'POST' }); toast('stopping the lever study'); } catch (e) { toast(e.message, 'bad'); } };
  const prog = el('div', { class: 'hint' }), bar = el('i', { style: 'width:0%' });
  const logBox = el('div', { class: 'log', id: 'peblog' });
  pane.append(el('div', { class: 'card' },
    el('div', { class: 'row' }, el('label', { class: 'rule' }, el('span', {}, 'Full runs per variant (at most)'), maxIn),
      budgetIn ? el('label', { class: 'rule', title: 'the runner’s --stop-after-min: the variant in hand is never cut; run again with the rows kept to continue' },
        el('span', {}, 'Start no new variant after (minutes)'), budgetIn) : null,
      el('label', { class: 'rule' }, resumeCb, el('span', {}, 'Keep the rows already done (untick to start the study again)'))),
    el('div', { class: 'row', style: 'margin-top:10px' }, runBtn, stop, el('span', { class: 'sp' }), count), ladderWarn,
    el('p', { class: 'hint', style: 'margin-top:6px' }, 'Each full run is a complete analysis and design of the shed (minutes); a whole study takes hours. One study at a time on this PC; closing this tab does not stop it — reopen it to re-attach.'),
    el('div', { class: 'progress' }, bar), prog, logBox));
  S.pebView = { prog, bar };
  pane.append(pebResults(P));
  main.append(pane);
  paintCount(); paintPebLive(); paintPebLog();          // after the pane is in the page: the log is found by id
}
function paintPebLive() {
  const v = S.pebView, P = S.peb || {}; if (!v) return;
  const g = P.progress, r = P.run;
  if (P.running && g) { v.bar.style.width = `${100 * Math.max(0, g.n - 1) / Math.max(1, g.of)}%`; v.prog.textContent = `variant ${g.n} of ${g.of}: ${g.label || '…'} · ${Math.round(g.elapsed_s / 60)} min elapsed`; }
  else if (r && r.status) {
    v.bar.style.width = r.status === 'finished' ? '100%' : v.bar.style.width;
    const more = { interrupted: ' — the module server stopped under it; run again with “keep the rows already done” to pick it up',
      paused: ` — ${r.note || 'time budget reached'}: run again with “keep the rows already done” to continue` }[r.status] || '';
    v.prog.textContent = `last run: ${r.status}${r.rc !== null && r.rc !== undefined ? ' (exit code ' + r.rc + ')' : ''}${r.finished_at ? ', ' + fmt.when(r.finished_at) : ''}${more}`;
  }
  else v.prog.textContent = '';
}
function paintPebLog() {
  const box = $('#peblog'); if (!box) return;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.replaceChildren(...S.pebLog.map(l => el('div', { class: 'l ' + l.k }, l.t)));
  if (stick) box.scrollTop = box.scrollHeight;
}
function pebResults(P) {
  const rows = P.rows || [], outs = P.outputs || [];
  const link = o => el('a', { href: `/api/project/${enc(S.project)}/peb-study/file/${enc(o.name)}`, target: '_blank', title: o.label }, o.name);
  return el('div', { class: 'card', id: 'pebresults' },
    el('div', { class: 'row' }, el('h3', { style: 'margin:0' }, 'Results'), el('span', { class: 'sp' }),
      outs.some(o => o.name === 'study.html') ? el('a', { class: 'btnlink', href: `/api/project/${enc(S.project)}/peb-study/file/study.html`, target: '_blank' }, 'Open study.html') : null),
    outs.length ? el('div', { class: 'outs' }, ...outs.map(o => el('div', {}, link(o), el('span', { class: 'hint' }, ` — ${o.label} · ${fmt.num(o.bytes / 1024, 1)} kB`)))) :
      el('p', { class: 'hint' }, 'No outputs yet: study.html, study.csv and study.json appear here as the study writes them (after each variant).'),
    rows.length ? el('div', { class: 'tablewrap', style: 'margin-top:10px' }, el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Variant'), el('th', {}, 'Status'), el('th', { class: 'num' }, 'kg/m²'), el('th', { class: 'num' }, 'vs strict'),
        el('th', {}, 'Governing check / note'), el('th', { class: 'num' }, 'Full runs'))),
      el('tbody', {}, ...rows.map(r => el('tr', { class: r.compliance === 'not compliant' ? 'nc' : (r.compliance === 'n/a' ? 'na' : '') },
        el('td', { class: 'title' }, r.label), el('td', {}, complyPill(r.compliance, r.status), r.compliance !== 'compliant' && r.compliance !== 'n/a' ? el('div', { class: 'hint' }, r.status) : null),
        el('td', { class: 'num' }, r.kg_m2 === null || r.kg_m2 === undefined ? '—' : r.kg_m2.toFixed(2)),
        el('td', { class: 'num' }, r.delta_vs_strict_kg_m2 === null || r.delta_vs_strict_kg_m2 === undefined ? '' : (r.delta_vs_strict_kg_m2 > 0 ? '+' : '') + r.delta_vs_strict_kg_m2.toFixed(2)),
        el('td', { class: 'why' + (r.error ? '' : ' ok') }, r.error || r.governing || (r.compliance === 'n/a' ? r.note : '')),
        el('td', { class: 'num' }, r.alias_of ? `= ${r.alias_of}` : (r.n_full_runs ?? ''))))))) : null);
}
function attachPebEvents() {
  if (S.pebEs) { S.pebEs.close(); S.pebEs = null; }
  const es = new EventSource(`/api/project/${enc(S.project)}/peb-study/events?since=${S.pebSeq}`);
  S.pebEs = es;
  es.onmessage = async (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
    if (ev.seq) S.pebSeq = ev.seq;
    if (ev.type === 'finished') {
      es.close(); S.pebEs = null;
      if (ev.idle) return;
      S.pebLog.push({ k: ev.status === 'finished' ? 'ok' : (ev.status === 'paused' ? 'var' : 'err'),
        t: `■ lever study ${ev.status || 'ended'}${ev.rc !== null && ev.rc !== undefined ? ' (exit code ' + ev.rc + ')' : ''}${ev.note ? ': ' + ev.note : ''}` });
      await refreshPeb(); if (S.step === 'peb') render();
      return;
    }
    if (ev.type === 'variant') {
      S.pebLog.push({ k: 'var', t: `▶ ${ev.n}/${ev.of} ${ev.label}${ev.resumed ? ' — done already, kept' : ''}` });
      await refreshPeb();
      if (S.step === 'peb') { paintPebLive(); const old = $('#pebresults'); if (old) old.replaceWith(pebResults(S.peb)); }
    }
    else if (ev.type === 'start') S.pebLog.push({ k: 'var', t: ev.text });
    else if (ev.type === 'log') S.pebLog.push({ k: /Traceback|Error|run failed|NOT feasible|no passing design/.test(ev.text || '') ? 'err' : 'l', t: ev.text });
    if (S.pebLog.length > 1500) S.pebLog.splice(0, 300);
    paintPebLog();
  };
  es.onerror = () => { /* the browser retries; a finished run answers with `finished` and we close */ };
}

load().catch(e => { document.body.append(el('div', { class: 'pane', style: 'padding:30px;color:var(--bad)' }, 'The variations module could not load: ' + e.message)); });
