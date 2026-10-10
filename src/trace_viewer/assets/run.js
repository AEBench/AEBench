// UI adapted from the PostTrainBench trace viewer:
// https://github.com/aisa-group/posttrainbench-website/tree/main/traces

const params = new URLSearchParams(window.location.search);
const RUN_ID = params.get('id');
const CATALOG = window.AEBENCH_TraceCatalog;
const { prettyAgent, prettyCase, prettyHarness, prettyModel } = CATALOG;
const DATA_BASE = (typeof window !== 'undefined' && window.AEBENCH_TRACE_DATA_BASE) || './data/';

const els = {
  topbarMeta: document.getElementById('topbar-meta'),
  tabNav: document.getElementById('tab-nav'),
  backLink: document.getElementById('back-link'),
  runNeighbors: document.getElementById('run-neighbors'),
  prevRun: document.getElementById('prev-run'),
  nextRun: document.getElementById('next-run'),

  summaryTitle: document.getElementById('summary-title'),
  summarySub: document.getElementById('summary-sub'),
  runIdText: document.getElementById('run-id-text'),
  runIdBox: document.getElementById('run-id-box'),
  copyIdBtn: document.getElementById('copy-id-btn'),
  copyFeedback: document.getElementById('copy-feedback'),
  scoreBig: document.getElementById('score-big'),
  scoreSub: document.getElementById('score-sub'),
  summaryStats: document.getElementById('summary-stats'),
  summaryQuick: document.getElementById('summary-quick'),
  summaryDetails: document.getElementById('summary-details'),
  summaryDetailsToggle: document.getElementById('summary-details-toggle'),
  linkRaw: document.getElementById('link-raw'),

  oracleBrief: document.getElementById('oracle-brief'),

  trace: document.getElementById('trace'),
  oracle: document.getElementById('oracle'),
  promptContent: document.getElementById('prompt-content'),

  metricGridRail: document.getElementById('metric-grid-rail'),
  metricGridModal: document.getElementById('metric-grid-modal'),
  metricsModal: document.getElementById('metrics-modal'),
  showAllBtn: document.getElementById('show-all-metrics'),
  railEmpty: document.getElementById('rail-empty'),

  summaryTokensBlock: document.getElementById('summary-tokens-block'),
  summaryTokens: document.getElementById('summary-tokens'),

  eventCount: document.getElementById('event-count'),
  expandOutputs: document.getElementById('expand-outputs'),
  jumpTurn: document.getElementById('jump-turn'),
  traceViewFocus: document.getElementById('trace-view-focus'),
  traceViewAll: document.getElementById('trace-view-all'),
};

let RECORD = null;
let PROMPT_LOADED = false;
let RAIL_CHARTS = [];
let MODAL_CHARTS = [];
let TRACE_START_MS = null;
let TRACE_VIEW = params.get('view') === 'focus' ? 'focus' : 'all';
const DATA_REQUEST_TIMEOUT_MS = 30000;
const CHART_LOAD_TIMEOUT_MS = 8000;
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

