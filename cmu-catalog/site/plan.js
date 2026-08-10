/* Degree tab: eight terms, a requirement checklist that ticks itself off, and
   prerequisite checking against everything scheduled in earlier terms. */

import {
  state, TERMS, degree, save, plannedSet, groupProgress, prereqStatus,
} from './data.js';
import { openPalette, openSheet, escapeHtml, toast } from './ui.js';

const yearsEl = document.getElementById('years');
const reqListEl = document.getElementById('reqList');
const degreeSelect = document.getElementById('degreeSelect');
const summaryEl = document.getElementById('progressSummary');
const planSub = document.getElementById('planSub');

export function initPlan() {
  degreeSelect.innerHTML = state.degrees
    .map(d => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.program)} — ${escapeHtml(d.credential)}</option>`)
    .join('');
  degreeSelect.value = state.degreeId;
  degreeSelect.addEventListener('change', () => {
    state.degreeId = degreeSelect.value;
    save();
    renderPlan();
    document.dispatchEvent(new CustomEvent('degree-changed'));
  });

  document.getElementById('clearPlan').addEventListener('click', () => {
    if (!confirm('Remove every course from all eight terms?')) return;
    state.plan = Array.from({ length: 8 }, () => []);
    save();
    renderPlan();
  });

  renderPlan();
}

export function addCourse(termIndex, id) {
  if (state.plan.some(term => term.includes(id))) { toast(`${id} is already in your plan`); return; }
  state.plan[termIndex].push(id);
  save();
  renderPlan();
}

function removeCourse(termIndex, id) {
  state.plan[termIndex] = state.plan[termIndex].filter(c => c !== id);
  save();
  renderPlan();
}

export function renderPlan() {
  renderTerms();
  renderRequirements();
}

/* ── terms ───────────────────────────────────────────────── */

function renderTerms() {
  yearsEl.innerHTML = '';
  let totalUnits = 0;

  for (let year = 0; year < 4; year++) {
    const wrap = document.createElement('div');
    wrap.className = 'year';
    wrap.innerHTML = `<div class="year-label">Year ${year + 1}</div>`;
    const row = document.createElement('div');
    row.className = 'year-row';

    for (let half = 0; half < 2; half++) {
      const index = year * 2 + half;
      const ids = state.plan[index];
      const units = ids.reduce((sum, id) => {
        const course = state.byId.get(id);
        return sum + (course && course.units ? course.units : 0);
      }, 0);
      totalUnits += units;

      const term = document.createElement('div');
      term.className = 'term';
      term.innerHTML = `
        <div class="term-head">
          <span class="term-name">${TERMS[index].split(' · ')[1]}</span>
          <span class="term-units">${units || 0} units</span>
        </div>
        <div class="term-list"></div>
        <button class="term-add">Add course</button>`;

      const list = term.querySelector('.term-list');
      for (const id of ids) list.appendChild(coursePill(id, index));

      term.querySelector('.term-add').addEventListener('click', () => {
        openPalette({
          context: `Adding to ${TERMS[index]}`,
          actionLabel: 'Add',
          onPick: course => addCourse(index, course.id),
        });
      });
      row.appendChild(term);
    }
    wrap.appendChild(row);
    yearsEl.appendChild(wrap);
  }

  planSub.textContent = `Eight semesters · ${plannedSet().size} courses · ${totalUnits} units planned`;
}

function coursePill(id, termIndex) {
  const course = state.byId.get(id);
  const status = prereqStatus(id, termIndex);
  const pill = document.createElement('div');
  pill.className = 'pill' + (!status.ok ? ' bad' : status.unknown ? ' unknown' : '');

  const flag = !status.ok
    ? `<span class="pill-flag flag-bad">prereq</span>`
    : status.unknown ? `<span class="pill-flag flag-warn">check</span>` : '';

  pill.innerHTML = `
    <span class="pill-id">${id}</span>
    <span class="pill-name">${escapeHtml(course ? course.name : 'Not in catalog')}</span>
    ${flag}
    <span class="pill-units">${course && course.units != null ? course.units : '—'}</span>
    <button class="pill-x" aria-label="Remove ${id}">✕</button>`;

  pill.querySelector('.pill-x').addEventListener('click', e => {
    e.stopPropagation();
    removeCourse(termIndex, id);
  });
  pill.addEventListener('click', () => {
    openSheet(id, {
      termIndex,
      actions: [{ label: 'Remove from plan', run: () => removeCourse(termIndex, id) }],
    });
  });
  return pill;
}

/* ── requirements ────────────────────────────────────────── */

function renderRequirements() {
  const deg = degree();
  reqListEl.innerHTML = '';
  if (!deg) { reqListEl.innerHTML = '<div class="empty">No degree selected.</div>'; return; }

  const planned = plannedSet();
  let done = 0, checkable = 0;
  const bySection = new Map();
  for (const group of deg.groups) {
    const key = group.section || 'Requirements';
    if (!bySection.has(key)) bySection.set(key, []);
    bySection.get(key).push(group);
  }

  for (const [section, groups] of bySection) {
    const heading = document.createElement('div');
    heading.className = 'req-section';
    heading.textContent = section;
    reqListEl.appendChild(heading);

    for (const group of groups) {
      const label = group.caption || section;

      // Some groups are prose only — "Two School of Computer Science
      // electives", with the qualifying set described in words rather than
      // listed. They can't be ticked off, so they're shown for reference and
      // left out of the tally instead of sitting permanently at 0/0.
      if (!group.courses.length) {
        const note = document.createElement('div');
        note.className = 'req req-info';
        note.innerHTML = `
          <div class="req-top">
            <span class="req-name">${escapeHtml(label)}</span>
            <span class="req-count">note</span>
          </div>
          <div class="req-rule">${escapeHtml((group.notes[0] || 'No course list published for this requirement.').slice(0, 220))}</div>`;
        reqListEl.appendChild(note);
        continue;
      }

      const progress = groupProgress(group, planned);
      if (progress.done) done++;
      checkable++;

      const el = document.createElement('div');
      el.className = 'req' + (progress.done ? ' done' : '');
      const counter = progress.need
        ? `${progress.rowsMet}/${progress.need}`
        : `${progress.rowsMet}/${progress.total}`;

      el.innerHTML = `
        <div class="req-top">
          <span class="req-check"></span>
          <span class="req-name">${escapeHtml(label)}</span>
          <span class="req-count">${counter}</span>
        </div>
        <div class="req-rule">${ruleText(group, progress)}</div>`;

      el.addEventListener('click', () => openRequirement(group, label));
      reqListEl.appendChild(el);
    }
  }

  const total = checkable;
  const pct = total ? done / total : 0;
  summaryEl.innerHTML = `
    <svg class="ring" viewBox="0 0 40 40">
      <circle class="track" cx="20" cy="20" r="16"></circle>
      <circle class="fill" cx="20" cy="20" r="16"
        stroke-dasharray="${(pct * 100.5).toFixed(1)} 100.5"
        transform="rotate(-90 20 20)"></circle>
    </svg>
    <span class="progress-text"><b>${done} of ${total}</b>requirement groups met</span>`;
}

function ruleText(group, progress) {
  if (group.rule.kind === 'all') return `All ${progress.total} required`;
  if (group.rule.kind === 'choose') return `Choose ${group.rule.n} of ${progress.total}`;
  if (group.units) return `${group.units} units · ${progress.total} options`;
  return `${progress.total} options`;
}

/* Pick a course that satisfies one requirement — the "I have a math
   requirement, show me what counts" flow. */
function openRequirement(group, label) {
  const ids = new Set();
  for (const row of group.courses) {
    ids.add(row.id);
    for (const alt of row.alt) ids.add(alt);
  }
  const known = new Set([...ids].filter(id => state.byId.get(id)));
  if (!known.size) { toast('No catalog courses listed for this requirement'); return; }

  openPalette({
    context: `${label} — ${known.size} qualifying course${known.size > 1 ? 's' : ''}. Pick one, then choose a term.`,
    ids: known,
    actionLabel: 'Choose',
    onPick: course => chooseTerm(course.id),
  });
}

function chooseTerm(id) {
  const existing = state.plan.findIndex(term => term.includes(id));
  if (existing >= 0) { toast(`${id} is already in ${TERMS[existing]}`); return; }

  openSheet(id, {
    actions: TERMS.map((name, index) => ({
      label: name.replace('Year ', 'Y').replace(' · ', ' '),
      primary: index === 0,
      run: () => addCourse(index, id),
    })),
  });
}
