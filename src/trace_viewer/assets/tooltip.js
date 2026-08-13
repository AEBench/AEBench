
(function () {
  let pop = null;
  let currentTrigger = null;

  function ensurePop() {
    if (pop) return pop;
    pop = document.createElement('div');
    pop.className = 'tt-pop';
    pop.setAttribute('role', 'tooltip');
    document.body.appendChild(pop);
    return pop;
  }

  function show(trigger) {
    const text = trigger.getAttribute('data-tip');
    if (!text) return;
    const el = ensurePop();
    el.textContent = text;
    position(trigger);
    el.classList.add('tt-show');
  }

  function position(trigger) {
    if (!pop) return;
    const tRect = trigger.getBoundingClientRect();
    const pRect = { width: pop.offsetWidth, height: pop.offsetHeight };
    const margin = 8;

    let top = tRect.top - pRect.height - margin;
    let arrowPos = 'below';     // arrow is on the bottom edge of the tooltip
    if (top < margin) {
      top = tRect.bottom + margin;
      arrowPos = 'above';       // arrow on the top edge
    }

    let left = tRect.left + tRect.width / 2 - pRect.width / 2;
    const minLeft = margin;
    const maxLeft = window.innerWidth - pRect.width - margin;
    if (maxLeft < minLeft) {
      left = minLeft;
    } else {
      left = Math.max(minLeft, Math.min(maxLeft, left));
    }

    const triggerCenterX = tRect.left + tRect.width / 2;
    const rawArrowX = triggerCenterX - left;
    const arrowX = Math.max(14, Math.min(pRect.width - 14, rawArrowX));

    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.dataset.arrowPos = arrowPos;
    pop.style.setProperty('--tt-arrow-x', arrowX + 'px');
  }

  function hide() {
    if (pop) pop.classList.remove('tt-show');
    currentTrigger = null;
  }

  document.addEventListener('mouseover', (e) => {
    const trigger = e.target.closest('[data-tip]');
    if (!trigger) return;
    if (trigger !== currentTrigger) {
      currentTrigger = trigger;
      show(trigger);
    }
  });

  document.addEventListener('mouseout', (e) => {
    const trigger = e.target.closest('[data-tip]');
    if (!trigger) return;
    if (e.relatedTarget && trigger.contains(e.relatedTarget)) return;
    if (trigger === currentTrigger) hide();
  });

  window.addEventListener('scroll', hide, { capture: true, passive: true });
  window.addEventListener('resize', hide, { passive: true });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
})();
