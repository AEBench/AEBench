// graph_view.js — Interactive Trajectory Multigraph Renderer with Dagre Layout
// AEBench Process-Centric Trace Viewer

(function () {
  'use strict';

  let GRAPH_DATA = null;
  let ALL_EVENTS = null;
  let SVG_EL = null;
  let VIEWPORT_EL = null;
  let INSPECTOR_EL = null;
  let SELECTED_NODE_ID = null;

  // Pan & Zoom state
  let transform = { x: 40, y: 40, scale: 1.0 };
  let isPanning = false;
  let panStart = { x: 0, y: 0 };
  let filterLoopsOnly = false;
  let filterHideLowSignal = false;

  const LOW_SIGNAL_CMDS = new Set(['pwd', 'whoami', 'uname', 'which', 'type', 'true']);

  function init(graphData, events) {
    GRAPH_DATA = graphData;
    ALL_EVENTS = events;
    SVG_EL = document.getElementById('graph-svg');
    VIEWPORT_EL = document.getElementById('graph-viewport');
    INSPECTOR_EL = document.getElementById('graph-inspector');

    if (!SVG_EL || !GRAPH_DATA || !GRAPH_DATA.nodes) return;

    setupControls();
    setupPanZoom();
    renderGraph();
  }

  function setupControls() {
    const btnFit = document.getElementById('graph-btn-fit');
    const btnZoomIn = document.getElementById('graph-btn-zoomin');
    const btnZoomOut = document.getElementById('graph-btn-zoomout');
    const btnLoops = document.getElementById('graph-btn-loops');
    const btnLowSignal = document.getElementById('graph-btn-lowsignal');
    const btnCloseInspector = document.getElementById('inspector-close-btn');

    if (btnFit) btnFit.addEventListener('click', fitToScreen);
    if (btnZoomIn) btnZoomIn.addEventListener('click', () => zoomBy(1.2));
    if (btnZoomOut) btnZoomOut.addEventListener('click', () => zoomBy(0.8));
    if (btnLoops) {
      btnLoops.addEventListener('click', () => {
        filterLoopsOnly = !filterLoopsOnly;
        btnLoops.classList.toggle('active', filterLoopsOnly);
        renderGraph();
      });
    }
    if (btnLowSignal) {
      btnLowSignal.addEventListener('click', () => {
        filterHideLowSignal = !filterHideLowSignal;
        btnLowSignal.classList.toggle('active', filterHideLowSignal);
        renderGraph();
      });
    }
    if (btnCloseInspector) {
      btnCloseInspector.addEventListener('click', closeInspector);
    }
  }

  function setupPanZoom() {
    if (!SVG_EL) return;

    SVG_EL.addEventListener('mousedown', e => {
      if (e.target.closest('.graph-node')) return;
      isPanning = true;
      panStart = { x: e.clientX - transform.x, y: e.clientY - transform.y };
      SVG_EL.classList.add('panning');
    });

    window.addEventListener('mousemove', e => {
      if (!isPanning) return;
      transform.x = e.clientX - panStart.x;
      transform.y = e.clientY - panStart.y;
      updateTransform();
    });

    window.addEventListener('mouseup', () => {
      if (isPanning) {
        isPanning = false;
        SVG_EL.classList.remove('panning');
      }
    });

    SVG_EL.addEventListener('wheel', e => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.12 : 0.89;
      const rect = SVG_EL.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      const newScale = Math.min(2.5, Math.max(0.2, transform.scale * zoomFactor));
      transform.x = mouseX - (mouseX - transform.x) * (newScale / transform.scale);
      transform.y = mouseY - (mouseY - transform.y) * (newScale / transform.scale);
      transform.scale = newScale;
      updateTransform();
    }, { passive: false });
  }

  function zoomBy(factor) {
    const rect = SVG_EL.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const newScale = Math.min(2.5, Math.max(0.2, transform.scale * factor));
    transform.x = cx - (cx - transform.x) * (newScale / transform.scale);
    transform.y = cy - (cy - transform.y) * (newScale / transform.scale);
    transform.scale = newScale;
    updateTransform();
  }

  function updateTransform() {
    if (VIEWPORT_EL) {
      VIEWPORT_EL.setAttribute(
        'transform',
        `translate(${transform.x}, ${transform.y}) scale(${transform.scale})`
      );
    }
  }

  function renderGraph() {
    if (!GRAPH_DATA || typeof dagre === 'undefined') return;

    const g = new dagre.graphlib.Graph({ multigraph: true });
    g.setGraph({
      rankdir: 'TB',
      nodesep: 45,
      ranksep: 55,
      marginx: 40,
      marginy: 40,
    });
    g.setDefaultEdgeLabel(() => ({}));

    // Filter nodes if low-signal filter is enabled
    const visibleNodes = GRAPH_DATA.nodes.filter(node => {
      if (filterHideLowSignal && node.visit_count === 1) {
        const cmd = (node.command || '').trim().split(' ')[0].toLowerCase();
        if (LOW_SIGNAL_CMDS.has(cmd)) return false;
      }
      return true;
    });

    const visibleNodeIds = new Set(visibleNodes.map(n => n.id));

    // Register nodes
    visibleNodes.forEach(node => {
      const isLoop = node.visit_count > 1;
      const width = isLoop ? 240 : 210;
      const height = 58;
      g.setNode(node.id, {
        width,
        height,
        nodeData: node,
      });
    });

    // Register edges
    const visibleEdges = (GRAPH_DATA.edges || []).filter(edge => {
      if (!visibleNodeIds.has(edge.source) || !visibleNodeIds.has(edge.target)) return false;
      if (filterLoopsOnly && !edge.is_self_loop && !edge.is_backtrack && edge.count <= 1) return false;
      return true;
    });

    visibleEdges.forEach(edge => {
      if (!edge.is_self_loop) {
        g.setEdge(edge.source, edge.target, { edgeData: edge }, edge.id);
      }
    });

    // Calculate layout
    dagre.layout(g);

    // Build SVG markup
    let out = `
      <defs>
        <marker id="arrow-default" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill="var(--text-muted, #94a3b8)" />
        </marker>
        <marker id="arrow-backtrack" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#f97316" />
        </marker>
        <marker id="arrow-loop" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 1 L 9 5 L 0 9 z" fill="#ef4444" />
        </marker>
      </defs>
      <g id="edges-layer"></g>
      <g id="nodes-layer"></g>
    `;

    VIEWPORT_EL.innerHTML = out;
    const edgesLayer = VIEWPORT_EL.querySelector('#edges-layer');
    const nodesLayer = VIEWPORT_EL.querySelector('#nodes-layer');

    // Render Edges
    let edgesHtml = '';
    visibleEdges.forEach(edge => {
      const src = g.node(edge.source);
      const tgt = g.node(edge.target);
      if (!src || !tgt) return;

      if (edge.is_self_loop) {
        // Draw self loop arc on right side of node
        const x = src.x + src.width / 2;
        const yTop = src.y - 12;
        const yBot = src.y + 12;
        const loopRadius = 28;
        const d = `M ${x} ${yTop} C ${x + loopRadius} ${yTop - 15}, ${x + loopRadius} ${yBot + 15}, ${x} ${yBot}`;
        const marker = 'url(#arrow-loop)';
        edgesHtml += `
          <g class="graph-edge self-loop" data-edge-id="${edge.id}">
            <path d="${d}" fill="none" stroke="#ef4444" stroke-width="2.2" stroke-dasharray="4 2" marker-end="${marker}"/>
            <text x="${x + loopRadius + 4}" y="${src.y + 4}" class="edge-count-label" fill="#ef4444">${edge.count}×</text>
          </g>
        `;
      } else {
        const edgeObj = g.edge(edge.source, edge.target, edge.id);
        if (!edgeObj || !edgeObj.points) return;

        const points = edgeObj.points;
        const d = buildSplinePath(points);
        const isBacktrack = edge.is_backtrack;
        const strokeColor = isBacktrack ? '#f97316' : 'var(--border-subtle, #475569)';
        const marker = isBacktrack ? 'url(#arrow-backtrack)' : 'url(#arrow-default)';
        const strokeWidth = Math.min(5, Math.max(1.5, 1.2 + Math.log2(edge.count || 1) * 1.2));
        const dash = isBacktrack ? 'stroke-dasharray="5 3"' : '';

        edgesHtml += `
          <g class="graph-edge ${isBacktrack ? 'backtrack-edge' : ''}" data-edge-id="${edge.id}">
            <path d="${d}" fill="none" stroke="${strokeColor}" stroke-width="${strokeWidth}" ${dash} marker-end="${marker}"/>
            ${edge.count > 1 ? `<text x="${points[Math.floor(points.length / 2)].x + 6}" y="${points[Math.floor(points.length / 2)].y - 4}" class="edge-count-label">${edge.count}×</text>` : ''}
          </g>
        `;
      }
    });
    edgesLayer.innerHTML = edgesHtml;

    // Render Nodes
    let nodesHtml = '';
    visibleNodes.forEach(node => {
      const pos = g.node(node.id);
      if (!pos) return;

      const isSelected = SELECTED_NODE_ID === node.id;
      const x = pos.x - pos.width / 2;
      const y = pos.y - pos.height / 2;
      const action = node.action || 'misc';
      const isLoop = node.visit_count > 1;

      // Check if any occurrence failed
      const hasFailure = node.occurrences && node.occurrences.some(o => o.outcome === 'failure');
      const allPassed = node.occurrences && node.occurrences.every(o => o.outcome === 'success');

      nodesHtml += `
        <g class="graph-node node-${action} ${isLoop ? 'is-loop' : ''} ${isSelected ? 'selected' : ''}"
           data-node-id="${node.id}"
           transform="translate(${x}, ${y})"
           tabindex="0"
           role="button"
           aria-label="${escapeHtml(node.label)} (${node.visit_count} visits)">
          <rect class="node-box" width="${pos.width}" height="${pos.height}" rx="7" ry="7"/>
          <line class="node-accent-bar" x1="0" y1="0" x2="0" y2="${pos.height}"/>
          
          <g class="node-content" transform="translate(12, 16)">
            <text class="node-tool-tag">${escapeHtml(node.tool)}</text>
            <text class="node-action-tag action-badge action-${action}" x="${pos.width - 24}" text-anchor="end">${escapeHtml(action)}</text>
            <text class="node-label" y="24" title="${escapeHtml(node.command || node.label)}">${escapeHtml(truncate(node.label, 26))}</text>
          </g>

          ${isLoop ? `
            <g class="node-visit-badge" transform="translate(${pos.width - 12}, 0)">
              <rect x="-14" y="-8" width="28" height="16" rx="8" ry="8" class="badge-bg"/>
              <text x="0" y="3" text-anchor="middle" class="badge-text">${node.visit_count}×</text>
            </g>
          ` : ''}

          ${hasFailure ? `
            <circle cx="10" cy="10" r="4.5" class="status-dot failure" title="Experienced failure"/>
          ` : allPassed ? `
            <circle cx="10" cy="10" r="4.5" class="status-dot success" title="Successful execution"/>
          ` : ''}
        </g>
      `;
    });
    nodesLayer.innerHTML = nodesHtml;

    // Attach click listeners to nodes
    nodesLayer.querySelectorAll('.graph-node').forEach(nodeEl => {
      nodeEl.addEventListener('click', e => {
        e.stopPropagation();
        const nodeId = nodeEl.getAttribute('data-node-id');
        selectNode(nodeId);
      });
      nodeEl.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          const nodeId = nodeEl.getAttribute('data-node-id');
          selectNode(nodeId);
        }
      });
    });

    updateStatsBar();
  }

  function buildSplinePath(points) {
    if (!points || points.length === 0) return '';
    if (points.length === 2) {
      return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;
    }
    let d = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
      const pPrev = points[i - 1];
      const pCurr = points[i];
      const midX = (pPrev.x + pCurr.x) / 2;
      const midY = (pPrev.y + pCurr.y) / 2;
      d += ` Q ${pPrev.x} ${pPrev.y}, ${midX} ${midY}`;
    }
    const last = points[points.length - 1];
    d += ` L ${last.x} ${last.y}`;
    return d;
  }

  function selectNode(nodeId) {
    SELECTED_NODE_ID = nodeId;
    const node = GRAPH_DATA.nodes.find(n => n.id === nodeId);
    if (!node) return;

    // Highlight node in DOM
    if (VIEWPORT_EL) {
      VIEWPORT_EL.querySelectorAll('.graph-node').forEach(el => {
        el.classList.toggle('selected', el.getAttribute('data-node-id') === nodeId);
      });
    }

    renderInspector(node);
  }

  function renderInspector(node) {
    if (!INSPECTOR_EL) return;
    INSPECTOR_EL.classList.remove('hidden');

    const occurrences = node.occurrences || [];
    const isLoop = occurrences.length > 1;

    let occTabs = '';
    if (isLoop) {
      occTabs = `
        <div class="inspector-tabs" role="tablist">
          ${occurrences.map((occ, idx) => `
            <button type="button" class="inspector-tab-btn ${idx === 0 ? 'active' : ''}" data-occ-index="${idx}" role="tab">
              Visit ${idx + 1}
              ${occ.outcome === 'failure' ? '<span class="tab-dot error"></span>' : ''}
            </button>
          `).join('')}
        </div>
      `;
    }

    const firstOcc = occurrences[0] || {};
    const inspectorContent = `
      <div class="inspector-header">
        <div class="inspector-title-row">
          <span class="action-badge action-${node.action}">${escapeHtml(node.action)}</span>
          <span class="inspector-tool-label">${escapeHtml(node.tool)}</span>
          ${isLoop ? `<span class="inspector-loop-counter">${occurrences.length} total visits</span>` : ''}
        </div>
        <button type="button" id="inspector-close-btn" class="inspector-close-btn" aria-label="Close inspector">×</button>
      </div>

      <div class="inspector-command-box">
        <div class="inspector-subhead">Command / Target</div>
        <code class="inspector-command-text">${escapeHtml(node.command || node.label)}</code>
      </div>

      ${occTabs}

      <div id="inspector-occ-body" class="inspector-occ-body">
        ${renderOccurrenceDetails(firstOcc, 0, occurrences.length)}
      </div>
    `;

    INSPECTOR_EL.innerHTML = inspectorContent;

    // Hook close button
    const closeBtn = INSPECTOR_EL.querySelector('#inspector-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeInspector);

    // Hook occurrence tab buttons
    INSPECTOR_EL.querySelectorAll('.inspector-tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        INSPECTOR_EL.querySelectorAll('.inspector-tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const idx = parseInt(btn.getAttribute('data-occ-index'), 10);
        const bodyEl = INSPECTOR_EL.querySelector('#inspector-occ-body');
        if (bodyEl && occurrences[idx]) {
          bodyEl.innerHTML = renderOccurrenceDetails(occurrences[idx], idx, occurrences.length);
          hookJumpBtn();
        }
      });
    });

    hookJumpBtn();
  }

  function renderOccurrenceDetails(occ, index, total) {
    const outcomeBadge = occ.outcome === 'success'
      ? '<span class="status-tag status-success">Success</span>'
      : occ.outcome === 'failure'
      ? '<span class="status-tag status-failure">Failed</span>'
      : '<span class="status-tag status-neutral">Neutral</span>';

    return `
      <div class="inspector-occ-meta">
        <div>
          <span class="muted">Occurrence:</span> <strong>${index + 1} of ${total}</strong>
          ${occ.step_idx != null ? `<span class="muted">· Turn</span> <strong>#${occ.step_idx}</strong>` : ''}
        </div>
        <div>${outcomeBadge}</div>
      </div>

      ${occ.thought ? `
        <div class="inspector-card">
          <div class="inspector-subhead">Preceding Thought</div>
          <div class="inspector-thought-text">${escapeHtml(occ.thought)}</div>
        </div>
      ` : ''}

      ${occ.output_text ? `
        <div class="inspector-card">
          <div class="inspector-subhead">Execution Output</div>
          <pre class="inspector-output-pre"><code>${escapeHtml(occ.output_text)}</code></pre>
        </div>
      ` : ''}

      <div class="inspector-actions-row">
        <button type="button" id="inspector-jump-turn" class="btn btn-primary btn-small" data-turn="${occ.step_idx}">
          Jump to Turn #${occ.step_idx} in Timeline
        </button>
      </div>
    `;
  }

  function hookJumpBtn() {
    const jumpBtn = INSPECTOR_EL.querySelector('#inspector-jump-turn');
    if (jumpBtn) {
      jumpBtn.addEventListener('click', () => {
        const turnNum = jumpBtn.getAttribute('data-turn');
        if (turnNum != null && typeof window.jumpToTraceTurn === 'function') {
          window.jumpToTraceTurn(parseInt(turnNum, 10));
        }
      });
    }
  }

  function closeInspector() {
    if (INSPECTOR_EL) INSPECTOR_EL.classList.add('hidden');
    SELECTED_NODE_ID = null;
    if (VIEWPORT_EL) {
      VIEWPORT_EL.querySelectorAll('.graph-node.selected').forEach(el => el.classList.remove('selected'));
    }
  }

  function fitToScreen() {
    if (!SVG_EL || !VIEWPORT_EL) return;
    const bbox = VIEWPORT_EL.getBBox();
    const svgRect = SVG_EL.getBoundingClientRect();

    if (bbox.width === 0 || bbox.height === 0) return;

    const padding = 60;
    const scaleX = (svgRect.width - padding) / bbox.width;
    const scaleY = (svgRect.height - padding) / bbox.height;
    const newScale = Math.min(1.2, Math.max(0.25, Math.min(scaleX, scaleY)));

    transform.scale = newScale;
    transform.x = (svgRect.width - bbox.width * newScale) / 2 - bbox.x * newScale;
    transform.y = 30 - bbox.y * newScale;
    updateTransform();
  }

  function updateStatsBar() {
    const statsEl = document.getElementById('graph-stats-summary');
    if (!statsEl || !GRAPH_DATA || !GRAPH_DATA.stats) return;

    const s = GRAPH_DATA.stats;
    statsEl.innerHTML = `
      <span><strong>${s.unique_nodes || 0}</strong> nodes (${s.total_steps || 0} linear steps)</span>
      <span class="stat-separator">·</span>
      <span><strong>${s.compression_ratio || 0}%</strong> compression</span>
      ${s.loop_edge_count ? `<span class="stat-separator">·</span><span class="badge-loop-stat"><strong>${s.loop_edge_count}</strong> loops</span>` : ''}
      ${s.backtrack_edge_count ? `<span class="stat-separator">·</span><span class="badge-backtrack-stat"><strong>${s.backtrack_edge_count}</strong> backtracks</span>` : ''}
    `;
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function truncate(str, maxLen) {
    const s = String(str || '');
    return s.length <= maxLen ? s : s.slice(0, maxLen).trim() + '…';
  }

  window.GraphView = {
    init,
    fitToScreen,
    renderGraph,
    closeInspector,
  };
})();
