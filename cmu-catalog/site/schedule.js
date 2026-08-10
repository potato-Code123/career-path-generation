/* Schedule tab: a weekly grid for Fall 2026 built from the Schedule of Classes.
   Time conflicts are highlighted but never prevented. */

import {
  state, save, conflicts, toMinutes, fmtTime, offeringsFor, offeringKey, DAY_ORDER,
} from './data.js';
import { openPalette, openSheet, escapeHtml, toast } from './ui.js';

const calendarEl = document.getElementById('calendar');
const pickedEl = document.getElementById('pickedList');
const conflictBar = document.getElementById('conflictBar');
const unscheduledEl = document.getElementById('unscheduled');
const schedSub = document.getElementById('schedSub');

const PALETTE = ['#0071e3', '#34c759', '#ff9500', '#af52de', '#ff2d55', '#5ac8fa', '#ffcc00', '#5856d6'];

export function initSchedule() {
  document.getElementById('addSection').addEventListener('click', pickCourse);
  renderSchedule();
}

function pickCourse() {
  const offeredIds = new Set(state.offerings.map(o => o.id));
  openPalette({
    context: `Fall 2026 · ${offeredIds.size} courses with sections`,
    ids: offeredIds,
    actionLabel: 'Sections',
    onPick: course => pickSection(course.id),
  });
}

function pickSection(id) {
  const sections = offeringsFor(id);
  if (!sections.length) { toast(`${id} has no Fall 2026 sections`); return; }
  openSheet(id, {
    actions: sections.map((s, i) => ({
      label: `${s.section}${s.tba ? ' · TBA' : ` · ${s.days.map(d => d.slice(0, 3)).join('')} ${s.begin}`}`,
      primary: i === 0,
      run: () => addSection(s),
    })),
  });
}

function addSection(offering) {
  const key = offeringKey(offering);
  if (state.picked.includes(key)) { toast('Already on your schedule'); return; }
  state.picked.push(key);
  save();
  renderSchedule();
}

function removeSection(key) {
  state.picked = state.picked.filter(k => k !== key);
  save();
  renderSchedule();
}

function pickedOfferings() {
  return state.picked
    .map(key => {
      const [id, section] = key.split('|');
      return state.offerings.find(o => o.id === id && o.section === section);
    })
    .filter(Boolean);
}

export function renderSchedule() {
  const picked = pickedOfferings();
  const clashes = conflicts(picked);
  const clashing = new Set();
  for (const c of clashes) { clashing.add(offeringKey(c.a)); clashing.add(offeringKey(c.b)); }

  renderRail(picked, clashing);
  renderConflicts(clashes);
  renderCalendar(picked, clashing);

  const units = picked.reduce((sum, o) => sum + (o.units || 0), 0);
  schedSub.textContent = `${picked.length} section${picked.length === 1 ? '' : 's'} · ${units} units`;
}

function colorFor(index) { return PALETTE[index % PALETTE.length]; }

function renderRail(picked, clashing) {
  pickedEl.innerHTML = '';
  if (!picked.length) {
    pickedEl.innerHTML = '<div class="empty">Nothing scheduled yet. Add a section to see it on the grid.</div>';
    return;
  }
  picked.forEach((offering, index) => {
    const key = offeringKey(offering);
    const row = document.createElement('div');
    row.className = 'picked' + (clashing.has(key) ? ' conflict' : '');
    row.innerHTML = `
      <span class="swatch" style="background:${colorFor(index)}"></span>
      <span class="picked-main">
        <span class="picked-id">${offering.id} · ${escapeHtml(offering.section)}</span>
        <span class="picked-meta">${offering.tba ? 'Time TBA' : `${offering.days.map(d => d.slice(0, 3)).join(' ')} ${offering.begin}–${offering.end}`}</span>
      </span>
      <button class="pill-x" aria-label="Remove">✕</button>`;
    row.querySelector('.pill-x').addEventListener('click', e => { e.stopPropagation(); removeSection(key); });
    row.addEventListener('click', () => openSheet(offering.id, {
      actions: [{ label: 'Remove from schedule', run: () => removeSection(key) }],
    }));
    pickedEl.appendChild(row);
  });
}