async function load() {
  try {
    if (!RUN_ID) {
      els.trace.innerHTML = '<p class="muted">The URL has no run ID. Return to the run list.</p>';
      return;
    }
    RECORD = await fetchJsonWithTimeout(`${DATA_BASE}${encodeURIComponent(RUN_ID)}.json`);
    if (!RECORD || !RECORD.meta || !RECORD.summary || !RECORD.index_row || !Array.isArray(RECORD.events)) {
      throw new Error('The trace data has an invalid format.');
    }
    computeTraceStart();
    renderTopbar();
    renderSummary();
    setupRunContext();
    renderTrace();
    renderOracle();
    renderMiniCharts();
    renderMiniTokens();
    setupTabs();
    setupCopyId();
    setupSummaryDetails();
    setupTraceControls();
    setupMetricsModal();
  } catch (error) {
    console.error(`Failed to load trace ${RUN_ID || '(missing id)'}:`, error);
    showRunLoadError(error);
  } finally {
    hidePageLoading();
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

function showRunLoadError(error) {
  const message = error.status === 404
    ? `Run ${RUN_ID} was not found.`
    : error.code === 'ETIMEDOUT'
      ? 'The trace request timed out.'
      : 'Could not load this trace. Check your connection and try again.';

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
  els.trace.replaceChildren(box);
}

function hidePageLoading() {
  const loader = document.getElementById('page-loading');
  if (!loader) return;
  requestAnimationFrame(() => {
    loader.classList.add('hidden');
    setTimeout(() => loader.remove(), 350);
  });
}

function computeTraceStart() {
  for (const ev of RECORD.events) {
    if (ev.ts) {
      const t = parseTraceTs(ev.ts);
      if (!isNaN(t)) { TRACE_START_MS = t; return; }
    }
  }
  const samples = RECORD.system_monitor || [];
  for (const s of samples) {
    if (s.ts) {
      const t = parseTraceTs(s.ts);
      if (!isNaN(t)) { TRACE_START_MS = t; return; }
    }
  }
}

function whenChartReady(fn, deadline = Date.now() + CHART_LOAD_TIMEOUT_MS) {
  if (typeof Chart !== 'undefined') {
    try {
      fn();
    } catch (error) {
      console.error('Failed to render system metrics:', error);
      setMetricsUnavailable('System metrics could not be rendered.');
    }
    return;
  }
  if (Date.now() >= deadline) {
    console.error('Chart.js did not load before the metrics timeout.');
    setMetricsUnavailable('System metrics are unavailable because the chart library did not load.');
    return;
  }
  setTimeout(() => whenChartReady(fn, deadline), 80);
}


function renderTopbar() {
  els.topbarMeta.textContent = '';
}

function renderSummary() {
  const m = RECORD.meta;
  const s = RECORD.summary;
  const ix = RECORD.index_row;

  els.summaryTitle.textContent = prettyCase(m.case_id);
  const subBits = [];
  if (m.model) subBits.push(prettyModel(m.model));
  els.summarySub.textContent = subBits.join(' · ');

  els.runIdText.textContent = m.run_id;

  const scoreBarFill = document.getElementById('score-bar-fill');
  const scoreBar = document.getElementById('score-bar');
  const NO_EVAL_TITLE = 'No oracle score is available for this run.';
  if (ix.score_ratio != null) {
    els.scoreBig.textContent = `${ix.score ?? '-'}/${ix.expected_score ?? '-'}`;
    els.scoreBig.classList.add('oracle-score');
    els.scoreBig.classList.remove('score-big-empty');
    els.scoreSub.textContent = RECORD.oracle?.summary || 'Oracle score';
    if (scoreBar) scoreBar.style.display = '';
    if (scoreBarFill) {
      scoreBarFill.style.setProperty('--score-scale', String(Math.min(1, Math.max(0, ix.score_ratio))));
    }
  } else {
    els.scoreBig.textContent = '-';
    els.scoreBig.classList.add('score-big-empty');
    els.scoreSub.innerHTML =
      `<span class="no-eval-marker" data-tip="${escapeHtml(NO_EVAL_TITLE)}">not evaluated</span>`;
    if (scoreBar) scoreBar.style.display = 'none';
  }

  const modelPretty = escapeHtml(prettyAgent((s.models || [])[0]) || '-');
  const modelQuick = prettyAgent((s.models || [])[0]) || '-';
  const reasoningEffort = CATALOG.prettyReasoningEffort(m.reasoning_effort);
  const modelQuickLabel = reasoningEffort
    ? `${modelQuick} (${reasoningEffort})`
    : modelQuick;
  const harness = prettyHarness(m.trace_format);
  els.summaryQuick.textContent = `${modelQuickLabel} · ${humanDuration(ix.duration_ms)}`;

  const stats = [
    ['model',       modelPretty],
  ];
  if (harness) stats.push(['harness', escapeHtml(harness)]);
  if (reasoningEffort) stats.push(['effort', escapeHtml(reasoningEffort)]);
  stats.push(
    ['runtime',     escapeHtml(ix.runtime || '-')],
    ['duration',    escapeHtml(humanDuration(ix.duration_ms))],
    ['turns',       escapeHtml((ix.num_turns != null && ix.num_turns > 0) ? String(ix.num_turns) : '-')],
    ['status',      escapeHtml(ix.status || '-')],
  );
  els.summaryStats.innerHTML = stats.map(([k, v]) =>
    `<dt>${k}</dt><dd>${v}</dd>`).join('');

  const rawBase = (typeof window !== 'undefined' && window.AEBENCH_TRACE_RAW_BASE) || null;
  const externalIcon = `<svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;
  const downloadIcon = `<svg class="btn-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
  if (rawBase && m.prompt_profile && m.case_id) {
    els.linkRaw.href = `${rawBase.replace(/\/+$/, '')}/${encodeURIComponent(m.prompt_profile)}/${encodeURIComponent(m.case_id)}`;
    els.linkRaw.target = '_blank';
    els.linkRaw.rel = 'noopener';
    els.linkRaw.removeAttribute('download');
    els.linkRaw.innerHTML = `${externalIcon}<span>Browse run files</span>`;
  } else {
    els.linkRaw.href = `${DATA_BASE}${encodeURIComponent(RUN_ID)}.json`;
    els.linkRaw.setAttribute('download', `${RUN_ID}.json`);
    els.linkRaw.innerHTML = `${downloadIcon}<span>Download trace</span>`;
  }
}

function safeReturnUrl() {
  const value = params.get('return');
  if (!value) return null;
  try {
    const url = new URL(value, window.location.href);
    const landingPath = new URL('index.html', window.location.href).pathname;
    return url.origin === window.location.origin && url.pathname === landingPath ? url : null;
  } catch {
    return null;
  }
}

async function setupRunContext() {
  const returnUrl = safeReturnUrl();
  if (!returnUrl) return;
  els.backLink.href = returnUrl.href;
  els.backLink.lastChild.textContent = ' Back to results';

  try {
    const catalogData = await fetchJsonWithTimeout(`${DATA_BASE}index.json`);
    if (!catalogData || !Array.isArray(catalogData.runs)) return;
    const state = CATALOG.readState(returnUrl.search);
    const ordered = CATALOG.orderedRuns(catalogData.runs, state);
    const current = ordered.findIndex(run => run.run_id === RUN_ID);
    if (current < 0 || ordered.length < 2) return;

    const neighborHref = run => {
      const url = new URL(window.location.href);
      url.searchParams.set('id', run.run_id);
      url.hash = '';
      return url.href;
    };
    const previous = ordered[current - 1];
    const next = ordered[current + 1];
    els.prevRun.classList.toggle('hidden', !previous);
    els.nextRun.classList.toggle('hidden', !next);
    if (previous) els.prevRun.href = neighborHref(previous);
    if (next) els.nextRun.href = neighborHref(next);
    els.runNeighbors.classList.remove('hidden');
  } catch (error) {
    console.warn('Could not build adjacent-run navigation:', error);
  }
}

function setupSummaryDetails() {
  els.summaryDetailsToggle.addEventListener('click', () => {
    const open = !els.summaryDetails.classList.contains('mobile-open');
    els.summaryDetails.classList.toggle('mobile-open', open);
    els.summaryDetailsToggle.setAttribute('aria-expanded', String(open));
  });
}

function setupTraceControls() {
  els.expandOutputs.addEventListener('change', () => renderTrace({ preservePosition: true }));

  const setTraceView = view => {
    if (TRACE_VIEW === view) return;
    TRACE_VIEW = view;
    const url = new URL(window.location.href);
    if (view === 'all') url.searchParams.set('view', 'all');
    else url.searchParams.delete('view');
    history.replaceState(null, '', url);
    renderTrace({ preservePosition: true });
  };
  els.traceViewFocus.addEventListener('click', () => setTraceView('focus'));
  els.traceViewAll.addEventListener('click', () => setTraceView('all'));

  els.trace.addEventListener('click', e => {
    const head = e.target.closest('.tool-result-head');
    if (!head || !head.querySelector('.clip-more')) return;
    const card = head.closest('.tool-call');
    if (card) card.classList.toggle('expanded');
  });

  els.trace.addEventListener('click', e => {
    const head = e.target.closest('.tool-call-head');
    if (!head) return;
    const card = head.closest('.tool-call');
    if (!card || !card.querySelector('.tool-result')) return;
    const collapsed = card.classList.toggle('output-collapsed');
    head.setAttribute('aria-expanded', String(!collapsed));
  });

  const jump = () => {
    const n = parseInt(els.jumpTurn.value, 10);
    if (!Number.isFinite(n) || n < 1) return;
    const target = document.getElementById('turn-' + n);
    if (!target) {
      els.jumpTurn.classList.add('jump-miss');
      setTimeout(() => els.jumpTurn.classList.remove('jump-miss'), 600);
      return;
    }
    target.scrollIntoView({ block: 'center' });
    flashEvent(target);
  };
  els.jumpTurn.addEventListener('change', jump);
  els.jumpTurn.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); jump(); }
  });

  els.trace.addEventListener('click', e => {
    const marker = e.target.closest('.event-marker');
    if (!marker) return;
    const anchor = marker.dataset.anchor;
    if (!anchor) return;
    const url = new URL(window.location.href);
    url.searchParams.delete('tab');
    url.hash = anchor;
    history.replaceState(null, '', url.toString());
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(url.toString()).catch(() => {});
    marker.classList.add('linked');
    setTimeout(() => marker.classList.remove('linked'), 1200);
  });

  const initialAnchor = eventAnchorFromHash();
  if (initialAnchor) {
    setTimeout(() => {
      const target = document.getElementById(initialAnchor);
      if (target) { target.scrollIntoView({ block: 'center' }); flashEvent(target); }
    }, 50);
  }
}

