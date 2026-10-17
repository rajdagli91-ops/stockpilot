/* StockPilot dashboard (static site).
   Reads data/universe.json, data/picks.json, data/stocks/<SYM>.json produced by the engine.
   Portfolios, GitHub token and Claude key live only in this browser (localStorage). */
'use strict';

// ================================================================ storage & state
const LS = {
  get(k, d) { try { const v = localStorage.getItem('sp.' + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem('sp.' + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
};
let META = {}, ROWS = [], BY = new Map(), PICKS = [], WAIT = [], MARKET = {}, EVENTS = [], IPOS = [], IPO_UPDATED = '';
const detailCache = new Map();
let portfolios = LS.get('portfolios', [{ id: 'p1', name: 'My portfolio', holdings: [] }]);
let activePf = LS.get('activePf', portfolios[0]?.id);
let aliases = LS.get('aliases', {});
const guess = (() => { const h = location.hostname.match(/^([^.]+)\.github\.io$/); return h ? { owner: h[1], repo: location.pathname.split('/')[1] || '' } : {}; })();
const settings = Object.assign({ model: 'claude-haiku-5-5', key: '', maxTokens: 350, ghOwner: guess.owner || '', ghRepo: guess.repo || '', ghToken: '', pass: '' }, LS.get('settings', {}));
const saveSettings = () => LS.set('settings', settings);
const savePf = () => { LS.set('portfolios', portfolios); LS.set('pfUpdated', Date.now()); scheduleCloudSave(); };

// ================================================================ helpers
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const f0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const f2 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });
const n = (x, f = f2) => x == null || isNaN(x) ? '—' : f.format(x);
const pc = (x, d = 1) => x == null || isNaN(x) ? '—' : (x > 0 ? '+' : '') + (+x).toFixed(d) + '%';
const inr = x => x == null ? '—' : (Math.abs(x) >= 1e7 ? '₹' + (x / 1e7).toFixed(2) + ' Cr' : Math.abs(x) >= 1e5 ? '₹' + (x / 1e5).toFixed(2) + ' L' : '₹' + f0.format(x));
const pill = s => `<span class="pill ${esc((s || 'NO').split(' ')[0].replace(/[^A-Z]/g, ''))}">${esc(s || 'NO DATA')}</span>`;
const scoreColor = v => v == null ? 'var(--nodata)' : v >= 66 ? 'var(--buy)' : v >= 45 ? 'var(--hold)' : 'var(--sell)';
const bar = v => `<div class="bar"><i><b style="width:${v ?? 0}%;background:${scoreColor(v)}"></b></i><span>${v == null ? '—' : Math.round(v)}</span></div>`;
const norm = s => String(s || '').toUpperCase().replace(/LIMITED|LTD\.?|\bEQ\b|EQUITY|\(I\)|INDIA|&|[^A-Z0-9]/g, '');
const fileOf = sym => 'data/stocks/' + encodeURIComponent(sym.replace(/&/g, '_')) + '.json';
const target = () => META.target_cagr || 18;
const heldSet = () => new Set(portfolios.flatMap(p => p.holdings.map(h => h.symbol)));
// Hold / Exit only make sense for stocks you own; anything else is Buy or Not a buy.
const sigLabel = (sym, sig) => !sig || sig === 'SCAN' || sig === 'NO DATA' ? (sig || 'NO DATA') : sig === 'BUY' ? 'BUY' : heldSet().has(sym) ? (sig === 'SELL' ? 'EXIT' : 'HOLD') : 'NOT A BUY';
const hzText = h => h ? `${h.term} · ${h.period}` : '';
const entryHtml = e => !e ? '' : `<div class="entry ${e.score >= 65 ? 'good' : e.score >= 50 ? 'fair' : 'wait'}"><div><b>Entry timing ${e.score}/100</b> · ${esc(e.verdict)}</div>${(e.good || []).length ? `<div class="hint">✓ ${esc(e.good.join(' · '))}</div>` : ''}${(e.why || []).length ? `<div class="hint">✗ ${esc(e.why.join(' · '))}</div>` : ''}${e.score < 65 && e.zone ? `<div class="hint">Buy zone near the 50-day avg: ₹${n(e.zone[0], f0)}–${n(e.zone[1], f0)}</div>` : ''}</div>`;
const hzLine = h => {
  if (!h) return '';
  const bits = [];
  if (h.expected != null && h.term !== 'Exit') bits.push((h.term === 'Short term' ? 'upside ' : 'expected ') + pc(h.expected) + (h.term === 'Short term' ? '' : '/yr'));
  if (h.target) bits.push('target ₹' + n(h.target, f0));
  if (h.stop) bits.push('stop-loss ₹' + n(h.stop, f0));
  return `<div class="hz"><b>${esc(hzText(h))}</b>${bits.length ? ' · ' + esc(bits.join(' · ')) : ''}</div>`;
};
const stageLabel = s => ({ 1: 'Price scan only', 2: 'Fundamentals + technicals', 3: 'Full: + news & filings' }[s] || 'Not analysed');
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 6000); }

async function loadData(bust = false) {
  const q = bust ? '?t=' + Date.now() : '';
  try {
    const [u, p] = await Promise.all([fetch('data/universe.json' + q, { cache: 'no-cache' }).then(r => r.json()), fetch('data/picks.json' + q, { cache: 'no-cache' }).then(r => r.json())]);
    META = u.meta || {}; ROWS = u.rows.map(r => Object.fromEntries(u.fields.map((f, i) => [f, r[i]]))); PICKS = p.picks || []; WAIT = p.wait || [];
  } catch { META = {}; ROWS = []; PICKS = []; WAIT = []; }
  const opt = f => fetch('data/' + f + q, { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).catch(() => null);
  const [m, e, i] = await Promise.all([opt('market.json'), opt('events.json'), opt('ipos.json')]);
  MARKET = m || {}; EVENTS = e?.events || []; IPOS = i?.ipos || []; IPO_UPDATED = i?.updated || '';
  BY = new Map(ROWS.map(r => [r.symbol, r]));
  detailCache.clear();
  $('#updated').textContent = META.updated ? 'Data ' + META.updated : '';
  const b = $('#banner');
  if (!ROWS.length) b.innerHTML = '<div class="banner">No data yet. Click <b>Fetch now</b> (needs GitHub set up in Settings) or run the workflow in GitHub Actions.</div>';
  else if (META.sample) b.innerHTML = '<div class="banner"><b>Sample data.</b> Made-up test numbers. Run a full scan to load real market data.</div>';
  else b.innerHTML = '';
}
async function loadDetail(sym) {
  if (detailCache.has(sym)) return detailCache.get(sym);
  const r = await fetch(fileOf(sym), { cache: 'no-cache' });
  if (!r.ok) return null;
  const d = await r.json(); detailCache.set(sym, d); return d;
}

// ================================================================ GitHub: fetch now / analyse / sync
const ghReady = () => settings.ghOwner && settings.ghRepo && settings.ghToken;
async function gh(path, opts = {}) {
  const r = await fetch(`https://api.github.com/repos/${settings.ghOwner}/${settings.ghRepo}${path}`, {
    ...opts, headers: { Accept: 'application/vnd.github+json', Authorization: 'Bearer ' + settings.ghToken, 'X-GitHub-Api-Version': '2022-11-28', ...(opts.headers || {}) } });
  if (!r.ok && r.status !== 204) { let m = r.statusText; try { m = (await r.json()).message; } catch { } throw new Error(`GitHub ${r.status}: ${m}`); }
  return r.status === 204 ? null : r.json();
}
async function dispatch(mode, symbols = '') {
  if (!ghReady()) { toast('Set up GitHub in Settings first.'); location.hash = 'settings'; return; }
  try {
    await gh('/actions/workflows/update.yml/dispatches', { method: 'POST', body: JSON.stringify({ ref: 'main', inputs: { mode, symbols } }) });
    LS.set('run', { since: Date.now(), mode, symbols });
    toast({ full: 'Full market scan started (about 45 minutes).', symbols: `Analysing ${symbols}… (2–3 minutes)`, portfolio: 'Fetching your portfolio stocks (about 3 minutes)…' }[mode] || 'Fetching fresh data (about 5 minutes)…');
    pollRun();
  } catch (e) { toast('Could not start: ' + e.message); }
}
let pollTimer = null;
async function pollRun() {
  clearTimeout(pollTimer);
  const job = LS.get('run', null), st = $('#runstate');
  if (!job || !ghReady()) { st.textContent = ''; $('#fetch').disabled = false; return; }
  $('#fetch').disabled = true;
  try {
    const j = await gh('/actions/workflows/update.yml/runs?per_page=5&event=workflow_dispatch');
    const run = (j.workflow_runs || []).find(r => new Date(r.created_at).getTime() >= job.since - 90000);
    if (!run) st.textContent = 'Starting…';
    else if (run.status !== 'completed') {
      const mins = Math.max(0, Math.round((Date.now() - new Date(run.run_started_at || run.created_at)) / 60000));
      st.innerHTML = `${run.status === 'queued' ? 'Queued' : 'Running'} · ${mins} min · <a href="${esc(run.html_url)}" target="_blank" rel="noopener">details</a>`;
    } else {
      LS.set('run', null); $('#fetch').disabled = false; REPORTS = null;
      if (run.conclusion === 'success') {
        st.textContent = 'Updated';
        setTimeout(async () => { await loadData(true); go(); toast('Fresh results loaded.'); }, 15000);
      } else { st.innerHTML = `Run ${esc(run.conclusion)} · <a href="${esc(run.html_url)}" target="_blank" rel="noopener">see log</a>`; }
      return;
    }
  } catch (e) { st.textContent = e.message; }
  pollTimer = setTimeout(pollRun, 15000);
}
// ---- encryption (matches engine/secure.py: PBKDF2-SHA256 200k -> AES-256-GCM)
const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = str => Uint8Array.from(atob(str), c => c.charCodeAt(0));
async function deriveKey(pass, salt, iter) {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' }, base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
async function encryptObj(obj, pass) {
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await deriveKey(pass, salt, 200000), new TextEncoder().encode(JSON.stringify(obj)));
  return { v: 1, iter: 200000, salt: b64(salt), iv: b64(iv), ct: b64(ct) };
}
async function decryptObj(blob, pass) {
  const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(blob.iv) }, await deriveKey(pass, unb64(blob.salt), blob.iter || 200000), unb64(blob.ct));
  return JSON.parse(new TextDecoder().decode(pt));
}
const utf8b64 = str => btoa(String.fromCharCode(...new TextEncoder().encode(str)));
const b64utf8 = str => new TextDecoder().decode(unb64(str.replace(/\n/g, '')));