function renderConflicts(clashes) {
  if (!clashes.length) { conflictBar.hidden = true; return; }
  conflictBar.hidden = false;
  conflictBar.innerHTML = clashes.map(c =>
    `<div><b>${c.a.id} ${escapeHtml(c.a.section)}</b> overlaps <b>${c.b.id} ${escapeHtml(c.b.section)}</b> on ${c.days.map(d => d.slice(0, 3)).join(', ')} — kept on the grid so you can decide.</div>`
  ).join('');
}

function renderCalendar(picked, clashing) {
  const scheduled = picked.filter(o => !o.tba && o.days.length && toMinutes(o.begin) != null);
  const unscheduled = picked.filter(o => !scheduled.includes(o));

  const usedDays = new Set();
  for (const o of scheduled) for (const d of o.days) usedDays.add(d);
  const days = DAY_ORDER.filter(d =>
    usedDays.has(d) || ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'].includes(d)
  );

  let min = 8 * 60, max = 18 * 60;
  for (const o of scheduled) {
    min = Math.min(min, toMinutes(o.begin));
    max = Math.max(max, toMinutes(o.end));
  }
  min = Math.floor(min / 60) * 60;
  max = Math.ceil(max / 60) * 60;
  const hours = (max - min) / 60;
  const PX = 46;

  const grid = document.createElement('div');
  grid.className = 'cal-grid';
  grid.style.setProperty('--cols', String(days.length));

  grid.innerHTML = `<div class="cal-corner"></div>` +
    days.map(d => `<div class="cal-day">${d.slice(0, 3)}</div>`).join('');

  const hoursCol = document.createElement('div');
  hoursCol.className = 'cal-hours';
  for (let h = 0; h < hours; h++) {
    const cell = document.createElement('div');
    cell.className = 'cal-hour';
    cell.textContent = fmtTime(min + h * 60).replace(':00', '');
    hoursCol.appendChild(cell);
  }
  grid.appendChild(hoursCol);

  days.forEach(day => {
    const col = document.createElement('div');
    col.className = 'cal-col';
    for (let h = 0; h < hours; h++) {
      const slot = document.createElement('div');
      slot.className = 'cal-slot';
      col.appendChild(slot);
    }
    // Overlapping sections must sit side by side, otherwise a conflict hides
    // the very course it conflicts with.
    const dayEvents = [];
    picked.forEach((offering, index) => {
      if (offering.tba || !offering.days.includes(day)) return;
      const start = toMinutes(offering.begin), end = toMinutes(offering.end);
      if (start == null || end == null) return;
      dayEvents.push({ offering, index, start, end });
    });
    dayEvents.sort((a, b) => a.start - b.start || a.end - b.end);

    const lanes = [];                       // lane -> end minute of last event
    for (const item of dayEvents) {
      let lane = lanes.findIndex(endsAt => endsAt <= item.start);
      if (lane === -1) { lanes.push(item.end); lane = lanes.length - 1; }
      else lanes[lane] = item.end;
      item.lane = lane;
    }
    const laneCount = Math.max(lanes.length, 1);

    dayEvents.forEach(({ offering, index, start, end, lane }) => {
      const key = offeringKey(offering);
      const event = document.createElement('div');
      event.className = 'event' + (clashing.has(key) ? ' conflict' : '');
      event.style.top = `${((start - min) / 60) * PX}px`;
      event.style.height = `${Math.max(((end - start) / 60) * PX - 2, 20)}px`;
      if (laneCount > 1) {
        const width = 100 / laneCount;
        event.style.left = `calc(${lane * width}% + 3px)`;
        event.style.right = 'auto';
        event.style.width = `calc(${width}% - 6px)`;
      }
      if (!clashing.has(key)) {
        event.style.borderLeftColor = colorFor(index);
        event.style.background = colorFor(index) + '1f';
      }
      event.innerHTML = `<b>${offering.id}</b><span>${escapeHtml(offering.section)} · ${offering.begin}</span>`;
      event.addEventListener('click', () => openSheet(offering.id, {
        actions: [{ label: 'Remove from schedule', run: () => removeSection(key) }],
      }));
      col.appendChild(event);
    });
    grid.appendChild(col);
  });

  calendarEl.innerHTML = '';
  calendarEl.appendChild(grid);

  unscheduledEl.innerHTML = unscheduled.length
    ? `<b>No meeting time published:</b><ul>${unscheduled.map(o => `<li>${o.id} ${escapeHtml(o.section)} — ${escapeHtml(o.loc)}</li>`).join('')}</ul>`
    : '';
}