function eventAnchorFromHash(hash = window.location.hash) {
  if (!hash || hash === '#') return null;
  let id;
  try {
    id = decodeURIComponent(hash.slice(1));
  } catch {
    return null;
  }
  return /^(?:turn-\d+|ev-[A-Za-z0-9_-]+)$/.test(id) ? id : null;
}

const eventFlashTimers = new WeakMap();
function flashEvent(el) {
  const currentTimer = eventFlashTimers.get(el);
  if (currentTimer) clearTimeout(currentTimer);
  el.classList.add('event-flash');
  const timer = setTimeout(() => {
    el.classList.remove('event-flash');
    eventFlashTimers.delete(el);
  }, 1200);
  eventFlashTimers.set(el, timer);
}

function setupCopyId() {
  let resetTimer = null;
  const copy = () => {
    const text = RECORD.meta.run_id;
    const finish = () => {
      if (resetTimer !== null) clearTimeout(resetTimer);
      els.runIdBox.classList.add('copied');
      resetTimer = setTimeout(() => {
        els.runIdBox.classList.remove('copied');
        resetTimer = null;
      }, 1100);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(finish).catch(finish);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch {}
      ta.remove();
      finish();
    }
  };
  els.runIdBox.addEventListener('click', copy);
  els.copyIdBtn.addEventListener('click', e => { e.stopPropagation(); copy(); });
}


function renderTrace({ preservePosition = false } = {}) {
  const anchor = preservePosition ? captureTraceAnchor() : null;
  const events = RECORD.events;
  const wantSys = TRACE_VIEW === 'all';
  const expandResults = els.expandOutputs.checked;

  const resultByUseId = new Map();
  for (const ev of events) {
    if (ev.type === 'user' && Array.isArray(ev.blocks)) {
      for (const b of ev.blocks) {
        if (b && b.type === 'tool_result' && b.tool_use_id) {
          resultByUseId.set(b.tool_use_id, { ev, block: b });
        }
      }
    }
  }
  const skipUserEv = new Set();
  for (const { ev } of resultByUseId.values()) {
    if (ev.blocks.every(b => b && b.type === 'tool_result' && resultByUseId.has(b.tool_use_id))) {
      skipUserEv.add(ev);
    }
  }

  let turnCounter = 0;
  const turnNumByUuid = new Map();
  for (const ev of events) {
    const isAgentTurn = ev.type === 'assistant' || (ev.type === 'codex_item' &&
      (ev.subtype === 'agent_message' || ev.subtype === 'assistant_message' ||
       ev.subtype === 'command_execution' || ev.subtype === 'file_change' ||
       ev.subtype === 'web_search'));
    if (isAgentTurn) {
      turnCounter++;
      turnNumByUuid.set(ev, turnCounter);
    }
  }

  const sessionCount = RECORD.summary.session_count || 1;
  const displayTurns = RECORD.index_row.num_turns ?? turnCounter;
  els.eventCount.innerHTML = `<span>${Number(displayTurns).toLocaleString()} turn${Number(displayTurns) === 1 ? '' : 's'} · ${sessionCount} session${sessionCount === 1 ? '' : 's'}</span><span class="trace-raw-count">${events.length.toLocaleString()} source events</span>`;
  els.eventCount.title = `${events.length.toLocaleString()} source events`;
  els.traceViewFocus.classList.toggle('active', TRACE_VIEW === 'focus');
  els.traceViewAll.classList.toggle('active', TRACE_VIEW === 'all');
  els.traceViewFocus.setAttribute('aria-pressed', String(TRACE_VIEW === 'focus'));
  els.traceViewAll.setAttribute('aria-pressed', String(TRACE_VIEW === 'all'));

  const out = [];
  let lastSession = -1;
  for (let eventIndex = 0; eventIndex < events.length; eventIndex++) {
    const ev = events[eventIndex];
    if (ev.type === 'system' && !wantSys && ev.subtype !== 'init') continue;
    if (skipUserEv.has(ev)) continue;

    if (ev.session_idx !== lastSession && lastSession >= 0) {
      out.push(renderSessionBanner(ev));
    }
    lastSession = ev.session_idx;

    out.push(renderEvent(ev, resultByUseId, expandResults, turnNumByUuid.get(ev), eventIndex));
  }
  els.trace.innerHTML = out.join('');
  if (anchor) restoreTraceAnchor(anchor);
  markClippedOutputs();
}

function captureTraceAnchor() {
  const stickyBottom = Math.max(0, els.tabNav.getBoundingClientRect().bottom) + 8;
  const visible = [...els.trace.querySelectorAll('.event')]
    .find(event => event.getBoundingClientRect().bottom > stickyBottom);
  if (!visible) return null;
  return { id: visible.id, offset: visible.getBoundingClientRect().top - stickyBottom };
}

function restoreTraceAnchor(anchor) {
  requestAnimationFrame(() => {
    const target = document.getElementById(anchor.id);
    if (!target) return;
    const stickyBottom = Math.max(0, els.tabNav.getBoundingClientRect().bottom) + 8;
    const delta = target.getBoundingClientRect().top - stickyBottom - anchor.offset;
    if (Math.abs(delta) > 0.5) window.scrollBy({ top: delta, behavior: 'instant' });
  });
}

