/* Shell: tab switching and boot. */

import { load } from './data.js';
import { openPalette, setSheetChangeHandler } from './ui.js';
import { initPlan, renderPlan } from './plan.js';
import { initSchedule, renderSchedule } from './schedule.js';
import { initGeneds, renderGeneds } from './geneds.js';

const tabs = [...document.querySelectorAll('.seg')];
const thumb = document.querySelector('.seg-thumb');
const panels = [...document.querySelectorAll('.tabpanel')];

function moveThumb(button) {
  thumb.style.width = `${button.offsetWidth}px`;
  thumb.style.transform = `translateX(${button.offsetLeft - 2}px)`;
}

function show(name) {
  tabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === name)));
  panels.forEach(p => { p.hidden = p.dataset.panel !== name; });
  const active = tabs.find(t => t.dataset.tab === name);
  if (active) moveThumb(active);
  if (name === 'schedule') renderSchedule();
  if (name === 'geneds') renderGeneds();
  history.replaceState(null, '', `#${name}`);
}

tabs.forEach(tab => tab.addEventListener('click', () => show(tab.dataset.tab)));
document.getElementById('searchTrigger').addEventListener('click', () => openPalette());
window.addEventListener('resize', () => {
  const active = tabs.find(t => t.getAttribute('aria-selected') === 'true');
  if (active) moveThumb(active);
});

(async function boot() {
  try {
    await load();
  } catch (error) {
    document.querySelector('main').innerHTML =
      `<div class="empty">Could not load course data (${error.message}). Serve this folder over HTTP rather than opening the file directly.</div>`;
    return;
  }

  initPlan();
  initSchedule();
  initGeneds();

  // Any sheet action can change the plan or schedule; re-render both.
  setSheetChangeHandler(() => { renderPlan(); renderSchedule(); });

  const initial = (location.hash || '#plan').slice(1);
  show(tabs.some(t => t.dataset.tab === initial) ? initial : 'plan');
})();
