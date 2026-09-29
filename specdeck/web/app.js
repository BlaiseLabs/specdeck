/* specdeck viewer — vanilla JS, no build step. */
'use strict';
(() => {
  const TOKEN = document.querySelector('meta[name="specdeck-token"]')?.content || '';
  const ORDER = ['passed', 'failed', 'error', 'blocked', 'skipped', 'notrun'];
  const LABEL = { passed: 'Passing', failed: 'Failing', error: 'Error', blocked: 'Blocked', skipped: 'Skipped', notrun: 'Not run' };
  const GROUP_COLORS = ['#4c6ef5', '#12b886', '#f59f00', '#e64980', '#7950f2', '#15aabf', '#fd7e14', '#82c91e', '#be4bdb', '#228be6', '#fa5252', '#40c057'];
  const BAD = new Set(['failed', 'error']);
  const COLOR = { passed: 'var(--pass)', failed: 'var(--fail)', error: 'var(--error)', blocked: 'var(--blocked)', skipped: 'var(--skipped)', notrun: 'var(--notrun)' };

  const S = {
    slug: null, board: null, byId: new Map(), runs: null,
    tab: 'overview', renderedTab: null, sel: null, viewRun: null,
    filter: { text: '', status: null, ids: null, idsLabel: '' },
    collapsed: new Set(), es: null, online: true,
    hist: {}, histFor: {}, source: {}, showSource: false,
    edit: null, edited: {},
    graph: { key: null, layout: null, t: { x: 0, y: 0, k: 1 }, fitted: false, group: null, search: '' },
    launchSeen: null, projects: null,
  };

  // ------------------------------------------------------------------ utils
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const enc = encodeURIComponent;
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // escape, then render `code` spans
  const fmt = (v) => esc(v).replace(/`([^`\n]+)`/g, '<code class="inl">$1</code>');
  // docstring body: single newlines are wrapping, blank lines are paragraphs
  const paras = (v) => String(v || '').trim().split(/\n\s*\n/).map((p) => `<p>${fmt(p.replace(/\s*\n\s*/g, ' '))}</p>`).join('');
  function splitParam(s) {
    const m = s.parametrized && /^(.*)\s\[(.+)\]$/.exec(s.name || '');
    return m ? { base: m[1], param: m[2] } : { base: s.name, param: null };
  }
  const titleHtml = (s) => { const { base, param } = splitParam(s); return `${param ? `<span class="param">${esc(param)}</span>` : ''}${fmt(base)}`; };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

  function relTime(ts) {
    if (!ts) return '';
    const d = Date.now() / 1000 - ts;
    if (d < 10) return 'just now';
    if (d < 60) return `${Math.round(d)}s ago`;
    if (d < 3600) return `${Math.round(d / 60)} min ago`;
    if (d < 86400) return `${Math.round(d / 3600)} h ago`;
    if (d < 86400 * 7) return `${Math.round(d / 86400)} d ago`;
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  const absTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : '');
  function fmtDur(s) {
    if (s == null) return '';
    if (s < 0.001) return '<1 ms';
    if (s < 1) return `${Math.round(s * 1000)} ms`;
    if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)} s`;
    const m = Math.floor(s / 60);
    return `${m}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
  }
  function icon(st, live) {
    if (live === 'running') return '<span class="ic ic-running" title="Running"></span>';
    return `<span class="ic ic-${st}${live === 'queued' ? ' is-queued' : ''}" title="${esc(LABEL[st] || st)}"></span>`;
  }
  function pill(st, live) {
    if (live === 'running') return `<span class="pill running">${icon(st, live)}Running</span>`;
    const q = live === 'queued' ? ' · queued' : '';
    return `<span class="pill ${st}">${icon(st)}${esc(LABEL[st] || st)}${q}</span>`;
  }
  function segbar(counts, cls = '') {
    const segs = ORDER.filter((k) => counts[k]).map((k) => `<span class="seg-${k}" style="flex-grow:${counts[k]}"></span>`).join('');
    return `<div class="segbar ${cls}">${segs}</div>`;
  }
  const failingCount = (c) => (c.failed || 0) + (c.error || 0);
  function groupColor(gid) {
    const groups = S.board ? S.board.groups.map((g) => g.id) : [];
    const i = Math.max(0, groups.indexOf(gid));
    return GROUP_COLORS[i % GROUP_COLORS.length];
  }
  const nameOf = (id) => S.byId.get(id)?.name || id;
  function shellQuote(s) { return /^[\w./:=-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`; }

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Specdeck-Token': TOKEN },
      body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.error || `${r.status} ${r.statusText}`), { status: r.status });
    return data;
  }

  function toast(html, { bad = false, ms = 4500 } = {}) {
    const box = $('#toasts');
    if (!box) return;
    const el = document.createElement('div');
    el.className = `toast${bad ? ' bad' : ''}`;
    el.innerHTML = html;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 320); }, ms);
  }

  async function copy(text, what = 'Copied') {
    try { await navigator.clipboard.writeText(text); toast(esc(what)); }
    catch { toast('Copy failed — select the text instead', { bad: true }); }
  }

  // tooltips for anything with data-tip
  function initTips() {
    const tip = $('#tip');
    let cur = null;
    document.addEventListener('mouseover', (e) => {
      const t = e.target.closest('[data-tip]');
      if (t === cur) return;
      cur = t;
      if (!t) { tip.classList.remove('show'); return; }
      tip.textContent = t.getAttribute('data-tip');
      tip.classList.add('show');
    });
    document.addEventListener('mousemove', (e) => {
      if (!cur) return;
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let x = e.clientX + 14, y = e.clientY + 16;
      if (x + w > innerWidth - 8) x = e.clientX - w - 14;
      if (y + h > innerHeight - 8) y = e.clientY - h - 12;
      tip.style.left = `${x}px`; tip.style.top = `${y}px`;
    });
    document.addEventListener('mouseleave', () => { cur = null; tip.classList.remove('show'); });
  }

  // ------------------------------------------------------------------ routing
  function parseRoute() {
    const m = location.pathname.match(/^\/p\/([^/]+)/);
    S.slug = m ? decodeURIComponent(m[1]) : null;
    const hp = new URLSearchParams(location.hash.slice(1));
    S.tab = ['overview', 'graph', 'history'].includes(hp.get('tab')) ? hp.get('tab') : 'overview';
    S.sel = hp.get('s');
    S.viewRun = hp.get('run') ? Number(hp.get('run')) : null;
  }
  function setHash() {
    const hp = new URLSearchParams();
    if (S.tab !== 'overview') hp.set('tab', S.tab);
    if (S.viewRun) hp.set('run', S.viewRun);
    if (S.sel) hp.set('s', S.sel);
    const h = hp.toString();
    history.replaceState(null, '', h ? `#${h}` : location.pathname);
  }

  // ------------------------------------------------------------------ boot
  function boot() {
    parseRoute();
    if (!S.slug) return projectsPage();
    try { S.collapsed = new Set(JSON.parse(localStorage.getItem(`specdeck:collapsed:${S.slug}`) || '[]')); } catch { /* ignore */ }
    $('#app').innerHTML = `
      <header class="top" id="top"></header>
      <div id="banner"></div>
      <main id="main">
        <section class="card summary" id="summary"></section>
        <nav class="tabs" id="tabs"></nav>
        <div id="tabbody"></div>
      </main>
      <aside class="drawer" id="drawer" aria-label="Scenario details"></aside>
      <div class="toasts" id="toasts"></div>
      <div class="tip" id="tip"></div>`;
    initTips();
    bindGlobal();
    refresh().then(connect);
    window.addEventListener('hashchange', () => {
      const prevRun = S.viewRun;
      parseRoute();
      if (prevRun !== S.viewRun) { S.graph.key = null; refresh(); } else render();
    });
  }

  function connect() {
    if (S.es) S.es.close();
    const es = new EventSource(`/api/p/${enc(S.slug)}/stream`);
    es.addEventListener('change', () => scheduleRefresh(60));
    es.onopen = () => { if (!S.online) { S.online = true; scheduleRefresh(0); } };
    es.onerror = () => { S.online = false; renderTop(); };
    S.es = es;
  }

  let refreshTimer = null, refreshing = false, refreshAgain = false;
  function scheduleRefresh(ms = 100) { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, ms); }
  async function refresh() {
    if (refreshing) { refreshAgain = true; return; }
    refreshing = true;
    try {
      const q = S.viewRun ? `?run=${S.viewRun}` : '';
      const b = await api(`/api/p/${enc(S.slug)}/board${q}`);
      const prev = S.board;
      S.board = b;
      S.byId = new Map(b.scenarios.map((s) => [s.id, s]));
      if (S.tab === 'history' || S.runs === null || (prev && runKey(prev) !== runKey(b))) {
        S.runs = (await api(`/api/p/${enc(S.slug)}/runs`)).runs;
      }
      S.online = true;
      announce(prev, b);
      render(prev);
    } catch (e) {
      if (e.status === 404) {
        $('#main').innerHTML = `<div class="card empty"><b>${esc(e.message)}</b>Run <code>pytest --specdeck-live</code> in the project first.</div>`;
      } else {
        S.online = false; renderTop();
      }
    } finally {
      refreshing = false;
      if (refreshAgain) { refreshAgain = false; scheduleRefresh(40); }
    }
  }
  const runKey = (b) => `${b.active_run?.id || ''}:${b.active_run?.done || ''}:${b.last_run?.id || ''}`;

  function announce(prev, b) {
    if (!prev) return;
    if (prev.active_run && !b.active_run && b.last_run && b.last_run.id === prev.active_run.id) {
      const r = b.last_run;
      const d = b.delta || {};
      const bits = [`<b>${r.passed}</b> passing`];
      if (r.failed + r.error) bits.push(`<b>${r.failed + r.error}</b> failing`);
      if (r.blocked) bits.push(`${r.blocked} blocked`);
      if (d.fixed?.length) bits.push(`<b>+${d.fixed.length}</b> newly passing`);
      if (d.regressed?.length) bits.push(`<b>${d.regressed.length}</b> regressed`);
      const st = r.status === 'passed' ? 'passed' : (r.status === 'failed' ? 'failed' : 'skipped');
      toast(`${icon(st)}<div>Run #${r.n || r.id} ${r.status === 'interrupted' ? 'interrupted' : 'finished'} · ${bits.join(' · ')}</div>`, { ms: 6000 });
    }
    const L = b.launch;
    if (L && !L.running && L.returncode != null && ![0, 1, 2].includes(L.returncode) && S.launchSeen !== L.started) {
      S.launchSeen = L.started;
      toast(`<div><b>The run started from the viewer failed (exit ${L.returncode}).</b>${L.log_tail ? `<pre>${esc(L.log_tail.slice(-900))}</pre>` : ''}</div>`, { bad: true, ms: 15000 });
    }
  }

  // ------------------------------------------------------------------ render
  function render(prev) {
    if (!S.board) return;
    const p = S.board.project;
    const ar = S.board.active_run;
    document.title = `${ar ? `● ${ar.done}/${ar.total} · ` : ''}${p.name} · specdeck`;
    renderTop();
    renderBanner();
    renderSummary();
    renderTabs();
    if (S.renderedTab !== S.tab) renderTabBody(); else updateTabBody();
    if (!S.edit) renderDrawer();
    if (prev) flashChanged(prev);
  }

  function flashChanged(prev) {
    const before = new Map(prev.scenarios.map((s) => [s.id, `${s.status}:${s.run_id}`]));
    S.board.scenarios.forEach((s) => {
      const b = before.get(s.id);
      if (b && b !== `${s.status}:${s.run_id}` && !s.live) {
        $$(`.row[data-sid="${CSS.escape(s.id)}"]`).forEach((el) => { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); });
      }
    });
  }

  function renderTop() {
    const top = $('#top');
    if (!top || !S.board) return;
    const b = S.board, ar = b.active_run, lr = b.last_run, L = b.launch;
    let state;
    if (!S.online) {
      state = '<span class="idle-dot"></span><span class="muted">Viewer connection lost — retrying…</span>';
    } else if (ar) {
      state = `<span class="live-dot"></span><b>Running</b><span class="muted num">${ar.done} / ${ar.total}${ar.trigger === 'ui' ? '' : ' · from terminal'}</span>`;
    } else if (L && L.running) {
      state = '<span class="live-dot"></span><b>Starting run…</b>';
    } else if (lr) {
      const dot = lr.status === 'passed' ? 'passed' : (lr.status === 'failed' ? 'failed' : '');
      const git = lr.git_branch ? ` · ${esc(lr.git_branch)}${lr.git_sha ? '@' + esc(lr.git_sha) : ''}` : '';
      state = `<span class="idle-dot ${dot}"></span>Last run <b>#${lr.n || lr.id}</b><span class="muted" data-tip="${esc(absTime(lr.finished || lr.started))}">${relTime(lr.finished || lr.started)} · ${fmtDur(lr.duration)}${git}</span>`;
    } else {
      state = '<span class="muted">No runs recorded yet</span>';
    }
    const failing = failingCount(b.counts) + (b.counts.blocked || 0);
    const busy = !!(ar || (L && L.running));
    const stopBtn = L && L.running
      ? '<button class="btn danger" data-act="stop">Stop</button>' : '';
    const pct = ar && ar.total ? (100 * ar.done / ar.total) : 0;
    const oldMenu = $('#pmenu');
    const menuState = oldMenu && !oldMenu.hidden ? oldMenu.innerHTML : null;
    top.innerHTML = `
      <a class="brand" href="/"><span class="logo"></span>specdeck</a>
      <span class="crumb">/</span>
      <div class="switcher">
        <button data-act="projects" aria-haspopup="menu">${esc(b.project.name)}<span class="caret"></span></button>
        <div class="menu" id="pmenu" hidden></div>
      </div>
      <div class="runstate">${state}</div>
      <div class="spacer"></div>
      <div class="top-actions">
        ${stopBtn}
        <button class="btn" data-act="run-failing" ${busy || !failing ? 'disabled' : ''} data-tip="Re-run failing and blocked scenarios">Run failing${failing ? ` <span class="faint">${failing}</span>` : ''}</button>
        <button class="btn primary" data-act="run-all" ${busy ? 'disabled' : ''}>
          <svg viewBox="0 0 16 16" fill="currentColor"><path d="M4.5 2.8v10.4c0 .5.6.8 1 .5l8-5.2a.6.6 0 000-1l-8-5.2c-.4-.3-1 0-1 .5z"/></svg>Run all</button>
      </div>
      ${ar ? `<div class="progress-line" style="width:${pct}%"></div>` : ''}`;
    if (menuState !== null) { const m = $('#pmenu'); m.innerHTML = menuState; m.hidden = false; }
  }

  function renderBanner() {
    const el = $('#banner');
    const v = S.board.view;
    if (!v.run_id || !v.run) { el.innerHTML = ''; return; }
    const r = v.run;
    el.innerHTML = `<div class="banner">
      <span>Viewing <b>run #${r.n || r.id}</b> · ${esc(relTime(r.started))}${r.full ? '' : ` · partial run of ${plural(r.total, 'scenario')}`}${r.git_branch ? ` · ${esc(r.git_branch)}@${esc(r.git_sha)}` : ''}</span>
      <span class="spacer"></span><button class="btn sm primary" data-act="latest">Back to latest</button></div>`;
  }

  function renderSummary() {
    const b = S.board, c = b.counts, total = c.total || 0;
    const legend = ORDER.filter((k) => c[k] || k === 'passed').map((k) =>
      `<button data-filter-status="${k}" class="${S.filter.status === k ? 'on' : ''}">${icon(k)}${LABEL[k]} <b>${c[k] || 0}</b></button>`).join('');
    const d = b.delta || {};
    const chips = [];
    if (d.fixed?.length) chips.push(`<button class="chip up ${S.filter.idsLabel === 'fixed' ? 'on' : ''}" data-delta="fixed">▲ ${plural(d.fixed.length, 'newly passing', 'newly passing')}</button>`);
    if (d.regressed?.length) chips.push(`<button class="chip down ${S.filter.idsLabel === 'regressed' ? 'on' : ''}" data-delta="regressed">▼ ${plural(d.regressed.length, 'regression')}</button>`);
    if (d.new?.length) chips.push(`<button class="chip new ${S.filter.idsLabel === 'new' ? 'on' : ''}" data-delta="new">+ ${plural(d.new.length, 'new scenario')}</button>`);
    const deltas = chips.length ? `<div class="deltas">${chips.join('')}<span class="faint" style="font-size:12px">in run #${d.run_n || d.run_id}</span></div>` : '';
    $('#summary').innerHTML = `
      <div>
        <div class="kpi-num">${c.passed || 0}<span>/ ${total}</span></div>
        <div class="kpi-label">${total ? 'scenarios passing' : 'no scenarios yet'}</div>
      </div>
      <div class="sum-mid">
        ${segbar(c, 'big')}
        <div class="legend">${legend}</div>
        ${deltas}
      </div>
      <div class="trend">${trendSvg(b.trend)}</div>`;
  }

  function trendSvg(trend) {
    if (!trend || !trend.length) return '<div class="cap">No complete runs yet</div>';
    const W = 220, H = 44, n = trend.length, gap = 2;
    const bw = Math.max(3, Math.min(10, Math.floor((W - (n - 1) * gap) / n)));
    const x0 = W - (n * bw + (n - 1) * gap);
    let bars = '';
    trend.forEach((r, i) => {
      const x = x0 + i * (bw + gap);
      const tot = Math.max(r.total || 0, 1);
      const parts = [['passed', r.passed], ['failed', (r.failed || 0) + (r.error || 0)], ['blocked', r.blocked],
        ['notrun', Math.max(0, tot - (r.passed || 0) - (r.failed || 0) - (r.error || 0) - (r.blocked || 0))]];
      let y = H;
      parts.forEach(([k, v]) => {
        if (!v) return;
        const h = (v / tot) * H;
        y -= h;
        bars += `<rect x="${x}" y="${y.toFixed(1)}" width="${bw}" height="${h.toFixed(1)}" class="seg-${k}" rx="1"></rect>`;
      });
      const tip = `Run #${r.n || r.id} · ${relTime(r.started)}\n${r.passed} passing · ${(r.failed || 0) + (r.error || 0)} failing · ${r.blocked || 0} blocked`;
      if (S.viewRun === r.id) bars += `<rect class="viewed" x="${x - 1}" y="0.5" width="${bw + 2}" height="${H - 1}" rx="2"></rect>`;
      bars += `<rect class="hit" x="${x - 1}" y="0" width="${bw + 2}" height="${H}" data-run="${r.id}" data-tip="${esc(tip)}"></rect>`;
    });
    // fill styles come from the seg-* classes via CSS background; map them to fills here
    bars = bars.replace(/class="seg-(\w+)"/g, (_, k) => `style="fill:${COLOR[k]}${k === 'notrun' ? ';opacity:.6' : ''}"`);
    return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${bars}</svg><div class="cap">${n === 1 ? '1 complete run so far' : `last ${n} complete runs`}</div>`;
  }

  function renderTabs() {
    const b = S.board;
    const tabs = [['overview', 'Overview', ''], ['graph', 'Graph', ''], ['history', 'History', b.runs_total ? b.runs_total : '']];
    $('#tabs').innerHTML = tabs.map(([k, l, n]) =>
      `<button data-tab="${k}" class="${S.tab === k ? 'on' : ''}">${l}${n !== '' ? `<span class="count">${n}</span>` : ''}</button>`).join('') +
      `<span class="hint"><span><span class="kbd">/</span> search</span><span><span class="kbd">j</span><span class="kbd">k</span> move</span><span><span class="kbd">esc</span> close</span></span>`;
  }

  function renderTabBody() {
    S.renderedTab = S.tab;
    const body = $('#tabbody');
    if (S.tab === 'overview') {
      body.innerHTML = `
        <div class="ov">
          <div>
            <div class="toolbar">
              <label class="search"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="4.8"/><path d="M10.6 10.6L14 14" stroke-linecap="round"/></svg>
              <input id="q" placeholder="Filter by name, id, tag or error…" value="${esc(S.filter.text)}" autocomplete="off" spellcheck="false"></label>
              <button class="btn" data-act="toggle-all" data-tip="Collapse or expand every group">Collapse all</button>
            </div>
            <div id="fnote"></div>
            <div id="features"></div>
          </div>
          <div class="side" id="side"></div>
        </div>`;
      $('#q').addEventListener('input', (e) => { S.filter.text = e.target.value; renderFeatures(); });
    } else if (S.tab === 'graph') {
      S.graph.key = null;
      body.innerHTML = `
        <div class="graph-tools">
          <label class="search"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="4.8"/><path d="M10.6 10.6L14 14" stroke-linecap="round"/></svg>
          <input id="gq" placeholder="Find a scenario…" value="${esc(S.graph.search)}" autocomplete="off" spellcheck="false"></label>
          <div class="gchips" id="gchips"></div>
        </div>
        <div class="card graph-wrap" id="gwrap">
          <svg id="gsvg"><defs>
            <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L9 5L0 9z" style="fill:var(--faint)"/></marker>
          </defs><g id="gview"></g></svg>
          <div class="graph-legend">${ORDER.map((k) => `<span>${icon(k)}${LABEL[k]}</span>`).join('')}<span>${icon('passed', 'running')}Running</span></div>
          <div class="graph-zoom">
            <button class="btn icon" data-act="zoom-in" data-tip="Zoom in">+</button>
            <button class="btn icon" data-act="zoom-out" data-tip="Zoom out">−</button>
            <button class="btn icon" data-act="fit" data-tip="Fit to screen">⤢</button>
          </div>
        </div>`;
      $('#gq').addEventListener('input', (e) => { S.graph.search = e.target.value; updateGraph(); });
      $('#gq').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const m = graphMatches();
          if (m.length) { openScenario(m[0]); centerOn(m[0]); }
        }
      });
      bindGraph();
    } else {
      body.innerHTML = '<div class="card" id="runs"></div>';
    }
    updateTabBody();
  }

  function updateTabBody() {
    if (S.tab === 'overview') { renderFilterNote(); renderFeatures(); renderSide(); }
    else if (S.tab === 'graph') { ensureGraph(); updateGraph(); }
    else renderHistory();
  }

  // ------------------------------------------------------------------ overview
  function filterActive() { return !!(S.filter.text || S.filter.status || S.filter.ids); }
  function matches(s) {
    const f = S.filter;
    if (f.status && s.status !== f.status) return false;
    if (f.ids && !f.ids.has(s.id)) return false;
    if (f.text) {
      const hay = `${s.name} ${s.id} ${(s.tags || []).join(' ')} ${s.group_name} ${s.message || ''} ${s.nodeid || ''}`.toLowerCase();
      if (!f.text.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  function renderFilterNote() {
    const el = $('#fnote');
    if (!el) return;
    const f = S.filter, bits = [];
    if (f.status) bits.push(`status: <b>${LABEL[f.status]}</b>`);
    if (f.ids) bits.push(`<b>${esc({ fixed: 'newly passing', regressed: 'regressions', new: 'new scenarios' }[f.idsLabel] || 'selection')}</b>`);
    el.innerHTML = bits.length ? `<div class="filter-note">Showing ${bits.join(' · ')} <a href="#" data-act="clear-filter">Clear</a></div>` : '';
  }

  function blockedNote(s) {
    const roots = (s.blocked_by || []).filter((id) => S.byId.has(id));
    if (!roots.length) return 'Waiting on a failing prerequisite';
    return `Waiting on ${nameOf(roots[0])}${roots.length > 1 ? ` (+${roots.length - 1})` : ''}`;
  }

  function rowHtml(s) {
    let msg = '';
    if (BAD.has(s.status) && s.message) msg = `<div class="row-msg">${esc(s.message)}</div>`;
    else if (s.status === 'blocked') msg = `<div class="row-msg blocked">${esc(blockedNote(s))}</div>`;
    else if (s.status === 'skipped' && s.message) msg = `<div class="row-msg skipped">${esc(s.message)}</div>`;
    const meta = [];
    if (s.holding_up) meta.push(`<span class="badge hot" data-tip="${plural(s.holding_up, 'scenario')} downstream can't pass until this does">holds up ${s.holding_up}</span>`);
    else if (s.downstream) meta.push(`<span class="badge" data-tip="${plural(s.downstream, 'scenario')} depend on this, directly or indirectly">⤷ ${s.downstream}</span>`);
    if (s.duration != null && s.status !== 'blocked') meta.push(`<span>${fmtDur(s.duration)}</span>`);
    const cls = ['row', s.live ? `live-${s.live}` : '', S.sel === s.id ? 'sel' : ''].join(' ');
    return `<div class="${cls}" data-sid="${esc(s.id)}">${icon(s.status, s.live)}
      <div style="min-width:0"><div class="row-name">${titleHtml(s)}</div>${msg}</div>
      <div class="row-meta">${meta.join('')}</div></div>`;
  }

  function renderFeatures() {
    const el = $('#features');
    if (!el) return;
    const b = S.board;
    if (!b.scenarios.length) {
      el.innerHTML = '<div class="card empty"><b>No scenarios recorded</b>Run the test suite with <code>--specdeck-live</code>.</div>';
      return;
    }
    const active = filterActive();
    let html = '';
    let shown = 0;
    b.groups.forEach((g) => {
      const items = b.scenarios.filter((s) => s.group === g.id && matches(s));
      if (active && !items.length) return;
      shown += items.length;
      const collapsed = !active && S.collapsed.has(g.id);
      const sub = [];
      if (g.failed + g.error) sub.push(`<span style="color:var(--fail)">${g.failed + g.error} failing</span>`);
      if (g.blocked) sub.push(`<span style="color:var(--blocked)">${g.blocked} blocked</span>`);
      if (g.notrun) sub.push(`${g.notrun} not run`);
      if (!sub.length && g.passed === g.total) sub.push('all passing');
      html += `<section class="card group ${collapsed ? 'collapsed' : ''}" data-group="${esc(g.id)}">
        <div class="group-h" data-toggle-group="${esc(g.id)}">
          <span class="chev"></span>
          <div style="min-width:0"><div class="gname" data-tip="${esc(g.name)}">${esc(g.name)}</div><div class="gsub">${sub.join(' · ')}</div></div>
          ${segbar(g, 'sm')}
          <div class="gfrac">${g.passed}<span> / ${g.total}</span></div>
        </div>
        <div class="rows">${items.map(rowHtml).join('')}</div>
      </section>`;
    });
    if (!shown) html = '<div class="card empty"><b>Nothing matches</b>Try a different filter. <a href="#" data-act="clear-filter">Clear filter</a></div>';
    el.innerHTML = html;
    const tb = $('[data-act="toggle-all"]');
    if (tb) tb.textContent = S.collapsed.size >= b.groups.length ? 'Expand all' : 'Collapse all';
  }

  function sideItem(id, extra) {
    const s = S.byId.get(id);
    if (!s) return '';
    return `<div class="item" data-sid="${esc(id)}">${icon(s.status, s.live)}<div style="min-width:0"><div class="item-name">${titleHtml(s)}</div>${extra || ''}</div></div>`;
  }

  function renderSide() {
    const el = $('#side');
    if (!el) return;
    const a = S.board.analysis, c = S.board.counts;
    // Stuck at
    let stuck;
    if (a.frontier.length) {
      const fam = new Map();  // parametrized cases of one test count once
      a.frontier.forEach((id) => {
        const s = S.byId.get(id);
        const k = s.parametrized ? `${s.file}::${s.function}` : id;
        if (!fam.has(k)) fam.set(k, []);
        fam.get(k).push(id);
      });
      const heads = [...fam.values()];
      stuck = heads.slice(0, 6).map((ids) => {
        const s = S.byId.get(ids[0]);
        const hold = ids.reduce((n, id) => n + (S.byId.get(id).holding_up || 0), 0);
        const sub = [];
        if (hold) sub.push(`<span class="badge hot">holds up ${hold}</span>`);
        if (ids.length > 1) sub.push(`<span class="badge">${ids.length} cases</span>`);
        const extra = `${s.message ? `<div class="item-msg" data-tip="${esc(s.message)}">${esc(s.message)}</div>` : ''}${sub.length ? `<div class="item-sub">${sub.join(' ')}</div>` : ''}`;
        if (ids.length > 1) {
          return `<div class="item" data-sid="${esc(ids[0])}">${icon(s.status, s.live)}<div style="min-width:0"><div class="item-name">${fmt(splitParam(s).base)}</div>${extra}</div></div>`;
        }
        return sideItem(ids[0], extra);
      }).join('') + (heads.length > 6 ? `<a class="more" data-filter-status="failed">Show all ${a.frontier.length} failing</a>` : '');
    } else if (c.total && c.passed === c.total) {
      stuck = `<div class="ok-state">${icon('passed')}Everything passes.</div>`;
    } else {
      stuck = `<div class="ok-state">${icon('passed')}Nothing is failing.${c.notrun ? ` ${plural(c.notrun, 'scenario')} not run yet.` : ''}</div>`;
    }
    // Goals
    let goals = '';
    if (a.goals.length) {
      const limit = S.showAllGoals ? a.goals.length : 5;
      goals = a.goals.slice(0, limit).map((g) => {
        const st = g.state === 'complete' ? 'Complete' : (g.state === 'stuck' ? 'Stuck' : 'In progress');
        const at = g.stuck_at.length ? `<div class="item-sub">stuck at <a data-sid-link="${esc(g.stuck_at[0])}">${esc(nameOf(g.stuck_at[0]))}</a>${g.stuck_at.length > 1 ? ` +${g.stuck_at.length - 1}` : ''}</div>` : '';
        const s = S.byId.get(g.id);
        return `<div class="item" data-sid="${esc(g.id)}">${icon(s.status, s.live)}<div style="min-width:0">
          <div class="item-name">${titleHtml(s)}</div>
          ${segbar(g.counts, 'sm')}
          <div class="item-sub"><span class="pill plain ${g.state}" style="height:20px;font-size:11px">${st}</span> ${g.counts.passed} of ${g.total} in this branch passing</div>
          ${at}</div></div>`;
      }).join('') + (a.goals.length > limit ? `<a class="more" data-act="all-goals">Show all ${a.goals.length} goals</a>` : '');
    }
    // Critical
    const crit = a.critical.slice(0, 5).map((id) => {
      const s = S.byId.get(id);
      return sideItem(id, `<div class="item-sub">${plural(s.downstream, 'scenario')} depend on this</div>`);
    }).join('');

    el.innerHTML = `
      <div class="card"><h3>Stuck at</h3><div class="sub">Failing, with every prerequisite passing. Fix these first.</div>${stuck}</div>
      ${goals ? `<div class="card"><h3>Goals</h3><div class="sub">End-to-end scenarios and everything they rely on.</div>${goals}</div>` : ''}
      ${crit ? `<div class="card"><h3>Load-bearing</h3><div class="sub">The scenarios most others depend on.</div>${crit}</div>` : ''}`;
  }

  // ------------------------------------------------------------------ graph
  const NW = 232, NH = 46, GX = 96, GY = 14;

  function layoutGraph(scen) {
    const ids = scen.map((s) => s.id);
    const has = new Set(ids);
    const deps = new Map(scen.map((s) => [s.id, (s.depends_on || []).filter((d) => has.has(d) && d !== s.id)]));
    const kids = new Map(ids.map((i) => [i, []]));
    deps.forEach((ds, i) => ds.forEach((d) => kids.get(d).push(i)));
    const connected = ids.filter((i) => deps.get(i).length || kids.get(i).length);
    const isolated = ids.filter((i) => !deps.get(i).length && !kids.get(i).length);
    const layer = new Map(), visiting = new Set();
    const L = (i) => {
      if (layer.has(i)) return layer.get(i);
      if (visiting.has(i)) return 0;
      visiting.add(i);
      const ds = deps.get(i);
      const v = ds.length ? 1 + Math.max(...ds.map(L)) : 0;
      visiting.delete(i);
      layer.set(i, v);
      return v;
    };
    connected.forEach(L);
    const layers = [];
    connected.forEach((i) => { (layers[layer.get(i)] ||= []).push(i); });
    for (let li = 0; li < layers.length; li++) layers[li] ||= [];
    const pos = new Map();
    const index = (l) => l.forEach((id, k) => pos.set(id, k));
    layers.forEach(index);
    const bary = (id, list) => (list.length ? list.reduce((a, x) => a + pos.get(x), 0) / list.length : pos.get(id));
    for (let it = 0; it < 8; it++) {
      for (let li = 1; li < layers.length; li++) {
        const l = layers[li];
        const key = new Map(l.map((id) => [id, bary(id, deps.get(id))]));
        l.sort((a, b) => key.get(a) - key.get(b));
        index(l);
      }
      for (let li = layers.length - 2; li >= 0; li--) {
        const l = layers[li];
        const key = new Map(l.map((id) => [id, bary(id, kids.get(id))]));
        l.sort((a, b) => key.get(a) - key.get(b));
        index(l);
      }
    }
    const nodes = new Map();
    const maxN = layers.length ? Math.max(...layers.map((l) => l.length)) : 0;
    layers.forEach((l, li) => {
      const off = ((maxN - l.length) * (NH + GY)) / 2;
      l.forEach((id, k) => nodes.set(id, { x: li * (NW + GX), y: off + k * (NH + GY) }));
    });
    const labels = [];
    let W = layers.length ? layers.length * (NW + GX) - GX : 0;
    let H = maxN ? maxN * (NH + GY) - GY : 0;
    if (connected.length) labels.push({ x: 0, y: -18, text: 'Dependencies flow left to right' });
    if (isolated.length) {
      const y0 = connected.length ? H + 64 : 0;
      const cols = Math.max(3, Math.min(6, layers.length || 4));
      labels.push({ x: 0, y: y0 - 18, text: `Independent scenarios · ${isolated.length}` });
      isolated.forEach((id, k) => nodes.set(id, { x: (k % cols) * (NW + 24), y: y0 + Math.floor(k / cols) * (NH + GY) }));
      W = Math.max(W, cols * (NW + 24) - 24);
      H = y0 + Math.ceil(isolated.length / cols) * (NH + GY) - GY;
    }
    const edges = [];
    deps.forEach((ds, i) => ds.forEach((d) => edges.push({ from: d, to: i })));
    return { nodes, edges, labels, W, H, deps, kids };
  }

  function trunc(s, n) { return s.length > n ? `${s.slice(0, n - 1)}…` : s; }

  function ensureGraph() {
    const scen = S.board.scenarios;
    const key = scen.map((s) => `${s.id}<${(s.depends_on || []).join(',')}`).join('|');
    if (key === S.graph.key) return;
    S.graph.key = key;
    const lay = layoutGraph(scen);
    S.graph.layout = lay;
    let g = '';
    lay.labels.forEach((l) => { g += `<text class="glabel" x="${l.x}" y="${l.y}">${esc(l.text)}</text>`; });
    lay.edges.forEach((e) => {
      const a = lay.nodes.get(e.from), b = lay.nodes.get(e.to);
      const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x - 2, y2 = b.y + NH / 2;
      const dx = Math.max(40, (x2 - x1) / 2);
      g += `<path class="ge" data-from="${esc(e.from)}" data-to="${esc(e.to)}" d="M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}" marker-end="url(#arr)"></path>`;
    });
    scen.forEach((s) => {
      const p = lay.nodes.get(s.id);
      const sub = `${s.group_name}${s.downstream ? ` · ⤷ ${s.downstream}` : ''}`;
      g += `<g class="gn" data-sid="${esc(s.id)}" transform="translate(${p.x},${p.y})">
        <rect class="body" width="${NW}" height="${NH}" rx="9"></rect>
        <rect class="gstripe" x="5" y="9" width="3" height="${NH - 18}" rx="1.5" style="fill:${groupColor(s.group)}"></rect>
        <circle class="dot" cx="21" cy="${NH / 2}" r="5"></circle>
        <text x="34" y="19">${esc(trunc(s.name.replace(/`/g, ''), 31))}</text>
        <text class="gsub-t" x="34" y="34">${esc(trunc(sub, 38))}</text>
      </g>`;
    });
    $('#gview').innerHTML = g;
    const chips = S.board.groups.map((gr) =>
      `<button class="gchip ${S.graph.group === gr.id ? 'on' : ''}" data-ggroup="${esc(gr.id)}" data-tip="${esc(gr.name)}"><i style="background:${groupColor(gr.id)}"></i>${esc(trunc(gr.name, 26))}<span class="faint">${gr.passed}/${gr.total}</span></button>`).join('');
    $('#gchips').innerHTML = chips;
    if (!S.graph.fitted) {
      fitGraph(0.85);
      S.graph.fitted = true;
      if (S.sel && lay.nodes.has(S.sel)) centerOn(S.sel);
    } else applyT();
  }

  function graphMatches() {
    const q = S.graph.search.trim().toLowerCase();
    if (!q) return [];
    return S.board.scenarios.filter((s) => `${s.name} ${s.id} ${(s.tags || []).join(' ')}`.toLowerCase().includes(q)).map((s) => s.id);
  }

  function related(id) {
    const lay = S.graph.layout;
    const out = new Set([id]);
    const walk = (start, edges) => {
      const st = [start];
      while (st.length) {
        const n = st.pop();
        (edges.get(n) || []).forEach((m) => { if (!out.has(m)) { out.add(m); st.push(m); } });
      }
    };
    walk(id, lay.deps);
    walk(id, lay.kids);
    return out;
  }

  function updateGraph() {
    if (!S.graph.layout) return;
    const q = graphMatches();
    const qs = new Set(q);
    let focus = null;
    if (S.sel && S.byId.has(S.sel)) focus = related(S.sel);
    else if (S.graph.group) focus = new Set(S.board.scenarios.filter((s) => s.group === S.graph.group).map((s) => s.id));
    else if (q.length) focus = qs;
    else if (filterActive()) focus = new Set(S.board.scenarios.filter(matches).map((s) => s.id));
    $$('.gn').forEach((el) => {
      const s = S.byId.get(el.dataset.sid);
      if (!s) return;
      const cls = ['gn', `st-${s.status}`];
      if (s.live) cls.push(`live-${s.live}`);
      if (S.sel === s.id) cls.push('sel');
      if (qs.has(s.id)) cls.push('match');
      if (focus && !focus.has(s.id)) cls.push('dim');
      el.setAttribute('class', cls.join(' '));
      const tip = `${s.name}\n${LABEL[s.status]}${s.live ? ` · ${s.live}` : ''}${s.message ? `\n${s.message}` : ''}`;
      el.setAttribute('data-tip', tip);
    });
    $$('.ge').forEach((el) => {
      const f = el.dataset.from, t = el.dataset.to;
      const cls = ['ge'];
      const fs = S.byId.get(f);
      if (fs && BAD.has(fs.status)) cls.push('bad');
      if (focus) { if (focus.has(f) && focus.has(t)) { if (S.sel) cls.push('hl'); } else cls.push('dim'); }
      el.setAttribute('class', cls.join(' '));
    });
    $$('.gchip').forEach((el) => el.classList.toggle('on', S.graph.group === el.dataset.ggroup));
  }

  function applyT() {
    const t = S.graph.t, v = $('#gview');
    if (v) v.setAttribute('transform', `translate(${t.x},${t.y}) scale(${t.k})`);
  }
  // minK: never open smaller than this; a big graph then starts at its top-left corner
  function fitGraph(minK = 0) {
    const wrap = $('#gwrap'), lay = S.graph.layout;
    if (!wrap || !lay) return;
    const cw = wrap.clientWidth, ch = wrap.clientHeight;
    const W = Math.max(lay.W, 1), H = Math.max(lay.H + 30, 1);
    const fit = Math.max(0.2, Math.min((cw - 60) / W, (ch - 80) / H, 1.1));
    if (fit >= minK) S.graph.t = { k: fit, x: (cw - W * fit) / 2, y: Math.max(40, (ch - H * fit) / 2 + 24) };
    else S.graph.t = { k: minK, x: 28, y: 48 };
    applyT();
  }
  function zoomAt(factor, cx, cy) {
    const t = S.graph.t;
    const k2 = Math.min(2.5, Math.max(0.2, t.k * factor));
    t.x = cx - ((cx - t.x) * k2) / t.k;
    t.y = cy - ((cy - t.y) * k2) / t.k;
    t.k = k2;
    applyT();
  }
  function centerOn(id) {
    const p = S.graph.layout?.nodes.get(id), wrap = $('#gwrap');
    if (!p || !wrap) return;
    const t = S.graph.t;
    t.x = wrap.clientWidth / 2 - (p.x + NW / 2) * t.k;
    t.y = wrap.clientHeight / 2 - (p.y + NH / 2) * t.k;
    applyT();
  }
  function bindGraph() {
    const wrap = $('#gwrap');
    let drag = null;
    wrap.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r = wrap.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0016), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    wrap.addEventListener('pointerdown', (e) => {
      if (e.target.closest('.graph-zoom, .graph-legend')) return;
      drag = { x: e.clientX, y: e.clientY, tx: S.graph.t.x, ty: S.graph.t.y, moved: false, node: e.target.closest('.gn') };
      wrap.setPointerCapture(e.pointerId);
    });
    wrap.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; wrap.classList.add('panning'); }
      if (drag.moved) { S.graph.t.x = drag.tx + dx; S.graph.t.y = drag.ty + dy; applyT(); }
    });
    wrap.addEventListener('pointerup', () => {
      if (drag && !drag.moved) {
        if (drag.node) openScenario(drag.node.dataset.sid);
        else if (S.sel) closeDrawer();
      }
      drag = null;
      wrap.classList.remove('panning');
    });
  }

  // ------------------------------------------------------------------ history
  function renderHistory() {
    const el = $('#runs');
    if (!el) return;
    const runs = S.runs || [];
    if (!runs.length) { el.innerHTML = '<div class="empty"><b>No runs yet</b>Run <code>pytest --specdeck-live</code>.</div>'; return; }
    const rows = runs.map((r) => {
      const done = r.status === 'running' ? (r.done || 0) : null;
      const counts = { passed: r.passed, failed: r.failed, error: r.error, blocked: r.blocked, skipped: r.skipped,
        notrun: Math.max(0, r.total - r.passed - r.failed - r.error - r.blocked - r.skipped) };
      const txt = r.status === 'running'
        ? `<b>${done}</b> of ${r.total} done`
        : `<b>${r.passed}</b> passing${r.failed + r.error ? ` · <b style="color:var(--fail)">${r.failed + r.error}</b> failing` : ''}${r.blocked ? ` · ${r.blocked} blocked` : ''}`;
      const stPill = r.status === 'running' ? '<span class="pill running"><span class="ic ic-running"></span>Running</span>'
        : r.status === 'passed' ? `<span class="pill passed">${icon('passed')}Passed</span>`
        : r.status === 'failed' ? `<span class="pill failed">${icon('failed')}Failed</span>`
        : `<span class="pill ${esc(r.status)} plain">${esc(r.status[0].toUpperCase() + r.status.slice(1))}</span>`;
      const change = [];
      if (r.fixed.length) change.push(`<span class="up" data-tip="Newly passing:\n${esc(r.fixed.slice(0, 12).map(nameOf).join('\n'))}">▲ ${r.fixed.length}</span>`);
      if (r.regressed.length) change.push(`<span class="down" data-tip="Regressed:\n${esc(r.regressed.slice(0, 12).map(nameOf).join('\n'))}">▼ ${r.regressed.length}</span>`);
      if (r.new.length) change.push(`<span class="faint" data-tip="New scenarios">+${r.new.length} new</span>`);
      const src = `${r.trigger === 'ui' ? 'Viewer' : 'Terminal'}${r.git_branch ? ` · <code>${esc(r.git_branch)}@${esc(r.git_sha)}</code>` : ''}`;
      return `<tr data-run="${r.id}" class="${S.viewRun === r.id ? 'viewing' : ''}">
        <td><span class="run-id">#${r.n || r.id}</span>${r.full ? '' : ' <span class="badge" data-tip="Only part of the suite ran">partial</span>'}</td>
        <td data-tip="${esc(absTime(r.started))}">${relTime(r.started)}</td>
        <td><span class="src">${src}</span></td>
        <td><div class="run-res">${segbar(counts, 'sm')}<span class="txt">${txt}</span></div></td>
        <td>${stPill}</td>
        <td><div class="change">${change.join('') || '<span class="faint">—</span>'}</div></td>
        <td class="num muted">${r.duration != null ? fmtDur(r.duration) : ''}</td></tr>`;
    }).join('');
    el.innerHTML = `<table class="runs-table"><thead><tr><th>Run</th><th>When</th><th>Started from</th><th>Result</th><th>Status</th><th>Change</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  // ------------------------------------------------------------------ drawer
  const cur = () => (S.sel ? S.byId.get(S.sel) : null);

  function openScenario(id) {
    if (S.edit && S.edit.id !== id && editDirty() && !confirm('Discard your unsaved edits?')) return;
    if (S.edit && S.edit.id !== id) S.edit = null;
    S.sel = id;
    S.showSource = false;
    setHash();
    renderDrawer();
    $$('.row.sel').forEach((el) => el.classList.remove('sel'));
    $$(`.row[data-sid="${CSS.escape(id)}"]`).forEach((el) => { el.classList.add('sel'); });
    const row = $(`#features .row[data-sid="${CSS.escape(id)}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
    if (S.tab === 'graph') updateGraph();
  }
  function closeDrawer() {
    if (S.edit && editDirty() && !confirm('Discard your unsaved edits?')) return;
    S.edit = null;
    S.sel = null;
    setHash();
    renderDrawer();
    $$('.row.sel').forEach((el) => el.classList.remove('sel'));
    if (S.tab === 'graph') updateGraph();
  }

  async function loadHist(s) {
    if (S.histFor[s.id] === s.run_id && S.hist[s.id]) return;
    S.histFor[s.id] = s.run_id;
    try {
      const r = await api(`/api/p/${enc(S.slug)}/history?id=${enc(s.id)}`);
      S.hist[s.id] = r.history;
      const el = $('#dr-hist');
      if (el && S.sel === s.id) el.innerHTML = histHtml(s);
    } catch { /* non-fatal */ }
  }

  function histHtml(s) {
    const h = S.hist[s.id];
    if (!h) return '<div class="hist-cap">Loading…</div>';
    if (!h.length) return '<div class="hist-cap">No finished runs of this scenario yet.</div>';
    const dots = h.map((x) => `<i style="background:${COLOR[x.status] || COLOR.notrun}${x.status === 'skipped' ? ';opacity:.5' : ''}" data-run="${x.run_id}" data-tip="Run #${x.n || x.run_id} · ${esc(relTime(x.started))}\n${esc(LABEL[x.status])}${x.duration != null && x.status !== 'blocked' ? ` · ${esc(fmtDur(x.duration))}` : ''}${x.message ? `\n${esc(x.message).slice(0, 200)}` : ''}"></i>`).join('');
    const passes = h.filter((x) => x.status === 'passed').length;
    let streak = 0;
    for (let i = h.length - 1; i >= 0 && h[i].status === h[h.length - 1].status; i--) streak++;
    const last = h[h.length - 1].status;
    return `<div class="hist-dots">${dots}</div><div class="hist-cap">Passed in ${passes} of the last ${plural(h.length, 'run')} · ${LABEL[last].toLowerCase()} for the last ${plural(streak, 'run')}</div>`;
  }

  function stepsFor(s) {
    if (s.steps && s.steps.length && s.status !== 'notrun') return s.steps;
    return (s.planned || []).map(([keyword, text, expected]) => ({
      keyword, text, assertion: expected ? { expected_label: expected } : null, state: 'pending', notyet: true,
    }));
  }

  function stepHtml(st) {
    const state = st.state || 'ok';
    const a = st.assertion || {};
    const kw = (st.keyword || '').toLowerCase();
    let lbl = '';
    if (a.expected_label != null && a.expected_label !== '') lbl += `<span>expected</span><code>${esc(a.expected_label)}</code>`;
    if (state === 'failed' && a.observed_label != null) lbl += `<span class="got"><span>got</span> <code>${esc(a.observed_label)}</code></span>`;
    let note = '';
    if (state === 'pending') {
      note = st.notyet ? 'not run yet' : (a.observed_label === 'not reached' ? 'not reached: the test stopped at an earlier step' : 'not run');
    }
    return `<li class="step ${state}"><span class="kw kw-${esc(kw)}">${esc(st.keyword)}</span>
      <div><div class="step-text">${fmt(st.text)}</div>${lbl ? `<div class="step-lbl">${lbl}</div>` : ''}${note ? `<div class="step-state">${note}</div>` : ''}</div></li>`;
  }

  function relChip(id) {
    const s = S.byId.get(id);
    if (!s) return '';
    return `<span class="rel-chip" data-sid-link="${esc(id)}" data-tip="${esc(s.name)}">${icon(s.status, s.live)}<span>${esc(trunc(s.name.replace(/`/g, ''), 46))}</span></span>`;
  }

  function renderDrawer() {
    const d = $('#drawer');
    const s = cur();
    if (!s) {
      d.classList.remove('open');
      document.body.classList.remove('drawer-open');
      return;
    }
    if (S.edit && S.edit.id === s.id) { renderEditor(); return; }
    const keep = $('.dr-body', d)?.scrollTop || 0;
    const sameScenario = d.dataset.sid === s.id;
    d.dataset.sid = s.id;
    const when = [];
    if (s.duration != null && s.status !== 'blocked') when.push(fmtDur(s.duration));
    if (s.run_id) {
      const rn = (S.runs || []).find((r) => r.id === s.run_id)?.n || s.run_id;
      when.push(`<a href="#" data-run-link="${s.run_id}">run #${rn}</a>`);
    }
    let alert = '';
    if (BAD.has(s.status) && s.message) alert = `<div class="alert fail"><code>${esc(s.message)}</code></div>`;
    else if (s.status === 'blocked') {
      const roots = (s.blocked_by || []).filter((id) => S.byId.has(id));
      alert = roots.length
        ? `<div class="alert blocked">Blocked. It runs once ${roots.length === 1 ? 'this passes' : 'these pass'}:<div class="rel" style="margin-top:8px">${roots.map(relChip).join('')}</div></div>`
        : '<div class="alert blocked">Blocked. It runs once its failing prerequisite passes.</div>';
    } else if (s.status === 'skipped' && s.message) alert = `<div class="alert blocked">Skipped: ${esc(s.message)}</div>`;
    else if (s.status === 'notrun') alert = '<div class="alert info">This scenario has not run yet.</div>';
    if (S.edited[s.id] !== undefined && S.edited[s.id] === s.run_id) {
      alert += '<div class="alert info"><span>You edited this test. Run it to see the change here.</span><span class="spacer"></span><button class="btn sm primary" data-act="run-one">Run</button></div>';
    }
    const deps = (s.depends_on || []).filter((id) => S.byId.has(id));
    const needed = (s.dependents || []).filter((id) => S.byId.has(id));
    const rel = deps.length || needed.length ? `
      <div class="section"><h4>Relationships</h4>
        ${deps.length ? `<div class="rel-label">Depends on</div><div class="rel">${deps.map(relChip).join('')}</div>` : ''}
        ${needed.length ? `<div class="rel-label">Needed by${s.downstream > needed.length ? ` · ${s.downstream} downstream in total` : ''}</div><div class="rel">${needed.map(relChip).join('')}</div>` : ''}
      </div>` : '';
    const src = S.source[s.id];
    const code = S.showSource
      ? (src ? `<div class="code">${src.source.replace(/\n$/, '').split('\n').map((l, i) => `<div data-n="${src.line + i}">${esc(l) || ' '}</div>`).join('')}</div>` : '<div class="hist-cap">Loading…</div>')
      : '';
    const cmd = `pytest ${shellQuote(s.nodeid || '')}`;
    d.innerHTML = `
      <div class="dr-head">
        ${pill(s.status, s.live)}
        <span class="muted" style="font-size:12.5px">${when.join(' · ')}</span>
        <span class="spacer"></span>
        <button class="btn sm" data-act="run-one" ${S.board.active_run ? 'disabled' : ''} data-tip="Run just this scenario">Run</button>
        <button class="btn sm" data-act="edit" ${S.viewRun ? 'disabled data-tip="Return to the latest view to edit"' : ''}>Edit</button>
        <button class="btn ghost icon" data-act="close" aria-label="Close" data-tip="Close (Esc)">✕</button>
      </div>
      <div class="dr-body">
        <h2>${titleHtml(s)}</h2>
        <div class="dr-meta"><span>${esc(s.group_name)}</span><code data-copy="${esc(s.id)}" data-tip="Scenario id · click to copy" style="cursor:pointer">${esc(s.id)}</code></div>
        ${s.description ? `<div class="dr-desc">${paras(s.description)}</div>` : ''}
        ${s.tags && s.tags.length ? `<div class="tags">${s.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}
        ${alert}
        <div class="section"><h4>Steps</h4><ol class="steps">${stepsFor(s).map(stepHtml).join('') || '<li class="hist-cap">No steps.</li>'}</ol></div>
        ${rel}
        <div class="section"><h4>History</h4><div id="dr-hist">${histHtml(s)}</div></div>
        <div class="section"><h4>Source</h4>
          <div class="src"><code>${esc(s.file || '')}${s.line ? `:${s.line}` : ''}</code>
          <button class="btn sm ghost" data-act="toggle-source">${S.showSource ? 'Hide' : 'Show'} source</button></div>
          ${code}
          <div class="cmd" style="margin-top:10px"><code>${esc(cmd)}</code><button class="btn sm" data-copy="${esc(cmd)}">Copy</button></div>
          ${s.parametrized ? '<div class="hint-line">One case of a parametrized test.</div>' : ''}
        </div>
      </div>`;
    d.classList.add('open');
    document.body.classList.add('drawer-open');
    if (sameScenario) $('.dr-body', d).scrollTop = keep;
    loadHist(s);
  }

  async function toggleSource() {
    const s = cur();
    if (!s) return;
    S.showSource = !S.showSource;
    renderDrawer();
    if (S.showSource && !S.source[s.id]) {
      try { S.source[s.id] = await api(`/api/p/${enc(S.slug)}/source?id=${enc(s.id)}`); }
      catch (e) { S.showSource = false; toast(esc(e.message), { bad: true }); }
      renderDrawer();
    }
  }

  // ------------------------------------------------------------------ editor
  async function startEdit() {
    const s = cur();
    if (!s) return;
    let model;
    try { model = await api(`/api/p/${enc(S.slug)}/source?id=${enc(s.id)}`); }
    catch (e) { toast(esc(e.message), { bad: true }); return; }
    S.source[s.id] = model;
    S.edit = {
      id: s.id, model, diff: null, busy: false,
      v: {
        title: model.title.value, details: model.details.value,
        tags: [...model.tags.value], depends_on: [...model.depends_on.value],
        steps: model.steps.map((x) => ({ text: x.text, expected: x.expected })),
      },
    };
    renderEditor();
    setTimeout(() => $('#drawer [data-f="title"]')?.focus(), 30);
  }

  function collectEdits() {
    const m = S.edit.model, v = S.edit.v, e = {};
    const titleChanged = m.title.editable && v.title.trim() !== m.title.value.trim();
    const detailsChanged = m.details.editable && v.details.trim() !== m.details.value.trim();
    if (titleChanged) e.title = v.title;
    if (detailsChanged) {
      e.details = v.details;
      if (m.title.derived && m.title.via === 'docstring' && !titleChanged) e.title = v.title;
    }
    const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
    if (m.tags.editable && !same(v.tags, m.tags.value)) e.tags = v.tags;
    if (m.depends_on.editable && !same(v.depends_on, m.depends_on.value)) e.depends_on = v.depends_on;
    const steps = [];
    m.steps.forEach((st, i) => {
      const o = { index: i };
      if (st.text_editable && v.steps[i].text !== st.text) o.text = v.steps[i].text;
      if (st.expected_editable && v.steps[i].expected !== st.expected) o.expected = v.steps[i].expected;
      if (Object.keys(o).length > 1) steps.push(o);
    });
    if (steps.length) e.steps = steps;
    return e;
  }
  const editDirty = () => !!(S.edit && Object.keys(collectEdits()).length);

  function chipsHtml(field) {
    const f = S.edit.model[field];
    const vals = S.edit.v[field];
    if (!f.editable) return `<div class="chipin locked"><code>${esc(f.source || '')}</code></div><div class="lock-note">Computed in code, edit it there.</div>`;
    const list = field === 'depends_on' ? '<datalist id="dep-ids">' + S.board.scenarios.filter((s) => s.id !== S.edit.id).map((s) => `<option value="${esc(s.id)}">${esc(trunc(s.name, 80))}</option>`).join('') + '</datalist>' : '';
    return `<div class="chipin" data-chips="${field}">${vals.map((v, i) => `<span class="c">${esc(v)}<button data-unchip="${field}:${i}" aria-label="Remove">×</button></span>`).join('')}
      <input data-chipinput="${field}" placeholder="${field === 'tags' ? 'Add a tag…' : 'Add a scenario id…'}" ${field === 'depends_on' ? 'list="dep-ids"' : ''} autocomplete="off"></div>${list}`;
  }

  function renderEditor() {
    const d = $('#drawer');
    const E = S.edit, m = E.model, v = E.v;
    const lock = (x) => (x ? '' : 'disabled');
    const steps = m.steps.map((st, i) => `
      <div class="ed-step"><span class="kw kw-${esc(st.keyword.toLowerCase())}">${esc(st.keyword)}</span>
        <div class="inputs">
          <input type="text" data-step="${i}" data-k="text" value="${esc(v.steps[i].text)}" ${lock(st.text_editable)} ${st.text_editable ? '' : 'data-tip="Built with an f-string or variable. Edit it in code."'}>
          <input type="text" class="exp" data-step="${i}" data-k="expected" value="${esc(v.steps[i].expected)}" placeholder="expected label (optional)" ${lock(st.expected_editable)}>
        </div></div>`).join('');
    const diff = E.diff != null ? `<div class="diff">${E.diff ? E.diff.split('\n').map((l) => {
      const c = l.startsWith('+++') || l.startsWith('---') ? 'file' : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '';
      return `<div class="${c}">${esc(l) || ' '}</div>`;
    }).join('') : '<div>No changes.</div>'}</div>` : '';
    const foot = E.diff != null
      ? `<button class="btn" data-act="edit-back">Back to editing</button><span class="spacer"></span>
         <button class="btn" data-act="edit-save" ${E.diff ? '' : 'disabled'}>Save</button>
         <button class="btn primary" data-act="edit-save-run" ${E.diff ? '' : 'disabled'}>Save and run</button>`
      : `<button class="btn" data-act="edit-cancel">Cancel</button><span class="spacer"></span>
         <button class="btn primary" data-act="edit-preview">Review changes</button>`;
    d.innerHTML = `
      <div class="dr-head"><span class="title">Edit scenario</span><span class="muted" style="font-size:12.5px"><code>${esc(m.file)}:${m.line}</code></span>
        <span class="spacer"></span><button class="btn ghost icon" data-act="edit-cancel" aria-label="Close">✕</button></div>
      <div class="dr-body editor" ${E.diff != null ? 'style="opacity:.98"' : ''}>
        ${m.parametrized ? '<div class="alert info" style="margin:0 0 14px">This test is parametrized, so your edits apply to every case.</div>' : ''}
        <label>Title ${m.title.via === 'name' ? '<span class="faint">· from name=</span>' : '<span class="faint">· first line of the docstring</span>'}</label>
        <textarea data-f="title" rows="2" ${lock(m.title.editable)}>${esc(v.title)}</textarea>
        <label>Details</label>
        <textarea data-f="details" rows="3" placeholder="Why this behaviour matters, edge cases…" ${lock(m.details.editable)}>${esc(v.details)}</textarea>
        <label>Tags</label>${chipsHtml('tags')}
        <label>Depends on</label>${chipsHtml('depends_on')}
        <div class="hint-line">A scenario is skipped as blocked while anything it depends on fails.</div>
        <label>Steps</label>
        ${steps || '<div class="hint-line">This test has no given/when/then steps.</div>'}
        ${diff}
      </div>
      <div class="dr-foot">${foot}</div>`;
    d.classList.add('open');
    document.body.classList.add('drawer-open');
    if (E.diff != null) $('.diff', d)?.scrollIntoView({ block: 'nearest' });
  }

  function rerenderChips(field) {
    const box = $(`#drawer [data-chips="${field}"]`);
    if (!box) return;
    const holder = document.createElement('div');
    holder.innerHTML = chipsHtml(field);
    box.replaceWith(holder.firstElementChild);
    $(`#drawer [data-chipinput="${field}"]`)?.focus();
  }

  async function previewEdit() {
    const edits = collectEdits();
    if (!Object.keys(edits).length) { toast('Nothing changed yet.'); return; }
    try {
      const r = await api(`/api/p/${enc(S.slug)}/edit`, { id: S.edit.id, fingerprint: S.edit.model.fingerprint, edits, dry_run: true });
      S.edit.diff = r.diff;
      renderEditor();
    } catch (e) { toast(esc(e.message), { bad: true, ms: 8000 }); }
  }

  async function saveEdit(andRun) {
    const E = S.edit;
    const s = cur();
    try {
      const r = await api(`/api/p/${enc(S.slug)}/edit`, { id: E.id, fingerprint: E.model.fingerprint, edits: collectEdits(), dry_run: false });
      S.source[E.id] = r.model;
      S.edited[E.id] = s ? s.run_id : null;
      S.edit = null;
      toast(`${icon('passed')}<div>Saved <code>${esc(E.model.file)}</code></div>`);
      renderDrawer();
      if (andRun) runScope('ids', [E.id]);
    } catch (e) {
      toast(esc(e.message), { bad: true, ms: 9000 });
      if (e.status === 409) { S.edit.diff = null; renderEditor(); }
    }
  }

  // ------------------------------------------------------------------ actions
  async function runScope(scope, ids) {
    try {
      await api(`/api/p/${enc(S.slug)}/run`, { scope, ids });
      toast(`<span class="ic ic-running"></span>Starting ${scope === 'all' ? 'the full suite' : scope === 'failing' ? 'failing scenarios' : ids.length === 1 ? `“${esc(trunc(nameOf(ids[0]), 50))}”` : plural(ids.length, 'scenario')}…`);
      scheduleRefresh(150);
    } catch (e) { toast(esc(e.message), { bad: true }); }
  }

  async function openProjects() {
    const menu = $('#pmenu');
    if (!menu) return;
    if (!menu.hidden) { menu.hidden = true; return; }
    menu.hidden = false;
    menu.innerHTML = '<div class="hist-cap" style="padding:8px">Loading…</div>';
    try {
      const { projects } = await api('/api/projects');
      menu.innerHTML = projects.map((p) => `<a href="/p/${enc(p.slug)}" class="${p.slug === S.slug ? 'current' : ''}">
        ${p.active_run ? '<span class="live-dot"></span>' : icon(failingCount(p.counts) ? 'failed' : (p.counts.passed === p.counts.total && p.counts.total ? 'passed' : 'notrun'))}
        <div style="min-width:0"><div class="m-name">${esc(p.name)}</div><div class="m-sub">${p.counts.passed} / ${p.counts.total} passing${p.last_run ? ` · ${relTime(p.last_run.finished || p.last_run.started)}` : ''}</div></div>
        ${segbar(p.counts, 'sm')}</a>`).join('') + '<a href="/" style="justify-content:center;color:var(--accent)">All projects</a>';
    } catch (e) { menu.innerHTML = `<div class="hist-cap" style="padding:8px">${esc(e.message)}</div>`; }
  }

  function visibleRowIds() { return $$('#features .row').map((el) => el.dataset.sid); }

  function bindGlobal() {
    document.addEventListener('click', (e) => {
      const t = e.target;
      const menu = $('#pmenu');
      if (menu && !menu.hidden && !t.closest('.switcher')) menu.hidden = true;

      const act = t.closest('[data-act]')?.dataset.act;
      if (act) {
        e.preventDefault();
        const s = cur();
        switch (act) {
          case 'run-all': return runScope('all');
          case 'run-failing': return runScope('failing');
          case 'run-one': return s && runScope('ids', [s.id]);
          case 'stop': return api(`/api/p/${enc(S.slug)}/stop`, {}).then(() => toast('Stopping…')).catch((x) => toast(esc(x.message), { bad: true }));
          case 'projects': return openProjects();
          case 'latest': S.viewRun = null; setHash(); S.graph.key = null; return refresh();
          case 'close': return closeDrawer();
          case 'edit': return startEdit();
          case 'toggle-source': return toggleSource();
          case 'edit-cancel':
            if (editDirty() && !confirm('Discard your unsaved edits?')) return;
            S.edit = null; return renderDrawer();
          case 'edit-preview': return previewEdit();
          case 'edit-back': S.edit.diff = null; return renderEditor();
          case 'edit-save': return saveEdit(false);
          case 'edit-save-run': return saveEdit(true);
          case 'clear-filter': S.filter = { text: '', status: null, ids: null, idsLabel: '' }; if ($('#q')) $('#q').value = ''; renderSummary(); return updateTabBody();
          case 'toggle-all': {
            const all = S.board.groups.map((g) => g.id);
            S.collapsed = S.collapsed.size >= all.length ? new Set() : new Set(all);
            localStorage.setItem(`specdeck:collapsed:${S.slug}`, JSON.stringify([...S.collapsed]));
            return renderFeatures();
          }
          case 'all-goals': S.showAllGoals = true; return renderSide();
          case 'zoom-in': { const w = $('#gwrap'); return zoomAt(1.25, w.clientWidth / 2, w.clientHeight / 2); }
          case 'zoom-out': { const w = $('#gwrap'); return zoomAt(0.8, w.clientWidth / 2, w.clientHeight / 2); }
          case 'fit': return fitGraph();
          default: return undefined;
        }
      }
      const copyEl = t.closest('[data-copy]');
      if (copyEl) return copy(copyEl.dataset.copy);
      const unchip = t.closest('[data-unchip]');
      if (unchip) {
        const [field, i] = unchip.dataset.unchip.split(':');
        S.edit.v[field].splice(Number(i), 1);
        return rerenderChips(field);
      }
      const tab = t.closest('[data-tab]');
      if (tab) {
        S.tab = tab.dataset.tab; setHash();
        if (S.tab === 'history') api(`/api/p/${enc(S.slug)}/runs`).then((r) => { S.runs = r.runs; renderHistory(); }).catch(() => {});
        return render();
      }
      const fs = t.closest('[data-filter-status]');
      if (fs) {
        const k = fs.dataset.filterStatus;
        S.filter.status = S.filter.status === k ? null : k;
        S.filter.ids = null; S.filter.idsLabel = '';
        if (S.tab === 'history') { S.tab = 'overview'; setHash(); }
        renderSummary();
        return S.renderedTab !== S.tab ? renderTabBody() : updateTabBody();
      }
      const dl = t.closest('[data-delta]');
      if (dl) {
        const k = dl.dataset.delta;
        if (S.filter.idsLabel === k) { S.filter.ids = null; S.filter.idsLabel = ''; }
        else { S.filter.ids = new Set(S.board.delta[k] || []); S.filter.idsLabel = k; S.filter.status = null; }
        if (S.tab === 'history') { S.tab = 'overview'; setHash(); }
        renderSummary();
        return S.renderedTab !== S.tab ? renderTabBody() : updateTabBody();
      }
      const gg = t.closest('[data-ggroup]');
      if (gg) { S.graph.group = S.graph.group === gg.dataset.ggroup ? null : gg.dataset.ggroup; return updateGraph(); }
      const runLink = t.closest('[data-run], [data-run-link]');
      if (runLink && !t.closest('.gn')) {
        e.preventDefault();
        const id = Number(runLink.dataset.run || runLink.dataset.runLink);
        S.viewRun = S.board.last_run && id === S.board.last_run.id && !S.viewRun ? null : id;
        if (runLink.closest('.runs-table')) S.tab = 'overview';
        setHash(); S.graph.key = null;
        return refresh();
      }
      const toggle = t.closest('[data-toggle-group]');
      if (toggle) {
        const g = toggle.dataset.toggleGroup;
        if (S.collapsed.has(g)) S.collapsed.delete(g); else S.collapsed.add(g);
        localStorage.setItem(`specdeck:collapsed:${S.slug}`, JSON.stringify([...S.collapsed]));
        return toggle.closest('.group').classList.toggle('collapsed');
      }
      const link = t.closest('[data-sid-link]');
      if (link) { e.preventDefault(); openScenario(link.dataset.sidLink); if (S.tab === 'graph') centerOn(link.dataset.sidLink); return undefined; }
      const row = t.closest('[data-sid]');
      if (row && !row.classList.contains('gn')) return openScenario(row.dataset.sid);
      return undefined;
    });

    document.addEventListener('input', (e) => {
      if (!S.edit) return;
      const t = e.target;
      if (t.dataset.f) S.edit.v[t.dataset.f] = t.value;
      if (t.dataset.step !== undefined) S.edit.v.steps[Number(t.dataset.step)][t.dataset.k] = t.value;
    });

    document.addEventListener('keydown', (e) => {
      const t = e.target;
      if (t.dataset && t.dataset.chipinput) {
        const field = t.dataset.chipinput;
        if ((e.key === 'Enter' || e.key === ',') && t.value.trim()) {
          e.preventDefault();
          const v = t.value.trim().replace(/,$/, '');
          if (!S.edit.v[field].includes(v)) S.edit.v[field].push(v);
          return rerenderChips(field);
        }
        if (e.key === 'Backspace' && !t.value && S.edit.v[field].length) {
          S.edit.v[field].pop();
          return rerenderChips(field);
        }
        return undefined;
      }
      if (e.key === 'Escape') {
        const menu = $('#pmenu');
        if (menu && !menu.hidden) { menu.hidden = true; return undefined; }
        if (S.edit) { if (editDirty() && !confirm('Discard your unsaved edits?')) return undefined; S.edit = null; return renderDrawer(); }
        if (/INPUT|TEXTAREA/.test(t.tagName) && t.value) { t.value = ''; t.dispatchEvent(new Event('input')); return undefined; }
        if (S.sel) return closeDrawer();
        return undefined;
      }
      if (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return undefined;
      if (e.key === '/') { e.preventDefault(); ($('#q') || $('#gq'))?.focus(); return undefined; }
      if (['1', '2', '3'].includes(e.key)) { S.tab = ['overview', 'graph', 'history'][Number(e.key) - 1]; setHash(); return render(); }
      if ((e.key === 'j' || e.key === 'k') && S.tab === 'overview') {
        const ids = visibleRowIds();
        if (!ids.length) return undefined;
        const i = ids.indexOf(S.sel);
        const next = e.key === 'j' ? (i < 0 ? 0 : Math.min(ids.length - 1, i + 1)) : (i < 0 ? 0 : Math.max(0, i - 1));
        return openScenario(ids[next]);
      }
      return undefined;
    });
  }

  // ------------------------------------------------------------------ projects page
  async function projectsPage() {
    $('#app').innerHTML = `
      <header class="top"><a class="brand" href="/"><span class="logo"></span>specdeck</a></header>
      <main class="projects-page" id="pmain"></main>
      <div class="toasts" id="toasts"></div><div class="tip" id="tip"></div>`;
    initTips();
    const draw = async () => {
      let projects = [];
      try { projects = (await api('/api/projects')).projects; } catch (e) { $('#pmain').innerHTML = `<div class="card empty"><b>${esc(e.message)}</b></div>`; return; }
      const cards = projects.map((p) => {
        const c = p.counts;
        const lr = p.last_run;
        const state = p.active_run
          ? `<span class="pill running"><span class="ic ic-running"></span>Running ${p.active_run.done}/${p.active_run.total}</span>`
          : failingCount(c) ? `<span class="pill failed">${icon('failed')}${failingCount(c)} failing</span>`
          : c.total && c.passed === c.total ? `<span class="pill passed">${icon('passed')}All passing</span>`
          : `<span class="pill notrun">${icon('notrun')}${c.total - c.passed} to go</span>`;
        return `<a class="card pcard" href="/p/${enc(p.slug)}">
          <div class="p-head"><div style="min-width:0;flex:1"><div class="p-name">${esc(p.name)}</div><div class="p-root">${esc(p.root)}</div></div>${state}</div>
          <div class="p-desc">${esc(p.description || '')}</div>
          ${segbar(c)}
          <div class="p-foot"><span><b>${c.passed}</b> / ${c.total} passing</span><span>${lr ? `last run ${relTime(lr.finished || lr.started)}` : 'no finished runs'}</span></div></a>`;
      }).join('');
      $('#pmain').innerHTML = `
        <h1>Projects</h1>
        <div class="muted">Every project that has recorded a test run. Each gets its own page.</div>
        ${projects.length ? `<div class="pgrid">${cards}</div>` : ''}
        <div class="card howto"><b>${projects.length ? 'Add another project' : 'Record your first run'}</b>
          <div class="muted" style="margin-top:4px">In any project that uses specdeck, run the tests with the live flag. The run appears here as it happens.</div>
          <pre>pytest --specdeck-live</pre>
          <div class="muted" style="margin-top:10px">To record every run without the flag, add <code>specdeck_live = true</code> under <code>[tool.pytest.ini_options]</code>.</div></div>`;
    };
    await draw();
    setInterval(draw, 3000);
  }

  boot();
})();