function markClippedOutputs() {
  requestAnimationFrame(() => {
    document.querySelectorAll('#trace .tool-call').forEach(card => {
      const body = card.querySelector('.tool-result-body');
      const head = card.querySelector('.tool-result-head');
      if (!body || !head) return;
      const clipped = body.scrollHeight > body.clientHeight + 4;
      const badge = head.querySelector('.clip-more');
      if (clipped && !badge) {
        card.classList.add('clipped');
        const b = document.createElement('span');
        b.className = 'clip-more';
        head.appendChild(b);
        head.setAttribute('data-tip', 'Toggle full output');
      } else if (!clipped && badge && !card.classList.contains('expanded')) {
        card.classList.remove('clipped');
        badge.remove();
        head.removeAttribute('data-tip');
      }
    });
  });
}

function renderSessionBanner(ev) {
  const sess = RECORD.sessions[ev.session_idx];
  const n = ev.session_idx + 1;
  const total = RECORD.summary.session_count;
  return `<div class="session-divider session-${ev.session_idx % 5}" id="session-${n}">
    <span class="session-rule"></span>
    <div class="session-chip">
      <span class="session-chip-n">Session ${n}</span>
      <span class="session-chip-total">of ${total}</span>
    </div>
    <div class="session-chip-meta">
      ${sess && sess.model ? `<span>${escapeHtml(sess.model)}</span>` : ''}
      ${sess && sess.ts_start ? `<span class="muted">${escapeHtml(formatEventTime(sess.ts_start).time || sess.ts_start)}</span>` : ''}
    </div>
    <span class="session-rule"></span>
  </div>`;
}

function renderEvent(ev, resultByUseId, expandResults, turnNum, eventIndex) {
  let roleClass = '';
  let markerLabel = '';
  let markerNum = '';
  if (turnNum != null) {
    markerNum = `<div class="turn-num">${turnNum}</div>`;
  } else if (ev.type === 'system' && ev.subtype === 'init') {
    markerLabel = '<div class="turn-role">start</div>';
    roleClass = 'role-system';
  } else if (ev.type === 'system') {
    const short = ({
      task_started: 'task',
      task_notification: 'task',
    })[ev.subtype] || (ev.subtype || 'sys').slice(0, 6);
    markerLabel = `<div class="turn-role" title="${escapeHtml(ev.subtype || 'system')}">${escapeHtml(short)}</div>`;
    roleClass = 'role-system';
  } else if (ev.type === 'result') {
    markerLabel = '<div class="turn-role">end</div>';
    roleClass = 'role-end';
  } else if (ev.type === 'user') {
    markerLabel = '<div class="turn-role">user</div>';
    roleClass = 'role-user';
  } else {
    markerLabel = '<div class="turn-role">·</div>';
  }
  const cls = `event session-${(ev.session_idx ?? 0) % 5}${roleClass ? ' ' + roleClass : ''}`;
  const tsParts = ev.ts ? formatEventTime(ev.ts) : null;
  const anchorId = turnNum != null
    ? `turn-${turnNum}`
    : `ev-${ev.session_idx ?? 0}-${ev.type}-${(ev.uuid || ev.ts || eventIndex).toString().replace(/[^A-Za-z0-9_-]/g, '').slice(-8) || eventIndex}`;
  const tsTitle = tsParts
    ? `${tsParts.wall || ''}${tsParts.date ? ' · ' + tsParts.date : ''} (wall-clock)`
    : '';
  const marker = `<aside class="event-marker" data-anchor="${anchorId}" title="Copy link to this event">
    ${markerNum}${markerLabel}
    ${tsParts ? `<div class="ev-time" title="${escapeHtml(tsTitle)}"><span class="ev-time-full">${escapeHtml(tsParts.time || '')}</span><span class="ev-time-short">${escapeHtml((tsParts.time || '').slice(0, 5))}</span></div>` : ''}
    ${ev.parent_tool_use_id ? `<div class="ev-sub-tag">sub-agent</div>` : ''}
  </aside>`;

  let body = '';
  if (ev.type === 'system' && ev.subtype === 'init') {
    const sess = RECORD.sessions[ev.session_idx] || {};
    const facts = [];
    if (sess.model) facts.push(['model', `<strong>${escapeHtml(sess.model)}</strong>`]);
    if (sess.cwd) facts.push(['cwd', `<code>${escapeHtml(sess.cwd)}</code>`]);
    if (sess.permission_mode) facts.push(['permission', `<code>${escapeHtml(sess.permission_mode)}</code>`]);
    const tools = Array.isArray(sess.tools) ? sess.tools : [];
    if (tools.length) facts.push(['tools', `${tools.length} <span class="muted">(${escapeHtml(tools.slice(0, 6).join(', '))}${tools.length > 6 ? ', …' : ''})</span>`]);
    body = `<div class="session-init-block">
      <div class="session-init-head">Session start</div>
      <dl class="session-init-grid">
        ${facts.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}
      </dl>
    </div>`;
  } else if (ev.type === 'system') {
    const taskType = ev.task_type || ev.raw?.task_type || '';
    const isBackgroundCommand = taskType === 'local_bash';
    if (ev.subtype === 'task_started') {
      const desc = ev.raw?.description || '(no description)';
      const label = isBackgroundCommand ? 'Background command started' : 'Sub-agent started';
      const typeLabel = taskType && !isBackgroundCommand
        ? ` <span class="muted">(${escapeHtml(taskType)})</span>`
        : '';
      body = `<div class="block-label">${ICON.tool} ${label}${typeLabel}</div>
              <div class="block-card agent-text">${escapeHtml(desc)}</div>`;
    } else if (ev.subtype === 'task_notification') {
      const status = ev.raw?.status ? `<span class="chip ${ev.raw.status === 'completed' ? 'good' : 'accent'}">${escapeHtml(ev.raw.status)}</span>` : '';
      const summary = ev.raw?.summary || '(no summary)';
      const label = isBackgroundCommand ? 'Background command update' : 'Sub-agent update';
      body = `<div class="block-label">${ICON.output} ${label} ${status}</div>
              <div class="block-card agent-text">${escapeHtml(summary)}</div>`;
    } else {
      body = `<details><summary class="muted" style="cursor:pointer;font-size:0.72rem">${escapeHtml(ev.subtype || 'system')}</summary><pre class="muted" style="font-size:0.72rem;margin-top:4px">${escapeHtml(JSON.stringify(ev.raw, null, 2))}</pre></details>`;
    }
  } else if (ev.type === 'result') {
    const meta = [
      ev.duration_ms ? msToHms(ev.duration_ms) : null,
      ev.num_turns != null ? ev.num_turns + ' turns' : null,
      ev.total_cost_usd != null ? 'reported cost $' + Number(ev.total_cost_usd).toFixed(2) : null,
      ev.stop_reason || null,
    ].filter(Boolean);
    body = `
      <div class="block-label">${ICON.output} Session ended</div>
      ${meta.length ? `<div class="result-meta muted">${meta.map(escapeHtml).join(' · ')}</div>` : ''}
      ${ev.result_text ? `<div class="block-text">${escapeHtml(ev.result_text)}</div>` : ''}
    `;
  } else if (ev.type === 'codex_item') {
    body = renderCodexItem(ev.item, expandResults);
  } else if (Array.isArray(ev.blocks)) {
    body = ev.blocks.map(b => renderBlock(b, resultByUseId, expandResults)).join('');
  }

  if (!body.trim()) {
    if (TRACE_VIEW === 'focus') return '';
    body = `<details><summary class="muted" style="cursor:pointer;font-size:0.72rem">Raw ${escapeHtml(ev.type || 'event')}</summary><pre class="muted" style="font-size:0.72rem;margin-top:4px">${escapeHtml(JSON.stringify(ev.raw ?? ev, null, 2))}</pre></details>`;
  }

  return `<article id="${anchorId}" class="${cls}">${marker}<div class="event-body">${body}</div></article>`;
}

