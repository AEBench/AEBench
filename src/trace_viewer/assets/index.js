// UI adapted from the PostTrainBench trace viewer:
// https://github.com/aisa-group/posttrainbench-website/tree/main/traces

const DATA_BASE = (typeof window !== 'undefined' && window.AEBENCH_TRACE_DATA_BASE) || './data/';
const CATALOG = window.AEBENCH_TraceCatalog;
const { prettyAgent, prettyAgentForRun, prettyCase, prettyHarness, prettyModel } = CATALOG;
const legacyRunId = new URLSearchParams(window.location.search).get('run');
if (legacyRunId) {
  const destination = new URL('run.html', window.location.href);
  destination.searchParams.set('id', legacyRunId);
  window.location.replace(destination);
}

const els = {
  q: document.getElementById('q'),
  promptProfileFilter: document.getElementById('prompt-profile-filter'),
  caseFilter: document.getElementById('case-filter'),
  modelFilter: document.getElementById('model-filter'),
  agentFilter: document.getElementById('agent-filter'),
  groupBy: document.getElementById('group-by'),
  sort: document.getElementById('sort'),
  runs: document.getElementById('runs'),
  empty: document.getElementById('empty'),
  loading: document.getElementById('loading'),
  resultCount: document.getElementById('result-count'),
  resetFilters: document.getElementById('reset-filters'),
  emptyReset: document.getElementById('empty-reset'),
  heroStats: document.getElementById('hero-stats'),
  matrix: document.getElementById('matrix'),
  matrixLegend: document.getElementById('matrix-legend'),
};

let DATA = { runs: [], prompt_profiles: [], cases: [], build_ts: null };
const DATA_REQUEST_TIMEOUT_MS = 30000;
const TABLE_PAGE_SIZE = 50;
let OPEN_GROUP_KEY = '';
let APPLYING_URL_STATE = false;

const CASE_ORDER = CATALOG.CASE_ORDER;
const MODEL_ORDER = CATALOG.MODEL_ORDER;

async function load() {
  try {
    DATA = await fetchJsonWithTimeout(`${DATA_BASE}index.json`);
    if (!DATA || !Array.isArray(DATA.runs)) {
      throw new Error('The trace index has an invalid format.');
    }

    populateFilters();
    applyUrlState();
    renderHeroStats();
    renderMatrix();
    els.loading.classList.add('hidden');
    render();
  } catch (error) {
    console.error('Failed to load the trace index:', error);
    const detail = error.status
      ? `The data server returned HTTP ${error.status}.`
      : error.code === 'ETIMEDOUT'
        ? 'The data request timed out.'
        : 'Check your connection and try again.';
    showFatal(`Could not load the trace index. ${detail}`);
  }
}