// ---- cloud save of portfolios (encrypted file in your repo; the system reads it for scheduled reports)
const cloudReady = () => ghReady() && settings.pass;
let cloudTimer = null;
function scheduleCloudSave() { if (!cloudReady()) return; clearTimeout(cloudTimer); cloudTimer = setTimeout(() => cloudSave(true), 2500); }
async function cloudSave(quiet = false) {
  if (!cloudReady()) { if (!quiet) { toast('Set GitHub and a passphrase in Settings to save to the cloud.'); location.hash = 'settings'; } return false; }
  try {
    const path = '/contents/config/portfolios.enc.json';
    let sha; try { sha = (await gh(path)).sha; } catch { sha = undefined; }
    const blob = await encryptObj({ updated: LS.get('pfUpdated', Date.now()), portfolios, aliases }, settings.pass);
    await gh(path, { method: 'PUT', body: JSON.stringify({ message: 'Update encrypted portfolios', content: utf8b64(JSON.stringify(blob)), ...(sha ? { sha } : {}) }) });
    LS.set('cloudSaved', Date.now()); const el = $('#cloudstate'); if (el) el.textContent = 'Saved to cloud just now';
    if (!quiet) toast('Portfolios saved (encrypted). Scheduled reports will use them.');
    return true;
  } catch (e) { if (!quiet) toast('Cloud save failed: ' + e.message); const el = $('#cloudstate'); if (el) el.textContent = 'Cloud save failed: ' + e.message; return false; }
}
async function cloudLoad(force = false) {
  if (!cloudReady()) return false;
  try {
    const f = await gh('/contents/config/portfolios.enc.json');
    const data = await decryptObj(JSON.parse(b64utf8(f.content)), settings.pass);
    const localEmpty = !portfolios.some(p => p.holdings.length);
    if (force || localEmpty || (data.updated || 0) > LS.get('pfUpdated', 0)) {
      portfolios = data.portfolios || []; aliases = data.aliases || aliases;
      LS.set('portfolios', portfolios); LS.set('aliases', aliases); LS.set('pfUpdated', data.updated || Date.now());
      if (!portfolios.find(p => p.id === activePf)) activePf = portfolios[0]?.id;
      return true;
    }
  } catch (e) { if (force) toast('Cloud load failed: ' + (String(e.message).includes('404') ? 'nothing saved yet' : e.name === 'OperationError' ? 'wrong passphrase' : e.message)); }
  return false;
}
let REPORTS = null;
async function loadReports() {
  if (!settings.pass) return null;
  try {
    const r = await fetch('data/report.enc.json?t=' + Date.now(), { cache: 'no-cache' });
    if (!r.ok) return null;
    REPORTS = (await decryptObj(await r.json(), settings.pass)).history || [];
  } catch (e) { REPORTS = e.name === 'OperationError' ? 'badpass' : null; }
  return REPORTS;
}

// ================================================================ smart search
function searchRows(q) {
  q = q.trim(); if (!q) return [];
  const up = q.toUpperCase(), lo = q.toLowerCase();
  const words = lo.split(/\s+/);
  const sigWord = words.find(w => ['buy', 'hold', 'sell'].includes(w));
  const rest = words.filter(w => w !== sigWord).join(' ');
  const scored = [];
  for (const r of ROWS) {
    if (sigWord && r.signal !== sigWord.toUpperCase()) continue;
    const name = (r.name || '').toLowerCase(), sec = (r.sector || '').toLowerCase();
    let s = 0;
    if (!rest) s = 1 + (r.overall || 0) / 100;
    else if (r.symbol === up) s = 100;
    else if (r.symbol.startsWith(rest.toUpperCase())) s = 60;
    else if (name.startsWith(rest)) s = 50;
    else if (name.includes(rest)) s = 30;
    else if (sec.includes(rest)) s = 10 + (r.overall || 0) / 100;
    if (s) scored.push([s + (r.stage || 0) * 0.1, r]);
  }
  return scored.sort((a, b) => b[0] - a[0]).slice(0, 8).map(x => x[1]);
}
function setupSearch() {
  const inp = $('#search'), box = $('#results');
  let idx = -1;
  const render = () => {
    const items = searchRows(inp.value);
    const q = inp.value.trim().toUpperCase();
    const looksSym = /^[A-Z0-9&-]{2,20}$/.test(q);
    const exact = items.some(r => r.symbol.startsWith(q)) || items.some(r => (r.name || '').toUpperCase().startsWith(q));
    box.innerHTML = items.map((r, i) => `<a href="#stock/${encodeURIComponent(r.symbol)}" class="res ${i === idx ? 'on' : ''}" role="option">
        <b>${esc(r.symbol)}</b><span class="nm">${esc(r.name || '')}</span>${r.stage >= 2 ? pill(sigLabel(r.symbol, r.signal)) + `<span class="sc">${Math.round(r.overall ?? 0)}</span>` : '<span class="muted" style="font-size:12px">scan only</span>'}</a>`).join('')
      + (q && !exact && looksSym ? `<a href="#stock/${encodeURIComponent(q)}" class="res ${idx === items.length ? 'on' : ''}"><b>${esc(q)}</b><span class="nm">Analyse this symbol</span></a>` : '')
      + (inp.value.trim() && !items.length && !looksSym ? '<div class="res muted">No match. Try a symbol, company name, sector, or "buy banking".</div>' : '');
    box.hidden = !box.innerHTML;
  };
  inp.addEventListener('input', () => { idx = -1; render(); });
  inp.addEventListener('focus', render);
  inp.addEventListener('keydown', e => {
    const links = [...box.querySelectorAll('a.res')];
    if (e.key === 'ArrowDown') { idx = Math.min(links.length - 1, idx + 1); render(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { idx = Math.max(-1, idx - 1); render(); e.preventDefault(); }
    else if (e.key === 'Enter') { const a = links[idx >= 0 ? idx : 0]; if (a) { location.hash = a.getAttribute('href').slice(1); inp.value = ''; box.hidden = true; inp.blur(); } }
    else if (e.key === 'Escape') { box.hidden = true; inp.blur(); }
  });
  box.addEventListener('click', () => { inp.value = ''; box.hidden = true; });
  document.addEventListener('click', e => { if (!e.target.closest('.search')) box.hidden = true; });
  document.addEventListener('keydown', e => { if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { e.preventDefault(); inp.focus(); } });
}

// ================================================================ router
const routes = { picks: viewPicks, market: viewMarket, ipos: viewIpos, screener: viewScreener, stock: viewStock, portfolios: viewPortfolios, settings: viewSettings };
function go() {
  const [name, arg] = (location.hash.slice(1) || 'picks').split('/');
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === name));
  (routes[name] || viewPicks)(decodeURIComponent(arg || ''));
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', go);

// ================================================================ portfolio analysis (shared)
function pfStats(p) {
  let inv = 0, val = 0, wS = 0, wSv = 0, wE = 0, wEv = 0;
  const rows = p.holdings.map(h => {
    const s = BY.get(h.symbol), px = s?.price ?? null, i = h.qty * h.avg, v = px != null ? h.qty * px : i;
    inv += i; val += v;
    if (s?.overall != null && s.stage >= 2) { wS += s.overall * v; wSv += v; }
    if (s?.expected_return != null) { wE += s.expected_return * v; wEv += v; }
    return { ...h, s, px, inv: i, val: v, priced: px != null };
  });
  rows.forEach(r => r.w = val ? r.val / val : 0);
  return { rows, inv, val, gain: val - inv, score: wSv ? wS / wSv : null, er: wEv ? wE / wEv : null };
}
function dailyReport(p) {
  const st = pfStats(p), dataDate = (META.updated || '').slice(0, 10);
  const snaps = LS.get('snap', {}), sn = snaps[p.id] || {};
  const cur = Object.fromEntries(st.rows.filter(r => r.s?.stage >= 2).map(r => [r.symbol, r.s.signal]));
  if (dataDate && sn.curDate !== dataDate) { sn.prev = sn.cur || {}; sn.prevDate = sn.curDate; sn.cur = cur; sn.curDate = dataDate; snaps[p.id] = sn; LS.set('snap', snaps); }
  const changes = Object.entries(cur).filter(([k, v]) => sn.prev && sn.prev[k] && sn.prev[k] !== v).map(([k, v]) => ({ sym: k, from: sn.prev[k], to: v }));
  const acts = [];
  const sells = st.rows.filter(r => r.s?.stage >= 2 && r.s.signal === 'SELL').sort((a, b) => b.w - a.w);
  if (sells.length) acts.push(['bad', `${sells.length} holdings rated EXIT (${(sells.reduce((a, r) => a + r.w, 0) * 100).toFixed(1)}% of value)`, sells.slice(0, 8).map(r => r.symbol)]);
  const weak = st.rows.filter(r => r.s?.expected_return != null && r.s.expected_return < 12 && r.s.signal !== 'SELL').sort((a, b) => b.w - a.w);
  if (weak.length) acts.push(['warn', `${weak.length} holdings expected to return under 12%/yr, below your 15% long-term target`, weak.slice(0, 8).map(r => r.symbol)]);
  const big = st.rows.filter(r => r.w > 0.10);
  if (big.length) acts.push(['warn', 'Single stocks above 10% of the portfolio', big.map(r => `${r.symbol} ${(r.w * 100).toFixed(0)}%`)]);
  const tiny = st.rows.filter(r => r.w < 0.01);
  if (tiny.length > 5) acts.push(['warn', `${tiny.length} positions under 1% each (${(tiny.reduce((a, r) => a + r.w, 0) * 100).toFixed(1)}% in total): too small to move returns`, tiny.slice(0, 10).map(r => r.symbol)]);
  const sec = {}; st.rows.forEach(r => { const k = r.s?.sector || 'Unknown'; sec[k] = (sec[k] || 0) + r.w; });
  Object.entries(sec).filter(([k, w]) => w > 0.25 && k !== 'Unknown').forEach(([k, w]) => acts.push(['warn', `${k} is ${(w * 100).toFixed(0)}% of the portfolio`, []]));
  const notDeep = st.rows.filter(r => !r.s || r.s.stage < 3);
  if (notDeep.length) acts.push(['info', `${notDeep.length} holdings lack a full daily analysis. Save portfolios to the cloud so they're analysed every day.`, notDeep.slice(0, 10).map(r => r.symbol), 'sync']);
  const held = new Set(st.rows.map(r => r.symbol));
  const ideas = PICKS.filter(x => !held.has(x.symbol));
  if (sells.length && ideas.length) acts.push(['ok', "Replacement ideas from today's top picks", ideas.map(x => x.symbol)]);
  return { st, changes, prevDate: sn.prevDate, acts };
}
function reportHtml(p, compact = false) {
  if (!p.holdings.length) return `<p class="muted">No holdings in "${esc(p.name)}" yet. <a href="#portfolios">Add or upload them</a>.</p>`;
  const R = dailyReport(p), st = R.st;
  const gap = st.er != null ? st.er - target() : null;
  return `
    <div class="tiles">
      <div class="tile"><div class="l">Value</div><div class="v">${inr(st.val)}</div></div>
      <div class="tile"><div class="l">Gain on cost</div><div class="v ${st.gain >= 0 ? 'pos' : 'neg'}">${pc(st.inv ? st.gain / st.inv * 100 : null)}</div></div>
      <div class="tile"><div class="l">Health score</div><div class="v" style="color:${scoreColor(st.score)}">${st.score == null ? '—' : st.score.toFixed(0)}<span class="muted" style="font-size:13px"> /100</span></div></div>
      <div class="tile"><div class="l">Expected return/yr</div><div class="v ${gap >= 0 ? 'pos' : 'neg'}">${pc(st.er)}</div><div class="hint">${gap == null ? '' : gap >= 0 ? 'On track for ' + target() + '%' : Math.abs(gap).toFixed(1) + ' pts short of ' + target() + '%'}</div></div>
    </div>
    ${R.changes.length ? `<div class="changes"><b>Signal changes since ${esc(R.prevDate || 'last update')}:</b> ${R.changes.map(c => `<a href="#stock/${encodeURIComponent(c.sym)}">${esc(c.sym)}</a> ${esc(c.from)} → ${pill(c.to)}`).join(' · ')}</div>` : ''}
    <ul class="acts">${R.acts.slice(0, compact ? 4 : 20).map(([lvl, msg, syms, act]) => `<li class="${lvl}"><span class="dot"></span><div>${esc(msg)}${syms.length ? '<div class="syms">' + syms.map(s => { const sym = String(s).split(' ')[0]; return `<a href="#stock/${encodeURIComponent(sym)}">${esc(s)}</a>`; }).join('') + '</div>' : ''}${act === 'sync' ? '<button class="btn ghost small" data-sync style="margin-top:6px">Save to cloud &amp; fetch</button>' : ''}</div></li>`).join('') || '<li class="ok"><span class="dot"></span><div>No issues flagged today.</div></li>'}</ul>`;
}
function wireSync(root) { root.querySelectorAll('[data-sync]').forEach(b => b.addEventListener('click', async () => { b.disabled = true; if (await cloudSave()) dispatch('portfolio'); b.disabled = false; })); }
async function cloudSaveIfDirty() { if (cloudReady() && LS.get('pfUpdated', 0) > LS.get('cloudSaved', 0)) return cloudSave(true); return true; }
function actionFor(row, weight) {
  if (!row || (row.stage || 0) < 2 || !row.signal || row.signal === 'SCAN') return 'NOT ANALYSED';
  if (row.signal === 'SELL') return 'EXIT';
  if (row.signal === 'BUY') return weight >= 0.08 ? 'HOLD (already large)' : row.horizon?.term === 'Short term' ? 'SHORT-TERM TRADE' : 'BUY MORE';
  if (row.expected_return != null && row.expected_return < 12) return 'HOLD / REVIEW';
  return 'HOLD';
}
function pointsHtml(pts, compact = false) {
  if (!pts) return '';
  const li = xs => xs.map(x => `<li>${esc(x)}</li>`).join('');
  return `<div class="points">${pts.verdict ? `<div class="pv">${esc(pts.verdict)}</div>` : ''}
    <div class="pcols">${pts.for?.length ? `<div><div class="ph pos">For</div><ul>${li(compact ? pts.for.slice(0, 2) : pts.for)}</ul></div>` : ''}
    ${pts.against?.length ? `<div><div class="ph neg">Against</div><ul>${li(compact ? pts.against.slice(0, 2) : pts.against)}</ul></div>` : ''}</div></div>`;
}
function claudeBox(id) { return `<div class="tools claudebox"><button class="btn small" data-ask="${esc(id)}">Analyse with Claude</button><button class="btn ghost small" data-copy="${esc(id)}">Copy prompt</button></div><div class="ai" data-out="${esc(id)}" hidden></div>`; }
function wireClaude(root, getPrompt) {
  root.querySelectorAll('[data-ask]').forEach(b => b.addEventListener('click', async e => { e.preventDefault(); e.stopPropagation(); const id = b.dataset.ask, out = root.querySelector(`[data-out="${CSS.escape(id)}"]`); out.hidden = false; askClaudeText(await getPrompt(id), out, b); }));
  root.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async e => { e.preventDefault(); e.stopPropagation(); const id = b.dataset.copy, out = root.querySelector(`[data-out="${CSS.escape(id)}"]`); const pr = await getPrompt(id); const txt = pr.system + '\n\n' + pr.user; out.hidden = false; try { await navigator.clipboard.writeText(txt); out.textContent = 'Copied. Paste it into Claude:\n\n' + txt; } catch { out.textContent = txt; } }));
}