const ICON = {
  thought: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V18h6v-1.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z"/></svg>',
  tool: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
  output: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
  text: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
};

function renderBlock(block, resultByUseId, expandResults) {
  if (!block || typeof block !== 'object') return '';
  switch (block.type) {
    case 'text':
      return `<div class="block-card agent-text">${mdLite(block.text || '')}</div>`;
    case 'thinking':
      return `<details class="block-card agent-thinking" ${TRACE_VIEW === 'all' ? 'open' : ''}><summary>${ICON.thought} <span>Thought</span></summary><div class="thinking-body">${mdLite(block.thinking || '')}</div></details>`;
    case 'tool_use': {
      const pair = resultByUseId.get(block.id);
      return renderToolCall(block, pair, expandResults);
    }
    case 'tool_result':
      return `<div class="standalone-output"><div class="block-label">${ICON.output} Output${block.is_error ? ' · error' : ''}</div>${renderToolResultBody(block)}</div>`;
    case 'tool_reference':
      return `<div class="muted block">tool_reference: ${escapeHtml(block.tool_name || '')}</div>`;
    default:
      return `<div class="muted block" style="font-size:0.72rem">[block ${escapeHtml(block.type || 'unknown')}]</div>`;
  }
}

function renderToolCall(block, pair, expandResults) {
  const name = block.name || 'tool';
  let argsHtml = '';
  if (name === 'TodoWrite' && block.input && Array.isArray(block.input.todos)) {
    argsHtml = renderTodos(block.input.todos);
  } else if (name === 'Bash' && block.input && typeof block.input.command === 'string') {
    argsHtml = `<div class="bash-cmd">${escapeHtml(block.input.command)}</div>`;
    if (block.input.description) {
      argsHtml += `<div class="muted" style="font-size:0.72rem;margin-top:4px">${escapeHtml(block.input.description)}</div>`;
    }
  } else if (name === 'Read' && block.input && typeof block.input.file_path === 'string') {
    argsHtml = `<div>${escapeHtml(block.input.file_path)}</div>`;
  } else if (name === 'Write' && block.input && typeof block.input.file_path === 'string') {
    argsHtml = `<div>${escapeHtml(block.input.file_path)}</div>`;
    if (typeof block.input.content === 'string') {
      argsHtml += `<details style="margin-top:6px"><summary class="muted" style="font-size:0.7rem;cursor:pointer">content (${block.input.content.length} chars)</summary><pre style="margin-top:4px">${escapeHtml(block.input.content)}</pre></details>`;
    }
  } else if (name === 'Edit' && block.input && typeof block.input.file_path === 'string') {
    argsHtml = `<div>${escapeHtml(block.input.file_path)}</div>`
      + `<pre class="diff-remove">- ${escapeHtml(block.input.old_string || '')}</pre>`
      + `<pre class="diff-add">+ ${escapeHtml(block.input.new_string || '')}</pre>`;
  } else {
    argsHtml = `<pre>${escapeHtml(JSON.stringify(block.input ?? {}, null, 2))}</pre>`;
  }

  const idLabel = block.id ? `<span class="tool-id">${escapeHtml(block.id.slice(-8))}</span>` : '';
  const summary = `<button type="button" class="tool-call-head" aria-expanded="true">${ICON.tool} <span class="block-label-inline">Tool</span> <span class="tool-name">${escapeHtml(name)}</span>${idLabel}</button>`;
  const result = pair ? renderToolResult(pair.block, expandResults) : '';
  const expanded = expandResults ? 'expanded' : '';
  const variant = name === 'Bash' ? 'tool-bash' : '';
  return `<div class="tool-call ${variant} ${expanded}">${summary}<div class="tool-args">${argsHtml}</div>${result}</div>`;
}

function renderToolResult(block, expandResults) {
  const head = [];
  head.push(`<span class="tool-result-label">${ICON.output} Output</span>`);
  if (block.is_error) head.push('<span class="chip bad">error</span>');
  if (block.content_truncated) head.push(`<span class="muted">truncated · ${fmtNum(block.content_full_len || 0)} chars</span>`);
  return `<div class="tool-result"><div class="tool-result-head">${head.join('')}</div>${renderToolResultBody(block)}</div>`;
}