async function fetchJsonWithTimeout(url, timeoutMs = DATA_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!resp.ok) {
      const error = new Error(`HTTP ${resp.status}`);
      error.status = resp.status;
      throw error;
    }
    return await resp.json();
  } catch (error) {
    if (error.name === 'AbortError') {
      const timeoutError = new Error(`Request timed out after ${timeoutMs}ms`);
      timeoutError.code = 'ETIMEDOUT';
      throw timeoutError;
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

function showFatal(message) {
  els.loading.classList.add('hidden');
  els.empty.classList.add('hidden');

  const box = document.createElement('div');
  box.className = 'empty-state';
  const text = document.createElement('p');
  text.className = 'muted';
  text.textContent = message;
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'btn btn-secondary';
  retry.textContent = 'Retry';
  retry.addEventListener('click', () => window.location.reload());
  box.append(text, retry);
  els.runs.replaceChildren(box);
}


function renderHeroStats() {
  const nRuns = DATA.runs.length;
  const cases = new Set(DATA.runs.map(r => r.case_id).filter(Boolean));
  const models = new Set(DATA.runs.map(r => r.model).filter(Boolean));
  const agents = new Set(DATA.runs.map(r => r.agent_kind).filter(Boolean));
  const updated = relTime(DATA.build_ts);

  const tiles = [
    { n: nRuns.toLocaleString(), label: `run${nRuns === 1 ? '' : 's'}`, primary: true },
    { n: cases.size,             label: `case${cases.size === 1 ? '' : 's'}` },
    { n: models.size,            label: `model${models.size === 1 ? '' : 's'}` },
    { n: agents.size,            label: `harness${agents.size === 1 ? '' : 'es'}` },
  ];
  let html = tiles.map(t => `
    <div class="stat-tile${t.primary ? ' stat-tile-primary' : ''}">
      <span class="stat-num">${escapeHtml(String(t.n))}</span>
      <span class="stat-label">${escapeHtml(t.label)}</span>
    </div>`).join('');
  if (updated) {
    html += `<div class="stat-meta">updated ${escapeHtml(updated)}</div>`;
  }
  els.heroStats.innerHTML = html;
}

function relTime(ts) {
  if (!ts) return '';
  const t = typeof ts === 'number' ? ts * (ts < 1e12 ? 1000 : 1) : Date.parse(ts);
  if (!isFinite(t)) return '';
  const dt = (Date.now() - t) / 1000;
  if (dt < 60) return 'just now';
  if (dt < 3600) return `${Math.round(dt / 60)}m ago`;
  if (dt < 86400) return `${Math.round(dt / 3600)}h ago`;
  if (dt < 86400 * 30) return `${Math.round(dt / 86400)}d ago`;
  if (dt < 86400 * 365) return `${Math.round(dt / (86400 * 30))}mo ago`;
  return `${Math.round(dt / (86400 * 365))}y ago`;
}


function renderMatrix() {
  const rows = uniqValuesOrdered(DATA.runs, 'case_id', CASE_ORDER);
  const cols = uniqValuesOrdered(DATA.runs, 'model', MODEL_ORDER);
  if (!rows.length || !cols.length) {
    els.matrix.innerHTML = '';
    return;
  }

  const cell = new Map();
  for (const r of DATA.runs) {
    if (!r.case_id || !r.model) continue;
    const key = `${r.case_id}|${r.model}`;
    let c = cell.get(key);
    if (!c) { c = { count: 0, bestScoreRatio: null, bestRun: null }; cell.set(key, c); }
    c.count += 1;
    if (r.score_ratio != null && (c.bestScoreRatio == null || r.score_ratio > c.bestScoreRatio)) {
      c.bestScoreRatio = r.score_ratio;
      c.bestRun = r;
    }
  }

  const rowMax = new Map();
  for (const b of rows) {
    let m = 0;
    for (const tm of cols) {
      const c = cell.get(`${b}|${tm}`);
      if (c && c.bestScoreRatio != null) m = Math.max(m, c.bestScoreRatio);
    }
    rowMax.set(b, m);
  }

  const grid = document.createElement('div');
  grid.className = 'matrix-grid';
  grid.style.gridTemplateColumns = `auto repeat(${cols.length}, minmax(0, 1fr))`;

  const corner = document.createElement('div');
  corner.className = 'matrix-corner';
  grid.appendChild(corner);

  for (const tm of cols) {
    const h = document.createElement('div');
    h.className = 'matrix-colhead';
    h.textContent = prettyModel(tm);
    h.title = tm;
    grid.appendChild(h);
  }

  for (const b of rows) {
    const rh = document.createElement('div');
    rh.className = 'matrix-rowhead';
    const caseLabel = prettyCase(b);
    rh.textContent = caseLabel;
    rh.title = caseLabel;
    grid.appendChild(rh);

    const max = rowMax.get(b) || 0;
    for (const tm of cols) {
      const c = cell.get(`${b}|${tm}`);
      const cellEl = document.createElement('button');
      cellEl.type = 'button';
      cellEl.className = 'matrix-cell';
      if (!c) {
        cellEl.classList.add('matrix-cell-empty');
        cellEl.innerHTML = `<span class="matrix-empty">-</span>`;
        cellEl.disabled = true;
        cellEl.setAttribute('aria-label', `${prettyCase(b)} · ${prettyModel(tm)}: no runs`);
      } else {
        const intensity = max > 0 && c.bestScoreRatio != null ? c.bestScoreRatio / max : 0;
        cellEl.style.setProperty('--cell-intensity', intensity.toFixed(3));
        const scoreLabel = c.bestRun
          ? `${c.bestRun.score ?? '-'}/${c.bestRun.expected_score ?? '-'}`
          : '-';
        cellEl.innerHTML = `<span class="matrix-score">${scoreLabel}</span>`;
        const tip = c.bestScoreRatio != null
          ? `best ${scoreLabel}${c.bestRun ? ' (' + prettyAgentForRun(c.bestRun) + ')' : ''}`
          : 'no oracle score';
        cellEl.setAttribute('data-tip', tip);
        cellEl.setAttribute('aria-label',
          `${prettyCase(b)} · ${prettyModel(tm)}: ${tip}`);
        cellEl.addEventListener('click', () => filterToCell(b, tm));
      }
      grid.appendChild(cellEl);
    }
  }

  els.matrix.innerHTML = '';
  els.matrix.appendChild(grid);

  els.matrixLegend.innerHTML = `
    <span class="legend-label">Darker = higher score within case</span>
    <span class="legend-scale" aria-hidden="true">
      <span class="legend-step" style="--cell-intensity:0.15"></span>
      <span class="legend-step" style="--cell-intensity:0.40"></span>
      <span class="legend-step" style="--cell-intensity:0.65"></span>
      <span class="legend-step" style="--cell-intensity:0.90"></span>
      <span class="legend-step" style="--cell-intensity:1"></span>
    </span>
    <span class="legend-label legend-label-right">low → high</span>`;
}

function filterToCell(caseId, model) {
  APPLYING_URL_STATE = true;
  els.caseFilter.value = caseId;
  els.modelFilter.value = model;
  els.caseFilter.dispatchEvent(new Event('change', { bubbles: true }));
  els.modelFilter.dispatchEvent(new Event('change', { bubbles: true }));
  APPLYING_URL_STATE = false;
  if (els.groupBy.value === 'case-model') OPEN_GROUP_KEY = `${caseId}|${model}`;
  else if (els.groupBy.value === 'case') OPEN_GROUP_KEY = caseId;
  else OPEN_GROUP_KEY = '';
  syncUrlState();
  render();
  document.querySelector('.filter-dock').scrollIntoView({ block: 'start' });
}

function uniqValuesSortedByCount(rows, key) {
  const counts = new Map();
  for (const r of rows) {
    const v = r[key];
    if (!v) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([v]) => v);
}

function uniqValuesOrdered(rows, key, orderList) {
  const seen = new Set();
  for (const r of rows) {
    const v = r[key];
    if (v) seen.add(v);
  }
  return [...seen].sort((a, b) => {
    const ai = CATALOG.orderIndex(orderList, a);
    const bi = CATALOG.orderIndex(orderList, b);
    if (ai !== bi) return ai - bi;
    return a.localeCompare(b);
  });
}


function populateFilters() {
  for (const profile of (DATA.prompt_profiles || [])) {
    addOpt(els.promptProfileFilter, profile, profile);
  }
  for (const caseId of (DATA.cases || [])) {
    addOpt(els.caseFilter, caseId, prettyCase(caseId));
  }
  const agents = [...new Set(DATA.runs.map(r => r.agent_kind).filter(Boolean))].sort();
  for (const a of agents) addOpt(els.agentFilter, a, prettyAgent(a));
  const models = [...new Set(DATA.runs.map(r => r.model).filter(Boolean))].sort();
  for (const m of models) addOpt(els.modelFilter, m, prettyModel(m));
}

function addOpt(select, value, label) {
  const opt = document.createElement('option');
  opt.value = value; opt.textContent = label;
  select.appendChild(opt);
}


function stateFromControls() {
  return {
    case: els.caseFilter.value,
    model: els.modelFilter.value,
    agent: els.agentFilter.value,
    prompt_profile: els.promptProfileFilter.value,
    q: els.q.value.trim(),
    group: els.groupBy.value,
    sort: els.sort.value,
    open: OPEN_GROUP_KEY,
  };
}

function applyUrlState() {
  const state = CATALOG.readState(window.location.search);
  APPLYING_URL_STATE = true;
  els.caseFilter.value = state.case;
  els.modelFilter.value = state.model;
  els.agentFilter.value = state.agent;
  els.promptProfileFilter.value = state.prompt_profile;
  els.q.value = state.q;
  els.groupBy.value = state.group;
  els.sort.value = state.sort;
  OPEN_GROUP_KEY = state.open;
  [els.caseFilter, els.modelFilter, els.agentFilter, els.promptProfileFilter, els.groupBy, els.sort]
    .forEach(select => select.dispatchEvent(new Event('change', { bubbles: true })));
  APPLYING_URL_STATE = false;
}

function syncUrlState() {
  const next = CATALOG.writeState(new URL(window.location.href), stateFromControls());
  history.replaceState(null, '', next);
}

function handleControlInput() {
  if (APPLYING_URL_STATE) return;
  OPEN_GROUP_KEY = '';
  syncUrlState();
  render();
}

function filterRows() {
  return CATALOG.filterRuns(DATA.runs, stateFromControls());
}

function render() {
  let rows = filterRows();
  const total = DATA.runs.length;
  const filtered = rows.length;
  const anyFilter = els.q.value || els.promptProfileFilter.value || els.caseFilter.value
                    || els.modelFilter.value || els.agentFilter.value;
  els.resultCount.textContent = anyFilter
    ? `${filtered.toLocaleString()} of ${total.toLocaleString()} runs`
    : '';
  els.resultCount.classList.toggle('hidden', !anyFilter);
  els.resetFilters.classList.toggle('hidden', !anyFilter);

  if (rows.length === 0) {
    els.runs.innerHTML = '';
    els.empty.classList.remove('hidden');
    return;
  }
  els.empty.classList.add('hidden');

  rows.sort(CATALOG.sorter(els.sort.value));

  const scoreRatioMax = Math.max(0.01, ...DATA.runs.map(r => r.score_ratio ?? 0));

  const groupMode = els.groupBy.value;
  els.runs.innerHTML = '';
  if (groupMode === 'none') {
    els.runs.appendChild(buildPagedTable(rows, scoreRatioMax, groupMode, ''));
    return;
  }

  const groups = CATALOG.buildGroups(rows, groupMode);
  const requestedGroupExists = groups.some(group => group.key === OPEN_GROUP_KEY);
  const effectiveOpenKey = requestedGroupExists
    ? OPEN_GROUP_KEY
    : (groups.length === 1 ? groups[0].key : '');
  for (const g of groups) {
    els.runs.appendChild(buildGroupDisclosure(g, groupMode, scoreRatioMax, g.key === effectiveOpenKey));
  }
}


function buildGroupDisclosure(group, mode, scoreRatioMax, initiallyOpen) {
  const details = document.createElement('details');
  details.className = 'run-group';
  details.dataset.groupKey = group.key;
  details.open = initiallyOpen;
  details.appendChild(buildGroupHeader(group, mode));

  const body = document.createElement('div');
  body.className = 'run-group-body';
  details.appendChild(body);
  let materialized = false;
  const materialize = () => {
    if (materialized) return;
    materialized = true;
    body.appendChild(buildPagedTable(group.rows, scoreRatioMax, mode, group.key));
  };
  if (initiallyOpen) materialize();

  details.addEventListener('toggle', () => {
    if (details.open) {
      els.runs.querySelectorAll('.run-group[open]').forEach(other => {
        if (other !== details) other.open = false;
      });
      OPEN_GROUP_KEY = group.key;
      materialize();
    } else if (OPEN_GROUP_KEY === group.key) {
      OPEN_GROUP_KEY = '';
    }
    syncUrlState();
  });
  return details;
}

function buildPagedTable(rows, scoreRatioMax, mode, groupKey) {
  const wrap = document.createElement('div');
  wrap.className = 'paged-table';
  let visible = Math.min(TABLE_PAGE_SIZE, rows.length);

  const renderPage = () => {
    wrap.replaceChildren(buildTable(rows.slice(0, visible), scoreRatioMax, mode, groupKey));
    if (visible >= rows.length) return;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'load-more-btn';
    const remaining = rows.length - visible;
    more.textContent = `Show ${Math.min(TABLE_PAGE_SIZE, remaining)} more`;
    more.addEventListener('click', () => {
      visible = Math.min(rows.length, visible + TABLE_PAGE_SIZE);
      renderPage();
    });
    wrap.appendChild(more);
  };
  renderPage();
  return wrap;
}

function buildGroupHeader(g, mode) {
  const head = document.createElement('summary');
  head.className = 'run-group-head';

  let title = '';
  if (mode === 'case-model') {
    const [b, m] = g.key.split('|');
    title = `<span class="run-group-name">${escapeHtml(prettyCase(b))}</span>
             <span class="run-group-name-separator">·</span>
             <span class="run-group-name run-group-model">${escapeHtml(prettyModel(m))}</span>`;
  } else if (mode === 'case') {
    title = `<span class="run-group-name">${escapeHtml(prettyCase(g.key))}</span>`;
  } else {
    title = `<span class="run-group-name">${escapeHtml(g.key)}</span>`;
  }

  let bestRun = null;
  const agents = new Set();
  const models = new Set();
  for (const r of g.rows) {
    agents.add(r.agent_kind);
    if (r.model) models.add(r.model);
    if (r.score_ratio != null && (!bestRun || r.score_ratio > bestRun.score_ratio)) bestRun = r;
  }
  const parts = [];
  if (mode === 'case') {
    parts.push(`${models.size} model${models.size === 1 ? '' : 's'}`);
  }
  parts.push(`${g.rows.length} run${g.rows.length === 1 ? '' : 's'}`);
  if (bestRun) {
    const agent = bestRun.agent_kind ? ` (${prettyAgentForRun(bestRun)})` : '';
    parts.push(`best ${bestRun.score ?? '-'}/${bestRun.expected_score ?? '-'}${agent}`);
  }
  if (mode === 'prompt-profile' && agents.size > 1) {
    parts.push(`${agents.size} agents`);
  }

  head.innerHTML = `
    <div class="run-group-head-title">${title}</div>
    <div class="run-group-head-meta">${escapeHtml(parts.join(' · '))}</div>
    <span class="run-group-head-caret" aria-hidden="true">›</span>`;
  return head;
}

function buildTable(rows, scoreRatioMax, mode, groupKey) {
  const t = document.createElement('table');
  t.className = 'runtable';
  const firstHeader = mode === 'case-model'
    ? 'run'
    : mode === 'case' ? 'model' : 'case';
  t.innerHTML = `
    <thead><tr>
      <th class="col-case">${firstHeader}</th>
      <th class="col-agent">harness</th>
      <th class="col-score">oracle</th>
      <th class="col-num">duration</th>
      <th class="col-num">turns</th>
      <th class="col-num">reported cost</th>
      <th class="col-num">runtime</th>
      <th class="col-verdict">status</th>
    </tr></thead>
    <tbody></tbody>`;
  const tbody = t.querySelector('tbody');
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.tabIndex = 0;
    const href = runHref(r.run_id, groupKey);
    tr.addEventListener('click', event => {
      if (event.target.closest('a, button')) return;
      navigateRun(href);
    });
    tr.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigateRun(href); }
    });
    tr.innerHTML = `
      <td class="col-case">${runIdentityCell(r, mode, href)}</td>
      <td class="col-agent">${agentCell(r)}</td>
      <td class="col-score">${scoreCell(r, scoreRatioMax)}</td>
      <td class="col-num">${durationCell(r)}</td>
      <td class="col-num">${(r.num_turns != null && r.num_turns > 0) ? r.num_turns.toLocaleString() : '<span class="muted">-</span>'}</td>
      <td class="col-num">${costCell(r.total_cost_usd)}</td>
      <td class="col-num">${escapeHtml(r.runtime || '-')}</td>
      <td class="col-verdict">${statusBadge(r)}</td>`;
    tbody.appendChild(tr);
  }
  const scroller = document.createElement('div');
  scroller.className = 'runtable-scroll';
  scroller.appendChild(t);
  return scroller;
}

