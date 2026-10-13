(function () {
  const DEFAULT_STATE = Object.freeze({
    case: '',
    model: '',
    agent: '',
    prompt_profile: '',
    q: '',
    group: 'case',
    sort: 'score-desc',
    open: '',
  });

  // TODO: Define stable case and model ordering when AEBench publishes a run catalog.
  const CASE_ORDER = [];
  const MODEL_ORDER = [];

  function orderIndex(orderList, value) {
    if (!value) return Infinity;
    const normalized = String(value).toLowerCase();
    for (let i = 0; i < orderList.length; i++) {
      if (orderList[i].toLowerCase() === normalized) return i;
    }
    return Infinity;
  }

  function readState(input) {
    const params = input instanceof URLSearchParams
      ? input
      : new URLSearchParams(typeof input === 'string' ? input : window.location.search);
    const group = ['case-model', 'case', 'prompt-profile', 'none'].includes(params.get('group'))
      ? params.get('group')
      : DEFAULT_STATE.group;
    const sort = ['score-desc', 'score-asc', 'cost-desc', 'turns-desc', 'duration-desc'].includes(params.get('sort'))
      ? params.get('sort')
      : DEFAULT_STATE.sort;
    return {
      case: params.get('case') || '',
      model: params.get('model') || '',
      agent: params.get('agent') || '',
      prompt_profile: params.get('prompt_profile') || '',
      q: params.get('q') || '',
      group,
      sort,
      open: params.get('open') || '',
    };
  }

  function writeState(url, state) {
    const next = new URL(url.toString());
    for (const key of Object.keys(DEFAULT_STATE)) next.searchParams.delete(key);
    for (const [key, value] of Object.entries(state)) {
      if (value == null || value === '' || value === DEFAULT_STATE[key]) continue;
      next.searchParams.set(key, value);
    }
    return next;
  }

  function prettyCase(value) {
    return value ? String(value) : '';
  }

  function prettyModel(name) {
    return prettyAgent(name);
  }

  function cap(value) {
    return value ? value[0].toUpperCase() + value.slice(1) : value;
  }

  function prettyAgent(name) {
    if (!name) return '';
    if (name === 'codex_non_api') return 'Codex subscription';
    if (name === 'claude_non_api') return 'Claude subscription';
    let value = String(name);
    let annotation = '';
    value = value.replace(/\[([^\]]+)\]\s*$/, (_, item) => {
      annotation = ` (${item.toUpperCase()})`;
      return '';
    });
    value = value.replace(/^claude-(opus|sonnet|haiku)-(\d+)-(\d+)$/i,
      (_, family, major, minor) => `Claude ${cap(family)} ${major}.${minor}`);
    value = value.replace(/^gpt-([\d.]+)(?:-(.+))?$/i, (_, version, tail) =>
      `GPT ${version}${tail ? ' ' + tail.split('-').map(cap).join(' ') : ''}`);
    value = value.replace(/^gemini-([\d.]+)(?:-(.+))?$/i, (_, version, tail) =>
      `Gemini ${version}${tail ? ' ' + tail.split('-').map(cap).join(' ') : ''}`);
    return value + annotation;
  }

  function prettyAgentForRun(run) {
    return run ? prettyAgent(run.agent_kind) : '';
  }

  function prettyHarness(value) {
    const names = {
      claude_code: 'Claude Code',
      codex: 'Codex CLI',
      opencode: 'OpenCode',
    };
    return value ? names[String(value).toLowerCase()] || String(value) : '';
  }

  function prettyReasoningEffort(value) {
    if (!value) return '';
    const labels = {
      low: 'Low',
      medium: 'Medium',
      high: 'High',
      xhigh: 'xHigh',
      max: 'Max',
    };
    const normalized = String(value).toLowerCase();
    return labels[normalized] || String(value);
  }

  function filterRuns(runs, state) {
    const query = String(state.q || '').trim().toLowerCase();
    return runs.filter(run => {
      if (state.prompt_profile && run.prompt_profile !== state.prompt_profile) return false;
      if (state.case && run.case_id !== state.case) return false;
      if (state.model && run.model !== state.model) return false;
      if (state.agent && run.agent_kind !== state.agent) return false;
      if (!query) return true;
      const haystack = [
        run.run_id, run.prompt_profile, run.case_id, run.model,
        run.agent_kind, prettyAgent(run.agent_kind),
        prettyAgentForRun(run), prettyModel(run.model),
        prettyCase(run.case_id),
        run.reasoning_effort, prettyReasoningEffort(run.reasoning_effort),
      ].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }

  function sorter(sort) {
    switch (sort) {
      case 'score-asc': return (a, b) => (a.score_ratio ?? Infinity) - (b.score_ratio ?? Infinity);
      case 'cost-desc': return (a, b) => (b.total_cost_usd ?? 0) - (a.total_cost_usd ?? 0);
      case 'turns-desc': return (a, b) => (b.num_turns ?? 0) - (a.num_turns ?? 0);
      case 'duration-desc': return (a, b) => (b.duration_ms ?? 0) - (a.duration_ms ?? 0);
      case 'score-desc':
      default: return (a, b) => (b.score_ratio ?? -1) - (a.score_ratio ?? -1);
    }
  }

  function groupKey(run, mode) {
    switch (mode) {
      case 'case-model': return `${run.case_id || '?'}|${run.model || '?'}`;
      case 'case': return run.case_id || '?';
      case 'prompt-profile': return run.prompt_profile || '?';
      default: return '';
    }
  }

  function groupSorter(mode, a, b) {
    if (mode === 'case' || mode === 'case-model') {
      const [aCase, aModel] = a.key.split('|');
      const [bCase, bModel] = b.key.split('|');
      const aCaseIndex = orderIndex(CASE_ORDER, aCase);
      const bCaseIndex = orderIndex(CASE_ORDER, bCase);
      if (aCaseIndex !== bCaseIndex) return aCaseIndex - bCaseIndex;
      if (mode === 'case-model') {
        const aModelIndex = orderIndex(MODEL_ORDER, aModel);
        const bModelIndex = orderIndex(MODEL_ORDER, bModel);
        if (aModelIndex !== bModelIndex) return aModelIndex - bModelIndex;
      }
      return a.key.localeCompare(b.key);
    }
    const aBest = a.best ?? -1;
    const bBest = b.best ?? -1;
    if (aBest !== bBest) return bBest - aBest;
    if (a.rows.length !== b.rows.length) return b.rows.length - a.rows.length;
    return a.key.localeCompare(b.key);
  }

  function buildGroups(rows, mode) {
    const groups = new Map();
    for (const run of rows) {
      const key = groupKey(run, mode);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(run);
    }
    return [...groups.entries()].map(([key, groupRows]) => {
      let best = null;
      for (const run of groupRows) {
        if (run.score_ratio != null && (best == null || run.score_ratio > best)) best = run.score_ratio;
      }
      return { key, rows: groupRows, best };
    }).sort((a, b) => groupSorter(mode, a, b));
  }

  function orderedRuns(runs, state) {
    const rows = filterRuns(runs, state).slice().sort(sorter(state.sort));
    if (state.group === 'none') return rows;
    return buildGroups(rows, state.group).flatMap(group => group.rows);
  }

  window.AEBENCH_TraceCatalog = {
    DEFAULT_STATE,
    CASE_ORDER,
    MODEL_ORDER,
    orderIndex,
    readState,
    writeState,
    prettyCase,
    prettyModel,
    prettyAgent,
    prettyAgentForRun,
    prettyHarness,
    prettyReasoningEffort,
    filterRuns,
    sorter,
    groupKey,
    buildGroups,
    orderedRuns,
  };
})();