function renderToolResultBody(block) {
  const c = block.content;
  let body = '';
  if (typeof c === 'string') {
    body = escapeHtml(c) + (block.content_truncated ? `\n\n[truncated; ${block.content_full_len} chars total]` : '');
  } else if (Array.isArray(c)) {
    body = c.map(sub => {
      if (sub && typeof sub.text === 'string') return escapeHtml(sub.text);
      if (sub && sub.type === 'tool_reference') return `[tool_reference ${escapeHtml(sub.tool_name)}]`;
      return escapeHtml(JSON.stringify(sub));
    }).join('\n');
  } else {
    body = '(no content)';
  }
  return `<div class="tool-result-body ${block.is_error ? 'error' : ''}">${body}</div>`;
}

function renderCodexItem(item, expandResults) {
  if (!item || typeof item !== 'object') return '';
  switch (item.type) {
    case 'reasoning':
    case 'agent_reasoning':
      return `<details class="block-card agent-thinking" ${TRACE_VIEW === 'all' ? 'open' : ''}><summary>${ICON.thought} <span>Thought</span></summary><div class="thinking-body">${mdLite(item.text || '')}</div></details>`;
    case 'agent_message':
    case 'assistant_message':
      return `<div class="block-card agent-text">${mdLite(item.text || '')}</div>`;
    case 'todo_list':
      return `<div class="standalone-output"><div class="block-label">Todo list</div>${renderTodos(item.items || [])}</div>`;
    case 'command_execution': {
      const out = item.aggregated_output || '';
      const truncated = out.length > 16384;
      const exitMeta = item.exit_code != null ? `<span class="tool-exit">exit ${item.exit_code}</span>` : '';
      const head = `<button type="button" class="tool-call-head" aria-expanded="true">${ICON.tool} <span class="block-label-inline">Tool</span> <span class="tool-name">${escapeHtml(item.shell || 'command')}</span>${exitMeta}</button>`;
      const args = `<div class="tool-args"><div class="bash-cmd">${escapeHtml(item.command || '')}</div></div>`;
      const body = `<div class="tool-result"><div class="tool-result-head"><span class="tool-result-label">${ICON.output} Output</span>${item.status ? `<span class="muted">${escapeHtml(item.status)}</span>` : ''}${truncated ? '<span class="muted">truncated</span>' : ''}</div><div class="tool-result-body ${item.exit_code && item.exit_code !== 0 ? 'error' : ''}">${escapeHtml(out.slice(0, 16384))}${truncated ? '\n\n[... truncated]' : ''}</div></div>`;
      return `<div class="tool-call tool-bash ${expandResults ? 'expanded' : ''}">${head}${args}${body}</div>`;
    }
    case 'file_change': {
      const rows = (item.changes || []).map(c =>
        `<div class="todo-item"><span class="check">${c.kind === 'add' ? '＋' : c.kind === 'delete' ? '－' : '✎'}</span><span>${escapeHtml(c.path)}</span></div>`).join('');
      return `<div class="standalone-output"><div class="block-label">File change · ${escapeHtml(item.status || '')}</div>${rows}</div>`;
    }
    case 'web_search':
      return `<div class="standalone-output"><div class="block-label">Web search</div><div>${escapeHtml(item.query || '')}</div></div>`;
    default:
      return `<details><summary class="muted" style="font-size:0.7rem;cursor:pointer">${escapeHtml(item.type || 'item')}</summary><pre style="font-size:0.74rem;white-space:pre-wrap;margin-top:4px">${escapeHtml(JSON.stringify(item, null, 2))}</pre></details>`;
  }
}

function renderTodos(todos) {
  return `<ul class="todo-list">${todos.map(t =>
    `<li class="todo-item ${t.completed ? 'done' : ''}"><span class="check">${t.completed ? '☑' : '☐'}</span><span>${escapeHtml(t.text || t.content || '')}</span></li>`
  ).join('')}</ul>`;
}


function getMetricDefs() {
  const allSnaps = RECORD.system_monitor || [];
  const snaps = TRACE_START_MS == null
    ? allSnaps
    : allSnaps.filter(s => {
        if (!s.ts) return true;
        const t = parseTraceTs(s.ts);
        return isNaN(t) || t >= TRACE_START_MS - 1000;
      });
  const labels = snaps.map(s => {
    if (!s.ts) return '';
    const t = parseTraceTs(s.ts);
    if (isNaN(t)) return s.ts;
    if (TRACE_START_MS == null) return s.ts;
    return fmtRelTime(Math.max(0, t - TRACE_START_MS));
  });
  const gpu = (k) => snaps.map(s => s.gpu ? s.gpu[k] : null);
  return [
    {
      key: 'gpu-util', title: 'GPU utilization', unit: '%',
      data: gpu('util_pct'), yMax: 100, palette: 'session-2',
      fmt: v => fmt(v, 0) + '%',
    },
    {
      key: 'gpu-mem', title: 'GPU memory used', unit: 'GiB',
      data: snaps.map(s => s.gpu ? s.gpu.mem_used_mib / 1024 : null),
      yMax: (snaps[0]?.gpu?.mem_total_mib || 81559) / 1024, palette: 'accent',
      fmt: v => fmt(v, 1) + ' GiB',
    },
    {
      key: 'gpu-temp', title: 'GPU temperature', unit: '°C',
      data: gpu('temp_c'), palette: 'session-4',
      fmt: v => fmt(v, 0) + '°C',
    },
    {
      key: 'gpu-power', title: 'GPU power', unit: 'W',
      data: gpu('power_w'), palette: 'session-1',
      fmt: v => fmt(v, 0) + ' W',
    },
    {
      key: 'cpu-load', title: 'CPU load (1m)', unit: 'load',
      data: snaps.map(s => s.cpu_load_1m), palette: 'session-2',
      fmt: v => fmt(v, 2),
    },
    {
      key: 'mem-used', title: 'System memory used', unit: 'GiB',
      data: snaps.map(s => s.mem_used_gib), palette: 'session-3',
      fmt: v => fmt(v, 0) + ' GiB',
    },
  ].map(m => ({ ...m, labels }));
}

function renderMiniCharts() {
  const phases = RECORD.oracle?.phases || [];
  if (!phases.length) {
    setMetricsUnavailable('No oracle result is available.');
    return;
  }
  setMetricsAvailable();
  els.metricGridRail.innerHTML = phases.map(oraclePhaseHtml).join('');
  els.showAllBtn.classList.remove('hidden');
}