// ================================================================ view: top picks (home)
function viewPicks() {
  const v = $('#view');
  const pf = portfolios.find(x => x.id === activePf) || portfolios[0];
  v.innerHTML = `
    <div><h1>Top ${PICKS.length || 5} picks today</h1>
      <p class="sub">${META.scanned ? `From ${f0.format(META.scanned)} NSE stocks scanned, ${f0.format(META.analysed || 0)} liquid ones analysed on fundamentals and technicals, ${META.deep || 0} checked for news and filings.` : 'No scan yet.'} ${META.last_full_scan ? 'Last full scan ' + esc(META.last_full_scan) + '.' : ''}</p></div>
    ${MARKET.mood != null ? `<a class="moodbar" href="#market"><span>Global markets</span><b style="color:${scoreColor(MARKET.mood)}">${MARKET.mood}/100 · ${esc(MARKET.bias)}</b><span class="muted">${esc((MARKET.markets || []).filter(x => ['sp500', 'nasdaq', 'nikkei', 'brent', 'usdinr'].includes(x.key)).map(x => x.label + ' ' + pc(x.chg_1d)).join(' · '))}</span></a>` : ''}
    ${PICKS.length ? `<div class="picks">${PICKS.map((x, i) => `
      <div class="pick">
        <a class="ph" href="#stock/${encodeURIComponent(x.symbol)}"><div><span class="rank">#${i + 1}</span> <b class="sym">${esc(x.symbol)}</b><div class="muted nm">${esc(x.name || '')}</div></div><div class="big" style="color:${scoreColor(x.overall)}">${Math.round(x.overall)}</div></a>
        <div class="pm">${pill(x.signal)} <span class="hint">${esc(x.confidence)} confidence · ${esc(x.sector || '')}</span></div>
        ${hzLine(x.horizon)}
        ${entryHtml(x.entry)}
        <dl><dt>Expected return</dt><dd class="${x.expected_return >= target() ? 'pos' : ''}">${pc(x.expected_return)}/yr</dd>
            <dt>P/E · PEG</dt><dd>${n(x.entry?.pe, f0)} · ${x.entry?.peg != null ? x.entry.peg.toFixed(1) : '—'}</dd>
            <dt>12 months</dt><dd>${pc(x.ret_12m)}</dd><dt>vs 50-day avg</dt><dd>${pc(x.entry?.vs_50dma)}</dd><dt>vs 200-day avg</dt><dd>${pc(x.vs_200dma)}</dd><dt>RSI</dt><dd>${n(x.rsi, f0)}</dd></dl>
        ${pointsHtml(x.points, true)}
        ${claudeBox(x.symbol)}
      </div>`).join('')}</div>
      <p class="hint">Picks need a BUY signal (business good enough for your return target), an entry-timing score of at least ${META.scan?.min_entry_score ?? 55} (price not stretched vs its averages, RSI not overheated, P/E reasonable for its growth, trend up), Medium or High confidence, at least ₹${META.scan?.min_traded_cr ?? 2} Cr traded daily, and at most ${META.scan?.max_picks_per_sector ?? 2} per sector. ${PICKS.length < 5 ? `Only ${PICKS.length} stocks cleared the bar; the system doesn't pad the list.` : ''}</p>`
      : `<div class="card empty">${ROWS.length ? "No stock cleared the BUY bar in the latest analysis. That happens in expensive or falling markets; the system won't force picks." : 'Run the first full scan to see picks.'}</div>`}
    ${WAIT.length ? `<div class="card"><div class="cardhead"><h2>Good businesses, wrong price today</h2><span class="hint">BUY-rated, but stretched right now. Buy near the zone, not at today's price.</span></div>
      <div class="tbox"><table><thead><tr><th>Stock</th><th class="num">Score</th><th class="num">Entry</th><th class="num">Price</th><th class="num">Buy zone</th><th class="hide-sm">Why wait</th></tr></thead><tbody id="waitb">
      ${WAIT.map(x => `<tr class="click" data-s="${esc(x.symbol)}"><td><b>${esc(x.symbol)}</b><div class="muted" style="font-size:12px">${esc((x.name || '').slice(0, 28))}</div></td><td class="num">${Math.round(x.overall)}</td><td class="num">${x.entry?.score ?? '—'}</td><td class="num">${n(x.price, f0)}</td><td class="num">${x.entry?.zone ? n(x.entry.zone[0], f0) + '–' + n(x.entry.zone[1], f0) : '—'}</td><td class="hide-sm" style="white-space:normal;min-width:200px">${esc((x.entry?.why || []).join('; '))}</td></tr>`).join('')}</tbody></table></div></div>` : ''}
    ${IPOS.filter(x => x.call === 'APPLY').length ? `<div class="card"><div class="cardhead"><h2>IPOs worth applying for</h2><a href="#ipos">All IPOs →</a></div><div class="syms">${IPOS.filter(x => x.call === 'APPLY').map(x => `<a href="#ipos">${esc(x.name)} · ${x.score}</a>`).join('')}</div></div>` : ''}
    <div class="card"><div class="cardhead"><h2>Your portfolio today: ${esc(pf?.name || '')}</h2><a href="#portfolios">Open portfolio →</a></div>${pf ? reportHtml(pf, true) : ''}</div>`;
  wireSync(v);
  v.querySelectorAll('#waitb tr[data-s]').forEach(tr => tr.addEventListener('click', () => { location.hash = 'stock/' + encodeURIComponent(tr.dataset.s); }));
  wireClaude(v, async sym => stockPrompt(await loadDetail(sym) || BY.get(sym)));
}

