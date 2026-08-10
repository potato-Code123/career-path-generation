/* Shared UI: toast, the course detail sheet, and the search palette. */

import { state, search, description, offeringsFor, prereqStatus, TERMS, save } from './data.js';
import { prereqGraph } from './graph.js';

/* ── toast ───────────────────────────────────────────────── */
let toastTimer;
export function toast(message) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2200);
}

/* ── search palette ──────────────────────────────────────── */
const scrim = document.getElementById('paletteScrim');
const input = document.getElementById('paletteInput');
const results = document.getElementById('paletteResults');
const contextBar = document.getElementById('paletteContext');
let picker = null;      // {context, ids, onPick, actionLabel}
let cursor = 0;
let current = [];

export function openPalette(options = {}) {
  picker = options;
  cursor = 0;
  contextBar.hidden = !options.context;
  if (options.context) contextBar.textContent = options.context;
  input.value = '';
  scrim.hidden = false;
  input.focus();
  renderResults();
}

export function closePalette() { scrim.hidden = true; picker = null; }

function candidates(query) {
  if (picker && picker.ids) {
    const set = picker.ids;
    const pool = [...set].map(id => state.byId.get(id)).filter(Boolean);
    if (!query.trim()) return pool.slice(0, 200);
    const q = query.trim().toLowerCase();
    return pool.filter(c => c.id.toLowerCase().includes(q) || c.name.toLowerCase().includes(q)).slice(0, 200);
  }
  return search(query);
}

function renderResults() {
  current = candidates(input.value);
  results.innerHTML = '';
  if (!current.length) {
    results.innerHTML = `<div class="empty">${input.value.trim() ? 'No matching courses.' : 'Start typing to search 4,263 courses.'}</div>`;
    return;
  }
  current.forEach((course, i) => {
    const row = document.createElement('div');
    row.className = 'result';
    row.setAttribute('aria-selected', String(i === cursor));
    const offered = offeringsFor(course.id).length;
    row.innerHTML = `
      <span class="result-id">${course.id}</span>
      <span class="result-main">
        <span class="result-name">${escapeHtml(course.name)}</span>
        <span class="result-meta">${course.units != null ? course.units + ' units' : 'variable units'}${course.sem.length ? ' · ' + course.sem.join(', ') : ''}${offered ? ' · F26' : ''}</span>
      </span>
      ${picker && picker.onPick ? `<span class="result-add">${picker.actionLabel || 'Add'}</span>` : ''}`;
    row.addEventListener('click', () => choose(course));
    row.addEventListener('mousemove', () => { cursor = i; markCursor(); });
    results.appendChild(row);
  });
}

function markCursor() {
  [...results.children].forEach((el, i) => el.setAttribute('aria-selected', String(i === cursor)));
}

function choose(course) {
  if (picker && picker.onPick) { picker.onPick(course); closePalette(); }
  else { closePalette(); openSheet(course.id); }
}

input.addEventListener('input', () => { cursor = 0; renderResults(); });
document.getElementById('paletteClose').addEventListener('click', closePalette);
scrim.addEventListener('mousedown', e => { if (e.target === scrim) closePalette(); });
input.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closePalette(); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); cursor = Math.min(cursor + 1, current.length - 1); markCursor(); scrollCursor(); }
  if (e.key === 'ArrowUp') { e.preventDefault(); cursor = Math.max(cursor - 1, 0); markCursor(); scrollCursor(); }
  if (e.key === 'Enter' && current[cursor]) { e.preventDefault(); choose(current[cursor]); }
});
function scrollCursor() {
  const el = results.children[cursor];
  if (el) el.scrollIntoView({ block: 'nearest' });
}

/* ── course sheet ────────────────────────────────────────── */
const sheet = document.getElementById('sheet');
const sheetScrim = document.getElementById('sheetScrim');
let onSheetChange = null;

export function setSheetChangeHandler(fn) { onSheetChange = fn; }

export function closeSheet() { sheet.hidden = true; sheetScrim.hidden = true; }
sheetScrim.addEventListener('click', closeSheet);