function oraclePhaseHtml(phase) {
  const status = phase.status || 'unknown';
  const icon = status === 'success' ? '✓' : status === 'failure' ? '!' : '·';
  return `<div class="metric-card-mini oracle-phase-card ${escapeHtml(status)}">
    <div class="oracle-phase-head"><span class="oracle-phase-icon">${icon}</span><span>${escapeHtml(prettyPhase(phase.phase))}</span></div>
    <p>${escapeHtml(phase.summary || status)}</p>
  </div>`;
}

function prettyPhase(value) {
  return String(value || '').replaceAll('_', ' ');
}

function setMetricsAvailable() {
  els.railEmpty.classList.add('hidden');
  els.metricGridRail.classList.remove('hidden');
  const toolbarBtn = document.getElementById('open-metrics-btn');
  [els.showAllBtn, toolbarBtn].filter(Boolean).forEach(btn => {
    btn.disabled = false;
    btn.removeAttribute('aria-disabled');
    btn.removeAttribute('title');
  });
}

function setMetricsUnavailable(message) {
  els.railEmpty.textContent = message;
  els.railEmpty.classList.remove('hidden');
  els.metricGridRail.classList.add('hidden');
  els.showAllBtn.classList.add('hidden');
  const toolbarBtn = document.getElementById('open-metrics-btn');
  [els.showAllBtn, toolbarBtn].filter(Boolean).forEach(btn => {
    btn.disabled = true;
    btn.setAttribute('aria-disabled', 'true');
    btn.title = message;
  });
}

function updateShowAllVisibility() {
  const rail = els.metricGridRail.closest('.rail-right');
  if (!rail) return;
  const clipped = rail.scrollHeight > rail.clientHeight + 2;
  els.showAllBtn.classList.toggle('hidden', !clipped);
}

function setChartDefaults() {
  const css = getComputedStyle(document.documentElement);
  Chart.defaults.font.family = "'JetBrains Mono', 'SF Mono', monospace";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = css.getPropertyValue('--text-secondary').trim() || '#6b655a';
  Chart.defaults.borderColor = css.getPropertyValue('--border-color').trim() || '#d9d4c8';
}

function paletteColor(palette) {
  const css = getComputedStyle(document.documentElement);
  const map = {
    accent:    css.getPropertyValue('--accent-primary').trim() || '#a66b4f',
    'session-1': css.getPropertyValue('--session-1').trim() || '#8a7240',
    'session-2': css.getPropertyValue('--session-2').trim() || '#6f7d45',
    'session-3': css.getPropertyValue('--session-3').trim() || '#80526a',
    'session-4': css.getPropertyValue('--session-4').trim() || '#97553a',
  };
  return map[palette] || map.accent;
}

function metricCardHtml(def, where) {
  return `<div class="metric-card-mini">
    <div class="metric-card-title">${escapeHtml(def.title)}</div>
    <canvas id="metric-${where}-${def.key}"></canvas>
  </div>`;
}

function buildChart(canvasId, def, { motion = 'initial' } = {}) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  const css = getComputedStyle(document.documentElement);
  const color = paletteColor(def.palette);
  const muted = css.getPropertyValue('--text-secondary').trim() || '#6b655a';
  const border = css.getPropertyValue('--border-color').trim() || '#d9d4c8';
  const tickTime = (_v, i) => {
    const ts = def.labels[i];
    if (!ts) return '';
    return /^\d{2}:\d{2}:\d{2}$/.test(ts) ? ts.slice(0, 5) : ts;
  };
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: def.labels,
      datasets: [{
        label: def.title,
        data: def.data,
        borderColor: color,
        backgroundColor: color + '22',
        fill: 'origin',
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: REDUCED_MOTION.matches || motion === 'none'
        ? false
        : { duration: motion === 'initial' ? 450 : 190, easing: 'easeOutCubic' },
      elements: { point: { radius: 0 }, line: { borderWidth: 1.4, tension: 0.25 } },
      layout: { padding: { top: 8, right: 10, bottom: 4, left: 4 } },
      scales: {
        x: {
          display: true,
          grid: { display: false },
          border: { color: border },
          ticks: {
            autoSkip: true,
            maxTicksLimit: 4,
            maxRotation: 0,
            callback: tickTime,
            padding: 6,
            font: { size: 10.5 },
          },
        },
        y: {
          display: true,
          position: 'left',
          beginAtZero: true,
          suggestedMax: def.yMax,
          grid: { color: border + '66', drawTicks: false, drawBorder: false },
          border: { display: false },
          ticks: {
            color: muted,
            maxTicksLimit: 4,
            padding: 6,
            font: { size: 10.5 },
            callback: (v) => unitTick(v, def.unit),
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          intersect: false,
          mode: 'index',
          titleFont: { size: 12 },
          bodyFont: { size: 12 },
          padding: 10,
          boxPadding: 4,
          callbacks: {
            title: (items) => {
              const ts = def.labels[items[0]?.dataIndex];
              return ts || '';
            },
            label: (ctx) => `${def.title}: ${def.fmt(ctx.parsed.y)}`,
          },
        },
      },
    },
  });
}

function unitTick(value, unit) {
  if (value == null || isNaN(value)) return '';
  let n;
  if (value === 0) n = '0';
  else if (Math.abs(value) >= 10) n = value.toFixed(0);
  else if (Math.abs(value) >= 1) n = value.toFixed(1).replace(/\.0$/, '');
  else n = value.toFixed(2);
  switch (unit) {
    case '%':    return n + '%';
    case 'GiB':  return n + 'G';
    case '°C':   return n + '°C';
    case 'W':    return n + 'W';
    case 'load': return n;
    default:     return unit ? n + ' ' + unit : n;
  }
}

function destroyCharts(arr) {
  for (const c of arr) { try { c?.destroy(); } catch {} }
  arr.length = 0;
}

function openMetricsModal(event) {
  const phases = RECORD.oracle?.phases || [];
  if (!phases.length) return;
  els.metricGridModal.innerHTML = phases.map(oraclePhaseHtml).join('');
  els.metricsModal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeMetricsModal() {
  els.metricsModal.classList.add('hidden');
  document.body.style.overflow = '';
}

function setupMetricsModal() {
  els.showAllBtn.addEventListener('click', openMetricsModal);
  const toolbarBtn = document.getElementById('open-metrics-btn');
  if (toolbarBtn) toolbarBtn.addEventListener('click', openMetricsModal);
  els.metricsModal.querySelectorAll('[data-modal-close]').forEach(el =>
    el.addEventListener('click', closeMetricsModal));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !els.metricsModal.classList.contains('hidden')) closeMetricsModal();
  });
}