// ================================================================ view: screener
let sc = { q: '', sig: '', sector: '', min: 0, deep: true, key: 'overall', dir: -1 };
function viewScreener() {
  const v = $('#view');
  const sectors = [...new Set(ROWS.map(r => r.sector).filter(Boolean))].sort();
  v.innerHTML = `
    <div><h1>Screener</h1><p class="sub">Every analysed stock. Filter here, or use the search bar for any symbol.</p></div>
    <div class="tools">
      <input id="sq" type="search" placeholder="Filter by name" value="${esc(sc.q)}" style="flex:1 1 180px" aria-label="Filter by name">
      <select id="ssig" aria-label="Signal"><option value="">All stocks</option>${[['BUY', 'Buy (any term)'], ['BUYL', 'Buy · long term'], ['BUYS', 'Buy · short term'], ['NOT', 'Not a buy'], ['MINE', 'In my portfolios']].map(([v, l]) => `<option value="${v}" ${sc.sig === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <select id="ssec" aria-label="Sector"><option value="">All sectors</option>${sectors.map(s => `<option ${sc.sector === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>
      <label class="hint inl">Min score <input id="smin" type="number" min="0" max="100" value="${sc.min}" style="width:70px"></label>
      <label class="hint inl"><input id="sdeep" type="checkbox" ${sc.deep ? 'checked' : ''}> Analysed only</label>
    </div>
    <div class="tbox"><table id="st"><thead><tr>
      ${[['symbol', 'Stock'], ['signal', 'Signal'], ['overall', 'Score'], ['fundamental', 'Fundamental', 'hide-sm'], ['technical', 'Technical', 'hide-sm'], ['expected_return', 'Exp. return/yr', 'num'], ['ret_12m', '12m', 'num hide-sm'], ['traded_cr', '₹ Cr/day', 'num hide-sm'], ['price', 'Price', 'num hide-sm']]
        .map(([k, l, c]) => `<th class="sort ${c || ''}" data-k="${k}" aria-sort="${sc.key === k ? (sc.dir < 0 ? 'descending' : 'ascending') : 'none'}">${l}</th>`).join('')}
    </tr></thead><tbody></tbody></table></div><p class="hint" id="scount"></p>`;
  const draw = () => {
    const q = sc.q.toLowerCase();
    const mine = heldSet();
    const sigOk = r => !sc.sig || (sc.sig === 'BUY' ? r.signal === 'BUY' : sc.sig === 'BUYL' ? r.signal === 'BUY' && r.horizon?.term === 'Long term' : sc.sig === 'BUYS' ? r.signal === 'BUY' && r.horizon?.term === 'Short term' : sc.sig === 'NOT' ? r.signal !== 'BUY' : mine.has(r.symbol));
    const rows = ROWS.filter(r => (!sc.deep || r.stage >= 2) && sigOk(r) && (!sc.sector || r.sector === sc.sector) && (!sc.min || (r.overall || 0) >= sc.min) && (!q || `${r.symbol} ${r.name}`.toLowerCase().includes(q)))
      .sort((a, b) => { const x = a[sc.key], y = b[sc.key]; if (x == null) return 1; if (y == null) return -1; return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sc.dir; });
    $('#st tbody').innerHTML = rows.slice(0, 300).map(r => `<tr class="click" data-s="${esc(r.symbol)}">
      <td><b>${esc(r.symbol)}</b><div class="muted" style="font-size:12px">${esc((r.name || '').slice(0, 30))}</div></td>
      <td>${r.stage >= 2 ? pill(sigLabel(r.symbol, r.signal)) + (r.horizon && r.signal === 'BUY' ? `<div class="hint">${esc(hzText(r.horizon))}</div>` : '') : '<span class="muted">scan</span>'}</td><td>${bar(r.overall)}</td>
      <td class="hide-sm">${bar(r.fundamental)}</td><td class="hide-sm">${bar(r.technical)}</td>
      <td class="num ${r.expected_return >= target() ? 'pos' : r.expected_return < 12 ? 'neg' : ''}">${pc(r.expected_return)}</td>
      <td class="num hide-sm ${r.ret_12m >= 0 ? 'pos' : 'neg'}">${pc(r.ret_12m)}</td><td class="num hide-sm">${n(r.traded_cr, f0)}</td><td class="num hide-sm">${n(r.price)}</td></tr>`).join('') || '<tr><td colspan="9" class="empty">No stocks match.</td></tr>';
    $('#scount').textContent = rows.length > 300 ? `Showing 300 of ${rows.length}. Narrow the filters to see others.` : `${rows.length} stocks`;
  };
  draw();
  $('#sq').addEventListener('input', e => { sc.q = e.target.value; draw(); });
  $('#ssig').addEventListener('change', e => { sc.sig = e.target.value; draw(); });
  $('#ssec').addEventListener('change', e => { sc.sector = e.target.value; draw(); });
  $('#smin').addEventListener('input', e => { sc.min = +e.target.value || 0; draw(); });
  $('#sdeep').addEventListener('change', e => { sc.deep = e.target.checked; draw(); });
  $('#st thead').addEventListener('click', e => { const th = e.target.closest('th'); if (!th) return; const k = th.dataset.k; sc.dir = sc.key === k ? -sc.dir : (['symbol', 'signal'].includes(k) ? 1 : -1); sc.key = k;
    document.querySelectorAll('#st th').forEach(t => t.setAttribute('aria-sort', t.dataset.k === k ? (sc.dir < 0 ? 'descending' : 'ascending') : 'none')); draw(); });
  $('#st tbody').addEventListener('click', e => { const tr = e.target.closest('tr[data-s]'); if (tr) location.hash = 'stock/' + encodeURIComponent(tr.dataset.s); });
}

// ================================================================ price chart
function priceChart(weekly) {
  if (!weekly?.length) return '<p class="muted">No price history.</p>';
  const W = 640, H = 200, ml = 8, mr = 56, mt = 10, mb = 22;
  const ys = weekly.map(d => d[1]), lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.08 || 1;
  const x = i => ml + i / Math.max(1, weekly.length - 1) * (W - ml - mr), y = v => mt + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (H - mt - mb);
  const path = weekly.map((d, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(d[1]).toFixed(1)).join('');
  const col = ys[ys.length - 1] >= ys[0] ? 'var(--buy)' : 'var(--sell)';
  const labIdx = [0, Math.floor(weekly.length / 2), weekly.length - 1];
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly price, last 2 years">
    ${[lo, (lo + hi) / 2, hi].map(t => `<line x1="${ml}" x2="${W - mr}" y1="${y(t)}" y2="${y(t)}" stroke="var(--line)"/><text x="${W - mr + 6}" y="${y(t) + 4}">${n(t, f0)}</text>`).join('')}
    <path d="${path}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${x(weekly.length - 1)}" cy="${y(ys[ys.length - 1])}" r="3.5" fill="${col}"/>
    ${labIdx.map((i, j) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${['start', 'middle', 'end'][j]}">${esc(weekly[i][0].slice(0, 7))}</text>`).join('')}</svg>`;
}

// ================================================================ Claude (optional, compact)
function holdingFor(sym) { for (const p of portfolios) { const h = p.holdings.find(x => x.symbol === sym); if (h) return h; } return null; }
function compactPrompt(d) {
  const f = d.fundamentals || {}, t = d.technicals || {}, h = holdingFor(d.symbol);
  const r = v => v == null ? 'na' : Math.round(v * 10) / 10;
  return [
    `${d.symbol}|${d.name}|${d.sector || 'na'}|px ${r(d.price)}`,
    `PE ${r(f.pe)} PB ${r(f.pb)} ROE ${r(f.roe)} DE ${r(f.debt_equity)} DY ${r(f.dividend_yield)} RevCAGR ${r(f.rev_cagr)} PATCAGR ${r(f.profit_cagr)} OCF/PAT ${r(f.cash_conversion)}`,
    `vs200DMA ${r(t.vs_200dma)} rel12m ${r(t.rel_12m)} RSI ${r(t.rsi)} vol ${r(t.volatility)} off52wHi ${r(t.from_52w_high)}`,
    `model ${d.overall} ${d.signal} ${d.confidence} expRet ${r(d.expected_return)}` + (d.entry ? ` entry ${d.entry.score} PEG ${r(d.entry.peg)} vs50DMA ${r(d.entry.vs_50dma)}` : ''),
    h ? `held qty ${h.qty} avg ${h.avg}` : 'not held',
    'news: ' + (d.headlines || []).slice(0, 5).map(x => x.title.slice(0, 90)).join(' ; '),
    (d.filings || []).length ? 'filings: ' + d.filings.slice(0, 3).map(x => x.title.slice(0, 80)).join(' ; ') : '',
  ].filter(Boolean).join('\n');
}
const SYSTEM = 'Indian equity analyst. Investor: long-term, target 20% CAGR, max drawdown 30%. Data is compact; na=missing. Reply in <=120 words, plain text: Verdict BUY/HOLD/SELL + confidence %; 3 reasons; 1 key risk; 1 trigger that would change the view. Say if data is insufficient.';
const IPO_SYSTEM = 'Indian IPO analyst. Investor: long-term, target 20% CAGR, max drawdown 30%. Money in Rs crore; na=missing. Reply in <=120 words, plain text: APPLY/AVOID/NEUTRAL + confidence %; 3 reasons (business, valuation vs peers, financials); 1 key risk; say what to check in the RHP. Say if data is insufficient.';
const stockPrompt = d => ({ system: SYSTEM, user: d?.params ? compactPrompt(d) : `${d?.symbol}|${d?.name}|px ${d?.price}|12m ${d?.ret_12m}%|model ${d?.overall} ${d?.signal}` });
function ipoPrompt(x) {
  const r = v => v == null ? 'na' : Math.round(v * 10) / 10;
  return { system: IPO_SYSTEM, user: [
    `${x.name}|${x.board}|${x.sector || 'na'}|${x.industry || ''}|open ${x.open || 'na'} close ${x.close || 'na'}|band ${x.price_band || x.price_high || 'na'}|size ${r(x.issue_size_cr)}cr`,
    `revenue ${(x.revenue || []).map(r).join('/') || 'na'} | PAT ${(x.pat || []).map(r).join('/') || 'na'} | networth ${r(x.net_worth)} debt ${r(x.debt)} EPS ${r(x.eps)} OFS% ${r(x.ofs_pct)}`,
    x.peers ? `listed peers ${x.peers.count}: avg score ${x.peers.avg_score}, median PE ${x.peers.median_pe}, exp ret ${x.peers.avg_expected_return}%, best ${x.peers.best.join(',')}` : 'peers na',
    `model ${x.score ?? 'na'} ${x.call}`, (x.headlines || []).length ? 'news: ' + x.headlines.slice(0, 4).map(h => h.title.slice(0, 90)).join(' ; ') : ''].filter(Boolean).join('\n') };
}
async function askClaudeText(pr, out, btn) {
  if (!settings.key) { out.textContent = 'Add your Anthropic API key in Settings, or use "Copy prompt" and paste it into Claude.'; return; }
  btn.disabled = true; out.textContent = 'Asking Claude…';
  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': settings.key, 'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true' },
      body: JSON.stringify({ model: settings.model, max_tokens: +settings.maxTokens || 350, system: pr.system, messages: [{ role: 'user', content: pr.user }] }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error?.message || r.statusText);
    const u = j.usage || {};
    out.textContent = (j.content || []).map(c => c.text || '').join('') + `\n\n— ${settings.model} · ${u.input_tokens ?? '?'} in / ${u.output_tokens ?? '?'} out tokens`;
  } catch (e) { out.textContent = 'Claude request failed: ' + e.message; }
  btn.disabled = false;
}

// ================================================================ view: stock
async function viewStock(sym) {
  sym = sym.toUpperCase();
  const v = $('#view');
  v.innerHTML = `<p class="muted">Loading ${esc(sym)}…</p>`;
  const d = await loadDetail(sym), row = BY.get(sym), h = holdingFor(sym);
  if (!d) {
    v.innerHTML = `<div><a href="#picks">← Top picks</a><h1 style="margin-top:8px">${esc(row?.name || sym)} <span class="muted mono">${esc(sym)}</span></h1>
      <p class="sub">${row ? stageLabel(row.stage) + ' · price ₹' + n(row.price) + ' · 12m ' + pc(row.ret_12m) + ' · ₹' + n(row.traded_cr, f0) + ' Cr traded/day' : 'Not in the latest scan.'}</p></div>
      <div class="card"><h2>${row ? 'Not fully analysed yet' : 'No data for this symbol'}</h2>
        <p class="sub">${row ? 'The daily scan analyses fundamentals only for liquid stocks (at least ₹' + (META.scan?.min_traded_cr ?? 2) + ' Cr traded a day).' : 'Check that the NSE symbol is correct.'} Run a full analysis now; it takes 2–3 minutes and this page updates by itself.</p>
        <div class="tools" style="margin-top:12px"><button class="btn" id="analyse">Analyse ${esc(sym)} now</button></div></div>`;
    $('#analyse').addEventListener('click', () => dispatch('symbols', sym));
    return;
  }
  const groups = { fundamental: 'Fundamentals', technical: 'Technicals', news: 'News & filings' };
  v.innerHTML = `
    <div><a href="#picks">← Top picks</a><h1 style="margin-top:8px">${esc(d.name)} <span class="muted mono">${esc(d.symbol)}</span></h1>
      <p class="sub">${esc(d.sector || '')}${d.industry ? ' · ' + esc(d.industry) : ''} · ₹${n(d.price)} · ${stageLabel(d.stage)} · analysed ${esc(d.analysed || '')}</p></div>
    <div class="card verdict">
      <div class="big" style="color:${scoreColor(d.overall)}">${d.overall ?? '—'}</div>
      <div><div class="sig">${pill(sigLabel(d.symbol, d.signal))}</div>${d.signal === 'BUY' || h ? hzLine(d.horizon) : ''}${entryHtml(d.entry)}<div class="hint" style="margin-top:6px">Confidence <b>${esc(d.confidence)}</b> · data coverage ${Math.round((d.coverage || 0) * 100)}%</div></div>
      <div style="flex:1 1 260px;min-width:0"><div class="hint">Expected return vs your 15–18% target</div>
        <div class="mono ${d.expected_return >= target() ? 'pos' : 'neg'}" style="font-size:22px">${pc(d.expected_return)} / yr</div>
        <div class="hint">Strong: ${esc((d.strengths || []).join(', '))}<br>Weak: ${esc((d.weaknesses || []).join(', '))}</div></div>
      <button class="btn ghost" id="analyse">Refresh analysis</button>
    </div>
    ${h ? `<div class="banner" style="margin:0">You hold ${n(h.qty, f0)} shares at ₹${n(h.avg)} · P&L ${pc(d.price && h.avg ? (d.price / h.avg - 1) * 100 : null)} · Action: ${pill(actionFor(BY.get(d.symbol) || d, 0))}</div>` : ''}
    <div class="card"><h2>${h ? 'Buy more, hold or exit' : 'Buy or not'}: the reasons</h2>${pointsHtml(d.points ? { ...d.points, verdict: h ? d.points.verdict : (d.points.verdict_new || d.points.verdict) } : null) || '<p class="muted">No reasons yet.</p>'}</div>
    <div class="grid2">
      <div class="card"><h2>Parameter ratings</h2><p class="sub">Each rated 0–100. ×weight shows how much it counts.</p>
        <div class="params" style="margin-top:12px">${Object.entries(groups).map(([g, label]) => `<div class="grp">${label}</div>` +
          d.params.filter(p => p.group === g).map(p => `<div class="prow"><div>${esc(p.label)} <span class="muted" style="font-size:12px">×${p.weight}</span></div>
            <div class="val">${p.value == null ? 'no data' : n(p.value) + esc(p.unit)}</div>${bar(p.score)}</div>`).join('')).join('')}</div></div>
      <div style="display:flex;flex-direction:column;gap:16px;min-width:0">
        <div class="card"><h2>Price, last 2 years</h2>${priceChart(d.weekly)}</div>
        <div class="card"><h2>Ask Claude</h2><p class="sub">Optional. Sends a ~150-word summary of this stock only.</p>${claudeBox(d.symbol)}</div>
      </div>
    </div>
    <div class="grid2">
      <div class="card"><h2>News, last 30 days</h2><ul class="list" style="margin-top:12px">${(d.headlines || []).map(x => `<li><span class="tone" style="background:${x.tone > 0 ? 'var(--buy)' : x.tone < 0 ? 'var(--sell)' : 'var(--nodata)'}"></span><span class="d">${esc(x.date)}</span><a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a></li>`).join('') || `<li class="muted"><span>${d.stage < 3 ? 'News is checked for top candidates and your tracked stocks. Click Refresh analysis to check this one.' : 'No recent news found.'}</span></li>`}</ul></div>
      <div class="card"><h2>Exchange filings (BSE / NSE)</h2><ul class="list" style="margin-top:12px">${(d.filings || []).map(x => `<li>${x.red_flag ? '<span class="pill SELL">FLAG</span>' : ''}<span class="d">${esc(x.date)}${x.source ? ' · ' + esc(x.source) : ''}</span>${x.link ? `<a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}</li>`).join('') || "<li class=\"muted\"><span>No filings loaded. Filings need the stock's BSE code in <code>config/watchlist.csv</code>.</span></li>"}</ul></div>
    </div>
    ${d.errors?.length ? `<p class="hint">Data gaps: ${esc(d.errors.join(' · '))}</p>` : ''}`;
  $('#analyse').addEventListener('click', () => dispatch('symbols', sym));
  wireClaude(v, async () => stockPrompt(d));
}

// ================================================================ portfolios
function matchSymbol(raw) {
  if (!raw) return null;
  const s = String(raw).trim(), up = s.toUpperCase();
  if (aliases[up]) return aliases[up];
  if (BY.has(up)) return up;
  const k = norm(s);
  if (!k) return null;
  for (const r of ROWS) if (norm(r.name) === k || norm(r.symbol) === k) return r.symbol;
  const hits = ROWS.filter(r => { const nk = norm(r.name); return k.length >= 6 && nk && (nk.startsWith(k) || (k.startsWith(nk) && k.length - nk.length <= 3)); });
  return hits.length === 1 ? hits[0].symbol : null;
}
function parseRows(rows) {
  const QTY = /qty|quantity|no\.? ?of shares|shares|units/i;
  const hi = rows.findIndex(r => r.some(c => QTY.test(String(c ?? ''))));
  if (hi < 0) throw new Error('Could not find a Quantity column. Is this the holdings export (not P&L or tradebook)?');
  const hdr = rows[hi].map(c => String(c || '').toLowerCase());
  const col = re => hdr.findIndex(h => re.test(h));
  const cSym = col(/symbol|tradingsymbol|instrument/), cName = col(/security|stock|scrip|name|company/);
  const cQty = col(QTY), cAvg = col(/avg|average|buy price|cost price|purchase price|buy rate/);
  const cInv = col(/invested|investment|buy value|cost value|purchase value|total cost/);
  if (cAvg < 0 && cInv < 0) throw new Error('Could not find an Average price (or Invested value) column.');
  const out = [];
  for (const r of rows.slice(hi + 1)) {
    const num = v => parseFloat(String(v ?? '').replace(/[,₹\s]/g, ''));
    const qty = num(r[cQty]), avg = cAvg >= 0 ? num(r[cAvg]) : num(r[cInv]) / qty;
    const raw = (cSym >= 0 && r[cSym]) || (cName >= 0 && r[cName]);
    if (!raw || !(qty > 0) || !(avg > 0)) continue;
    out.push({ raw: String(raw).trim(), qty, avg });
  }
  return out;
}
async function readFile(file) {
  const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
  const binary = (head[0] === 0x50 && head[1] === 0x4b) || (head[0] === 0xd0 && head[1] === 0xcf);
  if (!binary) {
    const text = await file.text();
    return text.split(/\r?\n/).map(l => l.match(/("([^"]|"")*"|[^,]*)(,|$)/g)?.map(c => c.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"')) || []);
  }
  if (typeof XLSX === 'undefined') throw new Error('Excel reader did not load; save the file as CSV and upload that.');
  const wb = XLSX.read(await file.arrayBuffer(), { type: 'array' });
  return XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, raw: false });
}

let pfSort = { key: 'val', dir: -1 };
function viewPortfolios() {
  if (!portfolios.length) { portfolios = [{ id: 'p' + Date.now(), name: 'My portfolio', holdings: [] }]; savePf(); }
  if (!portfolios.find(p => p.id === activePf)) activePf = portfolios[0].id;
  const p = portfolios.find(x => x.id === activePf);
  const st = pfStats(p);
  const v = $('#view');
  v.innerHTML = `
    <div><h1>Portfolios</h1><p class="sub">Keep as many named portfolios as you like (e.g. "Raj – Portfolio 1", "Raj – Portfolio 2", "Father – A49E068"). They save in this browser automatically; save to the cloud for scheduled reports and other devices.</p></div>
    <div class="tools">
      <div class="tabs" id="pftabs">${portfolios.map(x => `<button data-id="${esc(x.id)}" class="${x.id === activePf ? 'on' : ''}">${esc(x.name)}</button>`).join('')}</div>
      <button class="btn ghost" id="pfnew">+ New portfolio</button><span style="flex:1"></span>
      <button class="btn ghost" id="pfren">Rename</button><button class="btn danger" id="pfdel">Delete</button>
      <button class="btn ghost" id="pfexp">Export</button><label class="btn ghost" style="cursor:pointer" title="Broker holdings file (.xlsx / .xls / .csv) becomes a new portfolio; a StockPilot backup (.json) restores everything">Import file<input type="file" id="pfimp" accept=".xlsx,.xls,.csv,.json" hidden></label>
    </div>
    <div class="card cloud"><div><b>Saved in this browser.</b> <span class="hint" id="cloudstate">${cloudReady() ? (LS.get('cloudSaved', 0) ? 'Cloud copy saved ' + new Date(LS.get('cloudSaved')).toLocaleString('en-IN') : 'Not saved to cloud yet') : 'Add GitHub + a passphrase in Settings to save an encrypted copy, so scheduled reports (pre-market, 1 PM, 3 PM) include your portfolios and other devices can load them.'}</span></div>
      <div class="tools"><button class="btn ghost small" id="csave">Save to cloud</button><button class="btn ghost small" id="cload">Load from cloud</button></div></div>
    <div class="card"><div class="cardhead"><h2>Today's analysis</h2><span class="hint">Data from ${esc(META.updated || '—')}</span></div>${reportHtml(p)}</div>
    <div class="card" id="reports"><div class="cardhead"><h2>Scheduled reports</h2><span class="hint">Pre-market, 1 PM and 3 PM, all portfolios</span></div><div id="replist" class="muted">${settings.pass ? 'Loading…' : 'Set your passphrase in Settings to read reports.'}</div></div>
    <div class="card"><h2>Add holdings</h2>
      <div class="row" style="margin-top:12px">
        <label>Symbol<input id="hsym" list="symlist" placeholder="e.g. INFY" autocomplete="off"></label>
        <label>Quantity<input id="hqty" type="number" min="0" step="any"></label>
        <label>Average price ₹<input id="havg" type="number" min="0" step="any"></label>
        <button class="btn" id="hadd">Add / update</button>
      </div>
      <datalist id="symlist">${ROWS.filter(r => r.stage >= 2).map(s => `<option value="${esc(s.symbol)}">${esc(s.name)}</option>`).join('')}</datalist>
      <div class="row" style="margin-top:14px;align-items:center">
        <label class="btn ghost" style="cursor:pointer;flex:none">Upload broker file (.xlsx / .csv)<input type="file" id="upl" accept=".xlsx,.xls,.csv" hidden></label>
        <span class="hint" style="flex:1 1 220px">Upload replaces the holdings of the portfolio selected above (${esc(p.name)}).</span>
      </div>
      <div id="unmatched"></div>
    </div>
    <div class="tbox"><table id="pt"><thead><tr>
      ${[['symbol', 'Stock'], ['signal', 'Signal'], ['score', 'Score'], ['qty', 'Qty', 'num hide-sm'], ['avg', 'Avg ₹', 'num hide-sm'], ['px', 'Price ₹', 'num hide-sm'], ['val', 'Value', 'num'], ['w', 'Weight', 'num'], ['er', 'Exp. ret', 'num hide-sm'], ['gainp', 'Gain %', 'num'], ['action', 'Action'], ['', '']]
        .map(([k, l, c]) => `<th class="${k ? 'sort' : ''} ${c || ''}" data-k="${k}" aria-sort="${pfSort.key === k ? (pfSort.dir < 0 ? 'descending' : 'ascending') : 'none'}">${l}</th>`).join('')}
    </tr></thead><tbody></tbody></table></div>`;
  const rows = st.rows.map(r => ({ ...r, signal: r.s?.stage >= 2 ? r.s.signal : '', score: r.s?.stage >= 2 ? r.s.overall : null, er: r.s?.expected_return ?? null, gainp: r.px ? (r.px / r.avg - 1) * 100 : null, action: actionFor(r.s, r.w) }))
    .sort((a, b) => { const x = a[pfSort.key], y = b[pfSort.key]; if (x == null || x === '') return 1; if (y == null || y === '') return -1; return (typeof x === 'string' ? x.localeCompare(y) : x - y) * pfSort.dir; });
  $('#pt tbody').innerHTML = rows.length ? rows.map(r => `<tr class="click" data-s="${esc(r.symbol)}">
    <td><b>${esc(r.symbol)}</b>${r.raw && r.raw !== r.symbol ? `<div class="muted" style="font-size:12px">${esc(r.raw)}</div>` : ''}</td>
    <td>${r.signal ? pill(sigLabel(r.symbol, r.signal)) : '<span class="muted">not analysed</span>'}</td><td>${bar(r.score)}</td>
    <td class="num hide-sm">${n(r.qty, f0)}</td><td class="num hide-sm">${n(r.avg)}</td><td class="num hide-sm">${r.priced ? n(r.px) : '—'}</td>
    <td class="num">${inr(r.val)}</td><td class="num">${(r.w * 100).toFixed(1)}%</td><td class="num hide-sm">${pc(r.er)}</td>
    <td class="num ${r.gainp >= 0 ? 'pos' : 'neg'}">${pc(r.gainp)}</td>
    <td>${pill(r.action)}${r.s?.horizon ? `<div class="hint">${esc(hzText(r.s.horizon))}</div>` : ''}</td>
    <td><button class="btn ghost small" data-del="${esc(r.symbol)}" aria-label="Remove ${esc(r.symbol)}">✕</button></td></tr>`).join('')
    : '<tr><td colspan="12" class="empty">No holdings yet. Add one above or upload your broker file.</td></tr>';

  wireSync(v);
  $('#csave').addEventListener('click', () => cloudSave());
  $('#cload').addEventListener('click', async () => { if (!cloudReady()) { toast('Set GitHub and a passphrase in Settings first.'); return; } if (await cloudLoad(true)) { toast('Loaded portfolios from cloud.'); viewPortfolios(); } });
  renderReports();
  $('#pt thead').addEventListener('click', e => { const th = e.target.closest('th[data-k]'); if (!th?.dataset.k) return; const k = th.dataset.k; pfSort.dir = pfSort.key === k ? -pfSort.dir : (['symbol', 'signal'].includes(k) ? 1 : -1); pfSort.key = k; viewPortfolios(); });
  $('#pt tbody').addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (del) { e.stopPropagation(); p.holdings = p.holdings.filter(h => h.symbol !== del.dataset.del); savePf(); viewPortfolios(); return; }
    const tr = e.target.closest('tr[data-s]'); if (tr) location.hash = 'stock/' + encodeURIComponent(tr.dataset.s);
  });
  $('#pftabs').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; activePf = b.dataset.id; LS.set('activePf', activePf); viewPortfolios(); });
  $('#pfnew').addEventListener('click', () => { const name = prompt('Portfolio name', 'Portfolio ' + (portfolios.length + 1)); if (!name) return; const id = 'p' + Date.now(); portfolios.push({ id, name, holdings: [] }); activePf = id; savePf(); LS.set('activePf', id); viewPortfolios(); });
  $('#pfren').addEventListener('click', () => { const name = prompt('New name', p.name); if (!name) return; p.name = name; savePf(); viewPortfolios(); });
  $('#pfdel').addEventListener('click', () => { if (!confirm(`Delete "${p.name}"? This cannot be undone.`)) return; portfolios = portfolios.filter(x => x !== p); savePf(); viewPortfolios(); });
  $('#pfexp').addEventListener('click', () => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify({ portfolios, aliases }, null, 1)], { type: 'application/json' })); a.download = 'stockpilot-portfolios.json'; a.click(); });
  $('#pfimp').addEventListener('change', async e => {
    const file = e.target.files[0]; e.target.value = ''; if (!file) return;
    const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
    const isZipOrXls = (head[0] === 0x50 && head[1] === 0x4b) || (head[0] === 0xd0 && head[1] === 0xcf);  // .xlsx / .xls
    if (!isZipOrXls && (/\.json$/i.test(file.name) || (await file.slice(0, 200).text()).trim().startsWith('{'))) {
      try { const j = JSON.parse(await file.text()); if (!Array.isArray(j.portfolios)) throw new Error('Not a StockPilot backup'); portfolios = j.portfolios; aliases = j.aliases || aliases; savePf(); LS.set('aliases', aliases); viewPortfolios(); }
      catch (err) { alert('Import failed: ' + err.message); }
      return;
    }
    // broker spreadsheet -> a new portfolio named after the file (rename it afterwards if you like)
    const id = 'p' + Date.now(), np = { id, name: file.name.replace(/\.[^.]+$/, '').slice(0, 40) || 'Imported', holdings: [] };
    portfolios.push(np); activePf = id; LS.set('activePf', id);
    if (!(await brokerImport(file, np))) { portfolios = portfolios.filter(x => x !== np); activePf = p.id; LS.set('activePf', p.id); savePf(); viewPortfolios(); }
  });
  $('#hadd').addEventListener('click', () => {
    const sym = $('#hsym').value.trim().toUpperCase(), qty = +$('#hqty').value, avg = +$('#havg').value;
    if (!sym || !(qty > 0) || !(avg > 0)) { alert('Enter symbol, quantity and average price.'); return; }
    const ex = p.holdings.find(h => h.symbol === sym);
    if (ex) Object.assign(ex, { qty, avg }); else p.holdings.push({ symbol: sym, qty, avg });
    savePf(); viewPortfolios();
  });
  $('#upl').addEventListener('change', async e => { const file = e.target.files[0]; e.target.value = ''; if (file) await brokerImport(file, p); });
}
async function brokerImport(file, p) {
  try {
    const parsed = parseRows(await readFile(file)), matched = [], unmatched = [];
    if (!parsed.length) throw new Error('No holdings rows found (each row needs a stock, a quantity and an average price).');
    parsed.forEach(r => { const s = matchSymbol(r.raw); (s ? matched : unmatched).push({ ...r, symbol: s }); });
    const merge = {};
    matched.forEach(r => { const m = merge[r.symbol]; if (m) { m.avg = (m.avg * m.qty + r.avg * r.qty) / (m.qty + r.qty); m.qty += r.qty; } else merge[r.symbol] = { symbol: r.symbol, raw: r.raw, qty: r.qty, avg: r.avg }; });
    p.holdings = Object.values(merge); savePf(); viewPortfolios();
    if (unmatched.length) showUnmatched(p, unmatched);
    toast(`Loaded ${matched.length} holdings into "${p.name}"${unmatched.length ? `; ${unmatched.length} need a symbol` : ''}. Press Save to cloud when done.`);
    return true;
  } catch (err) { alert('Upload failed: ' + err.message); return false; }
}
function showUnmatched(p, list) {
  const box = $('#unmatched');
  box.innerHTML = `<div class="banner"><b>${list.length} rows need a symbol.</b> Type the NSE symbol for each (remembered for next upload). Leave blank to skip.
    <div style="display:flex;flex-direction:column;gap:6px;margin-top:10px">${list.map((r, i) => `<div class="row" style="align-items:center"><span style="flex:1 1 200px">${esc(r.raw)} · ${n(r.qty, f0)} @ ₹${n(r.avg)}</span><input data-i="${i}" list="symlist" placeholder="NSE symbol" style="flex:0 1 160px" aria-label="Symbol for ${esc(r.raw)}"></div>`).join('')}</div>
    <button class="btn" id="mapok" style="margin-top:10px">Save mappings</button></div>`;
  $('#mapok').addEventListener('click', () => {
    box.querySelectorAll('input[data-i]').forEach(inp => {
      const sym = inp.value.trim().toUpperCase(), r = list[+inp.dataset.i]; if (!sym) return;
      aliases[r.raw.toUpperCase()] = sym;
      const ex = p.holdings.find(h => h.symbol === sym);
      if (ex) { ex.avg = (ex.avg * ex.qty + r.avg * r.qty) / (ex.qty + r.qty); ex.qty += r.qty; } else p.holdings.push({ symbol: sym, raw: r.raw, qty: r.qty, avg: r.avg });
    });
    LS.set('aliases', aliases); savePf(); viewPortfolios();
  });
}

async function renderReports() {
  const box = $('#replist'); if (!box || !settings.pass) return;
  const reps = REPORTS || await loadReports();
  if (reps === 'badpass') { box.textContent = 'The passphrase in Settings does not match the one used by the scheduled runs (GitHub secret PORTFOLIO_KEY).'; return; }
  if (!reps?.length) { box.textContent = 'No reports yet. They appear after the first scheduled run with your cloud-saved portfolios.'; return; }
  let sel = 0;
  const draw = () => {
    const r = reps[sel];
    box.className = '';
    box.innerHTML = `<div class="tabs" style="margin-bottom:12px">${reps.slice(0, 9).map((x, i) => `<button data-i="${i}" class="${i === sel ? 'on' : ''}">${esc(x.slot)} · ${esc(x.time.slice(5, 16))}</button>`).join('')}</div>
      ${r.market?.mood != null ? `<p class="hint">Global mood ${r.market.mood}/100 (${esc(r.market.bias)}) · ${esc(r.market.movers.map(m => m.label + ' ' + pc(m.chg_1d)).join(' · '))}</p>` : ''}
      ${(r.ipos || []).length ? `<p class="hint">IPOs: ${r.ipos.map(x => `${esc(x.name)} ${pill(x.call)}`).join(' ')}</p>` : ''}
      ${r.portfolios.map(p => `<div class="rep"><h3>${esc(p.name)}</h3>
        <p class="hint">${inr(p.value)} · ${pc(p.gain_pct)} on cost · health ${p.health ?? '—'}/100 · expected ${pc(p.expected_return)}/yr${p.gap < 0 ? ` (${Math.abs(p.gap).toFixed(1)} pts short of ${target()}%)` : ''}</p>
        ${p.changes.length ? `<p class="hint"><b>Changed:</b> ${p.changes.map(c => `${esc(c.symbol)} ${esc(c.from)} → ${esc(c.to)}`).join(', ')}</p>` : ''}
        ${['EXIT', 'BUY MORE', 'SHORT-TERM TRADE', 'HOLD / REVIEW', 'HOLD', 'HOLD (already large)', 'NOT ANALYSED'].map(a => { const hs = p.holdings.filter(h => h.action === a); return hs.length ? `<div class="repgrp"><div>${pill(a)} <span class="hint">${hs.length}</span></div><ul>${hs.map(h => `<li><a href="#stock/${encodeURIComponent(h.symbol)}">${esc(h.symbol)}</a> <span class="muted">${h.weight.toFixed(1)}% · score ${h.score ?? '—'}${h.horizon ? ' · ' + esc(h.horizon + ' (' + h.period + ')') : ''}</span>${(a === 'BUY MORE' ? h.for : h.against).length ? ' — ' + esc((a === 'BUY MORE' ? h.for : h.against).slice(0, 2).join('; ')) : ''}</li>`).join('')}</ul></div>` : ''; }).join('')}
        ${p.alerts.length ? `<ul class="acts">${p.alerts.map(x => `<li class="warn"><span class="dot"></span><div>${esc(x)}</div></li>`).join('')}</ul>` : ''}</div>`).join('')}`;
    box.querySelectorAll('[data-i]').forEach(b => b.addEventListener('click', () => { sel = +b.dataset.i; draw(); }));
  };
  draw();
}

// ================================================================ view: global market + exchange events
function viewMarket() {
  const v = $('#view');
  const groups = {}; (MARKET.markets || []).forEach(m => (groups[m.group] = groups[m.group] || []).push(m));
  const tilts = Object.entries(MARKET.tilts || {}).sort((a, b) => b[1].score - a[1].score);
  const evIcon = t => ({ volume: 'Volume spike', news: 'Major news', red_flag: 'Red flag' }[t] || t);
  v.innerHTML = `
    <div><h1>Global markets</h1><p class="sub">How world markets are set up for India's next session. Updated ${esc(MARKET.updated || '—')}.</p></div>
    <div class="card verdict"><div class="big" style="color:${scoreColor(MARKET.mood)}">${MARKET.mood ?? '—'}</div>
      <div><div class="sig">${esc(MARKET.bias || 'No data')}</div><div class="hint">Global mood, 0–100. Overnight US and Asian markets, fear indices, crude, dollar, yields and the rupee.</div></div></div>
    <div class="grid2">
      <div class="card"><h2>Markets</h2><div class="tbox" style="margin-top:12px;border:0"><table><thead><tr><th>Market</th><th class="num">Last</th><th class="num">1 day</th><th class="num">5 days</th></tr></thead><tbody>
        ${Object.entries(groups).map(([g, ms]) => `<tr><td colspan="4" class="grp">${esc(g)}</td></tr>` + ms.map(m => `<tr><td>${esc(m.label)}</td><td class="num">${n(m.last)}</td><td class="num ${m.chg_1d >= 0 ? 'pos' : 'neg'}">${pc(m.chg_1d, 2)}</td><td class="num ${m.chg_5d >= 0 ? 'pos' : 'neg'}">${pc(m.chg_5d, 1)}</td></tr>`).join('')).join('')}
      </tbody></table></div><p class="hint">Rising VIX, yields, dollar, crude or USD/INR are read as negative for Indian stocks.</p></div>
      <div class="card"><h2>Effect on Indian sectors</h2><p class="sub">50 = no effect. This feeds the "Global markets" rating of every stock in the sector.</p>
        <div class="params" style="margin-top:12px">${tilts.map(([sec, t]) => `<div class="prow"><div>${esc(sec)}${t.why.length ? `<div class="hint">${esc(t.why.join('; '))}</div>` : ''}</div><div></div>${bar(t.score)}</div>`).join('') || '<p class="muted">No data yet.</p>'}</div></div>
    </div>
    <div class="card"><div class="cardhead"><h2>Exchange news &amp; volume spikes</h2><span class="hint">BSE announcements (and NSE when it allows access), plus BSE stocks trading 3× their usual volume</span></div>
      ${EVENTS.length ? `<div class="tbox"><table><thead><tr><th>Stock</th><th>What happened</th><th>Signal</th><th class="hide-sm">Date</th></tr></thead><tbody>
        ${EVENTS.map(e => `<tr class="click" data-s="${esc(e.symbol)}"><td><b>${esc(e.symbol)}</b><div class="muted" style="font-size:12px">${esc((e.name || '').slice(0, 28))} · ${esc(e.exchange || '')}</div></td>
          <td style="white-space:normal;min-width:220px"><span class="pill ${e.type === 'red_flag' ? 'SELL' : e.type === 'volume' ? 'HOLD' : 'BUY'}">${evIcon(e.type)}</span> ${e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener">${esc(e.detail)}</a>` : esc(e.detail)}</td>
          <td>${e.signal ? pill(sigLabel(e.symbol, e.signal)) + ' <span class="muted">' + (e.score != null ? Math.round(e.score) : '') + '</span>' : '—'}</td><td class="hide-sm muted">${esc(e.date || '')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No major announcements or volume spikes in the latest run.</p>'}
    </div>`;
  v.querySelectorAll('tr[data-s]').forEach(tr => tr.addEventListener('click', e => { if (!e.target.closest('a')) location.hash = 'stock/' + encodeURIComponent(tr.dataset.s); }));
}

// ================================================================ view: IPOs
function viewIpos() {
  const v = $('#view');
  v.innerHTML = `
    <div><h1>IPOs</h1><p class="sub">Open and upcoming IPOs, rated on their sector, price vs listed peers, and financials. Updated ${esc(IPO_UPDATED || '—')}.</p></div>
    ${IPOS.length ? `<div class="ipos">${IPOS.map((x, i) => `
      <div class="card ipo">
        <div class="cardhead"><div><h2>${esc(x.name)}</h2><div class="hint">${esc(x.board || '')} · ${esc(x.sector || 'sector unknown')} · ${esc(x.status || '')}${x.open ? ' · ' + esc(x.open) + ' → ' + esc(x.close || '') : ''}${x.price_band ? ' · ₹' + esc(String(x.price_band).replace(/^₹/, '')) : ''}${x.issue_size_cr ? ' · ₹' + n(x.issue_size_cr, f0) + ' Cr' : ''}</div></div>
          <div style="text-align:right">${pill(x.call)}<div class="big" style="color:${scoreColor(x.score)};font-size:30px">${x.score ?? '—'}</div></div></div>
        <p><b>${esc(x.verdict)}</b></p>
        ${x.about ? `<p class="hint">${esc(x.about)}</p>` : ''}
        ${pointsHtml({ for: x.for, against: x.against })}
        <div class="params" style="margin-top:10px">${x.params.map(p => `<div class="prow"><div>${esc(p.label)} <span class="muted" style="font-size:12px">×${p.weight}</span></div><div class="val">${esc(p.value ?? 'no data')}</div>${bar(p.score)}</div>`).join('')}</div>
        ${x.peers ? `<p class="hint">Listed ${esc(x.sector)} peers: ${x.peers.count} analysed, best ${x.peers.best.map(b => `<a href="#stock/${encodeURIComponent(b)}">${esc(b)}</a>`).join(', ')}.</p>` : ''}
        ${(x.headlines || []).length ? `<ul class="list">${x.headlines.slice(0, 3).map(h => `<li><span class="d">${esc(h.date)}</span><a href="${esc(h.link)}" target="_blank" rel="noopener">${esc(h.title)}</a></li>`).join('')}</ul>` : ''}
        ${x.rhp_link ? `<p class="hint"><a href="${esc(x.rhp_link)}" target="_blank" rel="noopener">${/chittorgarh/i.test(x.rhp_link) ? 'Issue details &amp; prospectus figures' : 'Prospectus (RHP)'}</a>${x.fin_years && x.fin_years.length ? ' · financials ' + esc(x.fin_years[0]) + ' – ' + esc(x.fin_years[x.fin_years.length - 1]) : ''}</p>` : ''}
        ${claudeBox('ipo' + i)}
      </div>`).join('')}</div>` : '<div class="card empty">No IPOs found in the latest run.</div>'}
    <div class="card"><h2>Add an IPO or fill in its financials</h2><p class="sub">Every open and upcoming IPO is read automatically from its own page: issue size, offer-for-sale share, 3 years of revenue and profit, net worth, debt, post-issue EPS, promoter holding and broker reviews. If an IPO still says NEED DATA, its prospectus figures aren't published yet; you can add them yourself as a row in <code>config/ipos.csv</code> on GitHub, and those figures win.</p></div>`;
  wireClaude(v, async id => ipoPrompt(IPOS[+id.slice(3)]));
}

// ================================================================ settings
function viewSettings() {
  const v = $('#view');
  v.innerHTML = `
    <div><h1>Settings</h1><p class="sub">Everything here is stored only in this browser.</p></div>
    <div class="card"><h2>GitHub (for Fetch, Analyse now and cloud save)</h2>
      <p class="sub">Create a fine-grained token at github.com → Settings → Developer settings → Fine-grained tokens. Give it access to <b>only this repository</b>, with <b>Actions: Read and write</b> and <b>Contents: Read and write</b>. Without a token the site still updates on schedule; you just can't fetch on demand or save portfolios to the cloud.</p>
      <div class="row" style="margin-top:14px">
        <label>Owner (your GitHub username)<input id="gho" value="${esc(settings.ghOwner)}"></label>
        <label>Repository<input id="ghr" value="${esc(settings.ghRepo)}"></label>
        <label>Token<input id="ght" type="password" value="${esc(settings.ghToken)}" placeholder="github_pat_…" autocomplete="off"></label>
      </div>
      <div class="tools" style="margin-top:12px"><button class="btn" id="ghsave">Save</button><button class="btn ghost" id="ghtest">Test connection</button><button class="btn ghost" id="fullscan">Run full market scan</button><span class="hint" id="ghmsg"></span></div>
    </div>
    <div class="card"><div class="cardhead"><h2>Sign-in</h2><button class="btn ghost small" id="signout">Sign out</button></div><p class="sub">User ID is set by the GitHub secret <code>LOGIN_USER</code>; the password is your portfolio passphrase (<code>PORTFOLIO_KEY</code>). Signing out removes them from this device.</p></div>
    <div class="card"><h2>Portfolio passphrase</h2>
      <p class="sub">Encrypts your portfolios before they're saved to GitHub, and decrypts your reports. Put the <b>same passphrase</b> in GitHub: repo → Settings → Secrets and variables → Actions → New secret named <code>PORTFOLIO_KEY</code>. If you forget it, the cloud copy can't be recovered (your browser copy is unaffected).</p>
      <div class="row" style="margin-top:14px"><label>Passphrase<input id="pass" type="password" value="${esc(settings.pass)}" autocomplete="new-password" placeholder="at least 12 characters"></label></div>
      <div class="tools" style="margin-top:12px"><button class="btn" id="psave">Save</button><span class="hint" id="pmsg"></span></div>
    </div>
    <div class="card"><h2>Report delivery (pre-market, 1 PM, 3 PM)</h2>
      <p class="sub">Reports always appear on the Portfolios page. To also get them on your phone or email, add these GitHub secrets (repo → Settings → Secrets and variables → Actions):</p>
      <ul class="list" style="margin-top:10px"><li><span><b>Telegram:</b> <code>TELEGRAM_TOKEN</code> (from @BotFather) and <code>TELEGRAM_CHAT_ID</code> (message your bot, then open api.telegram.org/bot&lt;token&gt;/getUpdates to see it).</span></li>
      <li><span><b>Email (Gmail):</b> <code>SMTP_USER</code> (your Gmail), <code>SMTP_PASS</code> (a Gmail app password), <code>REPORT_EMAIL</code> (where to send).</span></li></ul>
    </div>
    <div class="card"><h2>Claude (optional)</h2>
      <p class="sub">Uses your own Anthropic API key (console.anthropic.com), billed by Anthropic separately from a Claude subscription. Sent only to api.anthropic.com, with one compact stock summary per click.</p>
      <div class="row" style="margin-top:14px">
        <label>API key<input id="key" type="password" value="${esc(settings.key)}" placeholder="sk-ant-…" autocomplete="off"></label>
        <label>Model<input id="model" value="${esc(settings.model)}"></label>
        <label style="flex:0 1 140px">Max reply tokens<input id="maxt" type="number" min="100" max="1000" value="${esc(settings.maxTokens)}"></label>
      </div>
      <div class="tools" style="margin-top:12px"><button class="btn" id="save">Save</button><button class="btn danger" id="clear">Remove key</button><span class="hint" id="saved"></span></div>
    </div>
    <div class="card"><h2>How scoring works</h2>
      <p class="sub">13 parameters rated 0–100, combined by weight. BUY needs a score of at least ${META.thresholds?.buy ?? 66} and a strong 20% hurdle rating. SELL is below ${META.thresholds?.sell ?? 45}, or a weak hurdle with a falling trend. Confidence reflects how much data was found and whether fundamentals and technicals agree. It's a rules-based screen, not a probability. Edit <code>config/settings.json</code> to change weights.</p>
      <div class="tbox" style="margin-top:12px"><table><thead><tr><th>Parameter</th><th class="num">Weight</th></tr></thead><tbody>
        ${Object.entries(META.weights || {}).map(([k, w]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td class="num">${w}</td></tr>`).join('')}</tbody></table></div>
    </div>`;
  const readGh = () => { settings.ghOwner = $('#gho').value.trim(); settings.ghRepo = $('#ghr').value.trim(); settings.ghToken = $('#ght').value.trim(); saveSettings(); };
  $('#ghsave').addEventListener('click', () => { readGh(); $('#ghmsg').textContent = 'Saved'; });
  $('#ghtest').addEventListener('click', async () => { readGh(); try { const r = await gh(''); $('#ghmsg').textContent = 'Connected to ' + r.full_name; } catch (e) { $('#ghmsg').textContent = e.message; } });
  $('#fullscan').addEventListener('click', () => { readGh(); dispatch('full'); });
  $('#signout').addEventListener('click', signOut);
  $('#psave').addEventListener('click', async () => { const pw = $('#pass').value; if (pw && pw.length < 12) { $('#pmsg').textContent = 'Use at least 12 characters.'; return; } settings.pass = pw; saveSettings(); REPORTS = null; $('#pmsg').textContent = 'Saved'; if (cloudReady() && await cloudLoad()) toast('Loaded newer portfolios from cloud.'); });
  $('#save').addEventListener('click', () => { settings.key = $('#key').value.trim(); settings.model = $('#model').value.trim() || 'claude-haiku-5-5'; settings.maxTokens = +$('#maxt').value || 350; saveSettings(); $('#saved').textContent = 'Saved'; });
  $('#clear').addEventListener('click', () => { settings.key = ''; saveSettings(); viewSettings(); });
}

// ================================================================ boot
// ---------------- login: username + passphrase, checked by decrypting data/lock.enc.json
async function unlock() {
  const auth = LS.get('auth', null);
  let lock = null;
  try { const r = await fetch('data/lock.enc.json?t=' + Date.now(), { cache: 'no-cache' }); if (r.ok) lock = await r.json(); } catch { }
  const check = async (user, pass) => {
    if (!lock) return { ok: true, firstRun: true };
    try { const d = await decryptObj(lock, pass); return { ok: d.ok === true && (!d.user || d.user.toLowerCase() === user.toLowerCase()) }; } catch { return { ok: false }; }
  };
  if (auth && (await check(auth.user, auth.pass)).ok && Date.now() - (auth.at || 0) < 30 * 864e5) { if (!settings.pass) { settings.pass = auth.pass; saveSettings(); } return; }
  document.body.classList.add('locked');
  const box = document.createElement('div'); box.className = 'login';
  box.innerHTML = `<form class="card" id="loginf"><div class="brand">Stock<span>Pilot</span></div><h2>Sign in</h2>
    <label>User ID<input id="lu" autocomplete="username" required></label>
    <label>Password<input id="lp" type="password" autocomplete="current-password" required></label>
    <label class="inl hint"><input type="checkbox" id="lr" checked> Keep me signed in on this device for 30 days</label>
    <button class="btn">Sign in</button><p class="hint" id="lmsg"></p>
    <p class="hint">${lock ? 'Password is your portfolio passphrase.' : 'First run: the login activates after the next scheduled run creates it.'}</p></form>`;
  document.body.appendChild(box);
  await new Promise(done => $('#loginf').addEventListener('submit', async e => {
    e.preventDefault(); const user = $('#lu').value.trim(), pass = $('#lp').value;
    $('#lmsg').textContent = 'Checking…';
    const r = await check(user, pass);
    if (!r.ok) { $('#lmsg').textContent = 'Wrong user ID or password.'; return; }
    if ($('#lr').checked) LS.set('auth', { user, pass, at: Date.now() }); else LS.set('auth', null);
    settings.pass = pass; saveSettings(); box.remove(); document.body.classList.remove('locked'); done();
  }));
}
function signOut() { LS.set('auth', null); settings.pass = ''; saveSettings(); location.reload(); }

(async function boot() {
  await unlock();
  setupSearch();
  const menu = $('#fetchmenu');
  $('#fetch').addEventListener('click', e => { e.stopPropagation(); menu.hidden = !menu.hidden; });
  menu.addEventListener('click', async e => { const b = e.target.closest('[data-mode]'); if (!b) return; menu.hidden = true; await cloudSaveIfDirty(); dispatch(b.dataset.mode); });
  document.addEventListener('click', e => { if (!e.target.closest('.fetchbox')) menu.hidden = true; });
  if (cloudReady()) await cloudLoad();
  await loadData();
  go();
  pollRun();
})();
