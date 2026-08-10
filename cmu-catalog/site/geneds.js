/* Gen Eds tab: categories for the school implied by your degree, defaulting to
   the courses that actually run in Fall 2026. */

import { state, save, degree } from './data.js';
import { openSheet, escapeHtml } from './ui.js';

const gridEl = document.getElementById('genedGrid');
const schoolSelect = document.getElementById('schoolSelect');
const f26Toggle = document.getElementById('f26Only');
const subEl = document.getElementById('genedSub');

/* Map a degree's program name onto the school that owns its gen eds. */
const SCHOOL_PATTERNS = [
  [/computer science|artificial intelligence|robotics|human-computer|computational biology|machine learning|language technolog|software|scs/i, 'SCS'],
  [/engineering|biomedical|chemical|civil|electrical|mechanical|materials science|integrated innovation|cit/i, 'CIT'],
  [/mellon college|biological sciences|chemistry|mathematical sciences|physics|mcs/i, 'MCS'],
  [/dietrich|humanities|social sciences|english|history|philosophy|psychology|statistics|economics|information systems|languages/i, 'Dietrich'],
];

export function schoolForDegree() {
  const deg = degree();
  if (!deg) return 'SCS';
  const haystack = `${deg.program} ${deg.credential}`;
  for (const [pattern, school] of SCHOOL_PATTERNS) if (pattern.test(haystack)) return school;
  return 'SCS';
}

export function initGeneds() {
  const schools = [...new Set(state.geneds.map(g => g.school))].sort();
  schoolSelect.innerHTML = schools.map(s => `<option value="${s}">${s}</option>`).join('');
  if (!state.school) state.school = schoolForDegree();
  schoolSelect.value = state.school;

  schoolSelect.addEventListener('change', () => {
    state.school = schoolSelect.value;
    save();
    renderGeneds();
  });
  f26Toggle.addEventListener('change', renderGeneds);

  // Following the degree is the useful default, but an explicit choice sticks.
  document.addEventListener('degree-changed', () => {
    state.school = schoolForDegree();
    schoolSelect.value = state.school;
    save();
    renderGeneds();
  });

  renderGeneds();
}

export function renderGeneds() {
  const school = schoolSelect.value || state.school;
  const onlyF26 = f26Toggle.checked;
  const categories = state.geneds.filter(g => g.school === school);

  const totalF26 = categories.reduce((sum, c) => sum + c.f26Count, 0);
  const deg = degree();
  subEl.textContent = `${school} · ${categories.length} categories · ${totalF26} courses running in ${state.offeringsBySemester}`
    + (deg ? ` · matched to ${deg.credential}` : '');

  gridEl.innerHTML = '';
  if (!categories.length) {
    gridEl.innerHTML = '<div class="empty">No categories for this school.</div>';
    return;
  }

  for (const category of categories) {
    const courses = onlyF26 ? category.courses.filter(c => c.f26) : category.courses;
    const card = document.createElement('div');
    card.className = 'gened-card' + (category.listType === 'excluded' ? ' excluded' : '');

    const badge = category.listType === 'excluded'
      ? '<span class="badge badge-excluded">Does not count</span>'
      : category.listType === 'added'
        ? '<span class="badge badge-added">Recently added</span>'
        : '<span class="badge badge-approved">Approved</span>';

    card.innerHTML = `
      <div class="gened-card-head">
        ${badge}
        <h3>${escapeHtml(category.category)}</h3>
        ${category.rule ? `<p class="gened-rule">${escapeHtml(category.rule)}</p>` : ''}
        ${category.timeline ? `<p class="gened-rule">${escapeHtml(category.timeline)}</p>` : ''}
        <p class="gened-rule">${courses.length} course${courses.length === 1 ? '' : 's'}${onlyF26 ? ` offered in ${state.offeringsBySemester}` : ' listed'}${!onlyF26 && category.f26Count ? ` · ${category.f26Count} this fall` : ''}</p>
      </div>
      <div class="gened-list"></div>`;

    const list = card.querySelector('.gened-list');
    if (!category.available) {
      list.innerHTML = `<div class="empty">${escapeHtml(category.reason || 'No course list published.')}</div>`;
    } else if (!courses.length) {
      list.innerHTML = `<div class="empty">${onlyF26 ? `Nothing from this category runs in ${state.offeringsBySemester}.` : 'No courses listed.'}</div>`;
    } else {
      for (const course of courses) {
        const row = document.createElement('div');
        row.className = 'gened-row';
        row.innerHTML = `
          ${course.f26 ? '<span class="dot" title="Offered in Fall 2026"></span>' : '<span class="dot" style="background:transparent"></span>'}
          <span class="id">${course.id}</span>
          <span class="t">${escapeHtml(course.title || (state.byId.get(course.id) || {}).name || '')}</span>`;
        row.addEventListener('click', () => openSheet(course.id));
        list.appendChild(row);
      }
    }
    gridEl.appendChild(card);
  }
}