function runHref(id, groupKey) {
  const returnUrl = CATALOG.writeState(new URL(window.location.href), {
    ...stateFromControls(),
    open: groupKey || OPEN_GROUP_KEY,
  });
  returnUrl.hash = '';
  const destination = new URL('run.html', window.location.href);
  destination.searchParams.set('id', id);
  destination.searchParams.set('return', `${returnUrl.pathname}${returnUrl.search}`);
  return destination.href;
}

function navigateRun(href) {
  window.location.href = href;
}


function runIdentityCell(r, mode, href) {
  const caseId = prettyCase(r.case_id) || '?';
  const model = prettyModel(r.model) || '?';
  let primary = caseId;
  let secondary = model;
  if (mode === 'case-model') {
    primary = 'open run';
    secondary = '';
  } else if (mode === 'case') {
    primary = model;
    secondary = '';
  }
  return `<div class="case-cell">
    <div class="case-primary"><a class="run-primary-link" href="${escapeHtml(href)}">${escapeHtml(primary)}</a></div>
    ${secondary ? `<div class="case-secondary">${escapeHtml(secondary)}</div>` : ''}
  </div>`;
}

function agentCell(r) {
  if (!r.agent_kind) return '<span class="muted">-</span>';
  const pretty = prettyAgentForRun(r);
  const m = /^(.*?)\s+\(([^)]+)\)\s*$/.exec(pretty);
  const nameHtml = m
    ? `<span class="agent-name">${escapeHtml(m[1])}</span> <span class="agent-tag">${escapeHtml(m[2])}</span>`
    : `<span class="agent-name">${escapeHtml(pretty)}</span>`;
  const harness = prettyHarness(r.trace_format);
  const harnessHtml = harness
    ? `<span class="agent-harness">${escapeHtml(harness)}</span>`
    : '';
  const effort = CATALOG.prettyReasoningEffort(r.reasoning_effort);
  const effortHtml = effort
    ? `<span class="agent-tag">${escapeHtml(effort)}</span>`
    : '';
  return `${nameHtml}${effortHtml}${harnessHtml}`;
}