export async function openSheet(courseId, options = {}) {
  const course = state.byId.get(courseId);
  if (!course) { toast(`${courseId} is not in the catalog`); return; }

  sheet.hidden = false;
  sheetScrim.hidden = false;
  sheet.scrollTop = 0;
  sheet.innerHTML = `
    <div class="sheet-head">
      <div>
        <div class="sheet-title">${course.id}</div>
        <div class="sheet-name">${escapeHtml(course.name)}</div>
      </div>
      <button class="sheet-close" aria-label="Close">✕</button>
    </div>
    <div class="sheet-body"><div class="empty">Loading…</div></div>`;
  sheet.querySelector('.sheet-close').addEventListener('click', closeSheet);

  const desc = await description(courseId);
  const body = sheet.querySelector('.sheet-body');
  const sections = offeringsFor(courseId);

  const chips = [];
  chips.push(`<span class="chip">${course.units != null ? course.units + ' units' : 'Variable units'}</span>`);
  if (course.sem.length) chips.push(`<span class="chip">${course.sem.join(' · ')}</span>`);
  chips.push(`<span class="chip">${sections.length ? `${sections.length} F26 section${sections.length > 1 ? 's' : ''}` : 'Not offered F26'}</span>`);

  body.innerHTML = `
    <div class="meta-row">${chips.join('')}</div>
    ${options.termIndex != null ? renderPrereqStatus(courseId, options.termIndex) : ''}
    <div class="sheet-actions" id="sheetActions"></div>
    ${desc ? `<div class="sheet-section"><h4>Description</h4><p class="desc">${escapeHtml(desc)}</p></div>` : ''}
    <div class="sheet-section">
      <h4>Prerequisites</h4>
      ${course.pre.raw
        ? `<div class="prereq-raw">${escapeHtml(course.pre.raw)}</div>
           ${course.pre.review ? '<p class="note">This requirement mixes conditions the parser could not group with confidence — read the original wording above.</p>' : ''}`
        : '<p class="desc">None listed.</p>'}
      <div class="graph-wrap" id="graphWrap" hidden></div>
    </div>
    ${sections.length ? `
      <div class="sheet-section">
        <h4>Fall 2026 sections</h4>
        ${sections.map(s => `
          <div class="picked" data-section="${escapeAttr(s.section)}">
            <div class="picked-main">
              <div class="picked-id">${escapeHtml(s.section)}</div>
              <div class="picked-meta">${s.tba ? 'Time TBA' : `${s.days.map(d => d.slice(0, 3)).join(' ')} · ${s.begin}–${s.end}`} · ${escapeHtml(s.loc)}</div>
            </div>
          </div>`).join('')}
      </div>` : ''}`;

  const graph = prereqGraph(courseId);
  if (graph) {
    const wrap = body.querySelector('#graphWrap');
    wrap.hidden = false;
    wrap.appendChild(graph);
    wrap.addEventListener('click', e => {
      const node = e.target.closest('[data-course]');
      if (node && node.dataset.course !== courseId) openSheet(node.dataset.course, options);
    });
  }

  const actions = body.querySelector('#sheetActions');
  if (options.actions) {
    for (const action of options.actions) {
      const button = document.createElement('button');
      button.className = action.primary ? 'btn' : 'btn-quiet';
      button.style.width = 'auto';
      button.textContent = action.label;
      button.addEventListener('click', () => { action.run(); closeSheet(); if (onSheetChange) onSheetChange(); });
      actions.appendChild(button);
    }
  }
}

function renderPrereqStatus(courseId, termIndex) {
  const status = prereqStatus(courseId, termIndex);
  if (status.ok && !status.unknown) return '';
  if (!status.ok) {
    return `<div class="conflict-bar" style="margin-bottom:16px">Missing before ${TERMS[termIndex]}: ${status.missing.join(', ')}</div>`;
  }
  return `<div class="conflict-bar" style="margin-bottom:16px">Has a condition that can't be checked automatically (e.g. standing or permission).</div>`;
}

/* ── helpers ─────────────────────────────────────────────── */
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
export function escapeAttr(value) { return escapeHtml(value); }

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { if (!scrim.hidden) closePalette(); else if (!sheet.hidden) closeSheet(); }
  if (e.key === '/' && scrim.hidden && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'SELECT') {
    e.preventDefault(); openPalette();
  }
});