function renderMiniTokens() {
  const u = RECORD.summary.usage_total || {};
  const rows = [
    ['input',       u.input_tokens],
    ['output',      u.output_tokens],
    ['cache write', u.cache_creation_input_tokens],
    ['cache read',  u.cache_read_input_tokens],
  ].filter(([, v]) => v != null && v > 0);
  if (!rows.length) {
    els.summaryTokensBlock.classList.add('hidden');
    return;
  }
  els.summaryTokensBlock.classList.remove('hidden');
  els.summaryTokens.innerHTML = rows.map(([k, v]) =>
    `<dt>${k}</dt><dd>${fmtNum(v)}</dd>`).join('');
}


function renderOracle() {
  renderOracleBrief();
  const phases = RECORD.oracle?.phases || [];
  if (!phases.length) {
    els.oracle.innerHTML = '<p class="muted">No oracle result is available for this run.</p>';
    return;
  }
  els.oracle.innerHTML = `<div class="oracle-phase-list">${phases.map(oraclePhaseHtml).join('')}</div>`;
}

function renderOracleBrief() {
  const brief = RECORD.case_brief || {};
  const rows = [
    ['Core claim', brief.core_claim],
    ['Acceptable evidence', brief.acceptable_evidence],
    ['Allowed tolerance', brief.allowed_tolerance],
  ].filter(([, value]) => value);
  els.oracleBrief.innerHTML = `<div class="case-brief-card">${rows.map(([label, value]) =>
    `<div><span>${escapeHtml(label)}</span><p>${escapeHtml(value)}</p></div>`).join('')}</div>`;
}


async function loadPrompt() {
  if (PROMPT_LOADED) return;
  PROMPT_LOADED = true;
  els.promptContent.innerHTML = `<pre class="prompt-preview"><code>${escapeHtml(RECORD.prompt || 'No prompt was saved.')}</code></pre>`;
}


function setupTabs() {
  const btns = [...els.tabNav.querySelectorAll('.tab-btn')];
  const sections = new Map();
  for (const b of btns) sections.set(b.dataset.tab, document.getElementById('section-' + b.dataset.tab));

  const legacyHashTab = new URLSearchParams(location.hash.slice(1)).get('tab');
  const hashTab = params.get('tab') || legacyHashTab;
  let active = (hashTab && sections.has(hashTab)) ? hashTab : btns[0].dataset.tab;
  selectTab(active, { initial: true });

  els.tabNav.addEventListener('click', e => {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;
    selectTab(btn.dataset.tab);
  });

  function selectTab(name, { initial = false } = {}) {
    btns.forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    for (const [k, sec] of sections) sec?.classList.toggle('active', k === name);

    const url = new URL(window.location.href);
    if (name === 'trace') url.searchParams.delete('tab');
    else url.searchParams.set('tab', name);

    const currentHashHasLegacyTab = new URLSearchParams(url.hash.slice(1)).has('tab');
    if (currentHashHasLegacyTab || (name !== 'trace' && eventAnchorFromHash(url.hash))) {
      url.hash = '';
    }
    history.replaceState(null, '', url.toString());
    if (!initial) {
      if (window.matchMedia('(max-width: 900px)').matches) {
        requestAnimationFrame(() => scrollSectionBelowTabs(sections.get(name)));
      } else {
        window.scrollTo({ top: 0, behavior: 'auto' });
      }
    }
    if (name === 'prompt') loadPrompt();
    if (name === 'trace') markClippedOutputs();
  }
}

function scrollSectionBelowTabs(element) {
  if (!element) return;
  const topbarHeight = document.querySelector('.topbar')?.offsetHeight || 48;
  const tabHeight = els.tabNav.offsetHeight || 44;
  const top = element.getBoundingClientRect().top + window.scrollY - topbarHeight - tabHeight - 8;
  window.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
}

window.addEventListener('aebench:themechange', () => {
  if (!RECORD || typeof Chart === 'undefined' || !(RECORD.system_monitor || []).length) return;
  setChartDefaults();
  const defs = getMetricDefs();

  if (els.metricGridRail.querySelector('canvas')) {
    destroyCharts(RAIL_CHARTS);
    RAIL_CHARTS = defs.map(d => buildChart(`metric-rail-${d.key}`, d, { motion: 'none' }));
  }

  if (!els.metricsModal.classList.contains('hidden')) {
    destroyCharts(MODAL_CHARTS);
    MODAL_CHARTS = defs.map(d => buildChart(`metric-modal-${d.key}`, d, { motion: 'none' }));
  }
});




function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function mdLite(s) {
  return escapeHtml(s)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>');
}
function fmtNum(v) {
  if (typeof v !== 'number') return escapeHtml(String(v));
  if (Math.abs(v) >= 1000) return v.toLocaleString();
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(3);
  return v.toFixed(4);
}
function fmt(v, decimals = 0) {
  if (v == null) return '-';
  if (typeof v !== 'number') return String(v);
  return v.toFixed(decimals);
}
function msToHms(ms) {
  if (ms == null) return '-';
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h}h ${m}m ${sec}s`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function humanDuration(durationMs) {
  if (durationMs) return msToHms(durationMs);
  return '-';
}

const _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function formatEventTime(iso) {
  const t = parseTraceTs(iso);
  if (isNaN(t)) return { date: '', time: iso, wall: iso };
  const d = new Date(t);
  const pad = n => String(n).padStart(2, '0');
  const date = `${_MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  const wall = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  let time = wall;
  if (TRACE_START_MS != null) {
    time = fmtRelTime(Math.max(0, t - TRACE_START_MS));
  }
  return { date, time, wall };
}

function parseTraceTs(ts) {
  if (!ts) return NaN;
  const s = String(ts);
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/.test(s) && !/[Zz]$|[+-]\d{2}:?\d{2}$/.test(s)) {
    return Date.parse(s.replace(' ', 'T') + 'Z');
  }
  return Date.parse(s);
}

function fmtRelTime(deltaMs) {
  if (deltaMs == null || isNaN(deltaMs)) return '';
  const s = Math.max(0, Math.floor(deltaMs / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = n => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(sec)}`;
}

load();