const NO_EVAL_TITLE = 'No oracle score is available for this run.';

function scoreCell(r, scoreRatioMax) {
  if (r.score_ratio == null) {
    return `<span class="no-eval-marker" data-tip="${NO_EVAL_TITLE}">not evaluated</span>`;
  }
  const w = Math.max(2, (r.score_ratio / scoreRatioMax) * 100);
  return `<div class="score-cell">
    <div class="score-bar-small" aria-hidden="true"><div class="score-fill" style="width:${w.toFixed(1)}%"></div></div>
    <span class="score-num">${escapeHtml(`${r.score ?? '-'}/${r.expected_score ?? '-'}`)}</span>
  </div>`;
}

function durationCell(r) {
  if (r.duration_ms) {
    const s = Math.floor(r.duration_ms / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m`;
    return `${s}s`;
  }
  return '<span class="muted">-</span>';
}

function costCell(cost) {
  if (cost == null) {
    return '<span class="muted cost-missing" data-tip="Cost was not reported by the agent harness.">-</span>';
  }
  return `<span data-tip="Cost estimate reported by the agent CLI.">$${Number(cost).toFixed(2)}</span>`;
}

const WARN_SVG = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>`;
const CHECK_SVG = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`;

function statusBadge(run) {
  if (run.status === 'success') {
    return `<span class="vbadge vbadge-ok" aria-label="success">${CHECK_SVG}</span>`;
  }
  if (run.status) {
    return `<span class="vbadge vbadge-flag">${WARN_SVG}<span>${escapeHtml(run.status)}</span></span>`;
  }
  return '<span class="vbadge vbadge-pending">-</span>';
}


function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function clearFilters() {
  APPLYING_URL_STATE = true;
  els.q.value = '';
  els.promptProfileFilter.value = '';
  els.caseFilter.value = '';
  els.modelFilter.value = '';
  els.agentFilter.value = '';
  [els.promptProfileFilter, els.caseFilter, els.modelFilter, els.agentFilter]
    .forEach(s => s.dispatchEvent(new Event('change', { bubbles: true })));
  APPLYING_URL_STATE = false;
  OPEN_GROUP_KEY = '';
  syncUrlState();
  render();
}

[els.q, els.promptProfileFilter, els.caseFilter, els.modelFilter,
 els.agentFilter, els.groupBy, els.sort]
  .forEach(el => el.addEventListener('input', handleControlInput));
els.resetFilters.addEventListener('click', clearFilters);
els.emptyReset.addEventListener('click', clearFilters);
window.addEventListener('popstate', () => {
  applyUrlState();
  render();
});


function makeCustomSelect(selectEl) {
  if (!selectEl) return;
  const wrap = document.createElement('div');
  wrap.className = 'cs';
  selectEl.parentNode.insertBefore(wrap, selectEl);
  wrap.appendChild(selectEl);
  selectEl.classList.add('cs-native');

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'cs-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.innerHTML = `
    <span class="cs-value"></span>
    <svg class="cs-caret" width="10" height="6" viewBox="0 0 12 8" fill="none" aria-hidden="true">
      <path d="M1 1.5L6 6.5L11 1.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  const menu = document.createElement('ul');
  menu.className = 'cs-menu';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-hidden', 'true');
  wrap.appendChild(trigger);
  wrap.appendChild(menu);

  const valueEl = trigger.querySelector('.cs-value');
  const syncTrigger = () => {
    const opt = selectEl.selectedOptions[0];
    valueEl.textContent = opt ? opt.textContent : '';
    const isActive = !!selectEl.value && selectEl.value !== '';
    wrap.classList.toggle('cs-active', isActive);
  };
  const rebuildMenu = () => {
    menu.innerHTML = '';
    for (const opt of selectEl.options) {
      const li = document.createElement('li');
      li.className = 'cs-option' + (opt.value === selectEl.value ? ' active' : '');
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', opt.value === selectEl.value ? 'true' : 'false');
      li.dataset.value = opt.value;
      li.tabIndex = -1;
      li.textContent = opt.textContent;
      menu.appendChild(li);
    }
  };
  const setInstant = instant => {
    if (!instant) return;
    wrap.classList.add('cs-no-motion');
    requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.remove('cs-no-motion')));
  };
  const open = ({ instant = false } = {}) => {
    setInstant(instant);
    rebuildMenu();
    wrap.classList.add('cs-open');
    trigger.setAttribute('aria-expanded', 'true');
    menu.setAttribute('aria-hidden', 'false');
    document.addEventListener('mousedown', onOutside);
    document.addEventListener('keydown', onKey);
  };
  const close = ({ instant = false } = {}) => {
    setInstant(instant);
    wrap.classList.remove('cs-open');
    trigger.setAttribute('aria-expanded', 'false');
    menu.setAttribute('aria-hidden', 'true');
    document.removeEventListener('mousedown', onOutside);
    document.removeEventListener('keydown', onKey);
    trigger.focus();
  };
  const onOutside = (e) => { if (!wrap.contains(e.target)) close(); };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close({ instant: true }); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const items = [...menu.querySelectorAll('.cs-option')];
      if (!items.length) return;
      const cur = items.findIndex(li => li.classList.contains('focused'));
      const next = e.key === 'ArrowDown'
        ? Math.min(items.length - 1, cur + 1)
        : Math.max(0, cur === -1 ? items.length - 1 : cur - 1);
      items.forEach(li => li.classList.remove('focused'));
      items[next].classList.add('focused');
      items[next].scrollIntoView({ block: 'nearest' });
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const focused = menu.querySelector('.cs-option.focused') || menu.querySelector('.cs-option.active');
      if (focused) selectValue(focused.dataset.value, { instant: true });
    }
  };
  const selectValue = (v, { instant = false } = {}) => {
    if (selectEl.value === v) { close({ instant }); return; }
    selectEl.value = v;
    selectEl.dispatchEvent(new Event('input', { bubbles: true }));
    selectEl.dispatchEvent(new Event('change', { bubbles: true }));
    syncTrigger();
    close({ instant });
  };

  trigger.addEventListener('click', event => {
    const options = { instant: event.detail === 0 };
    if (wrap.classList.contains('cs-open')) close(options);
    else open(options);
  });
  menu.addEventListener('click', e => {
    const li = e.target.closest('.cs-option');
    if (li) selectValue(li.dataset.value);
  });

  new MutationObserver(syncTrigger).observe(selectEl, { childList: true });
  selectEl.addEventListener('change', syncTrigger);

  syncTrigger();
}

[els.caseFilter, els.modelFilter, els.agentFilter, els.promptProfileFilter,
 els.groupBy, els.sort].forEach(makeCustomSelect);

load();
