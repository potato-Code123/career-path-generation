/* Data loading, indexes, and the two pieces of real logic:
   prerequisite evaluation and meeting-time conflicts. */

export const state = {
  courses: [],
  byId: new Map(),
  degrees: [],
  offerings: [],
  offeringsBySemester: 'F26',
  geneds: [],
  descriptions: null,          // lazily fetched
  degreeId: null,
  plan: emptyPlan(),           // 8 terms of course ids
  picked: [],                  // schedule: {id, section}
  school: null,
};

export const TERMS = [
  'Year 1 · Fall', 'Year 1 · Spring',
  'Year 2 · Fall', 'Year 2 · Spring',
  'Year 3 · Fall', 'Year 3 · Spring',
  'Year 4 · Fall', 'Year 4 · Spring',
];

function emptyPlan() { return Array.from({ length: 8 }, () => []); }

/* ── loading ─────────────────────────────────────────────── */

async function json(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

export async function load() {
  const [courses, degrees, offerings, geneds] = await Promise.all([
    json('data/courses.json'),
    json('data/degrees.json'),
    json('data/offerings.json'),
    json('data/geneds.json'),
  ]);
  state.courses = courses;
  state.byId = new Map(courses.map(c => [c.id, c]));
  state.degrees = degrees;
  state.offerings = offerings.rows;
  state.offeringsBySemester = offerings.semester;
  state.geneds = geneds;
  restore();

  // Descriptions are 3.4 MB and only needed once a course sheet opens.
  json('data/descriptions.json').then(d => { state.descriptions = d; });
}

export async function description(id) {
  if (!state.descriptions) state.descriptions = await json('data/descriptions.json');
  return state.descriptions[id] || '';
}

/* ── persistence ─────────────────────────────────────────── */

const KEY = 'cmu-planner-v1';

export function save() {
  localStorage.setItem(KEY, JSON.stringify({
    degreeId: state.degreeId, plan: state.plan, picked: state.picked, school: state.school,
  }));
}

function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
    if (Array.isArray(saved.plan) && saved.plan.length === 8) state.plan = saved.plan;
    if (Array.isArray(saved.picked)) state.picked = saved.picked;
    if (saved.degreeId && state.degrees.some(d => d.id === saved.degreeId)) state.degreeId = saved.degreeId;
    if (saved.school) state.school = saved.school;
  } catch { /* first visit */ }
  if (!state.degreeId) {
    const cs = state.degrees.find(d => /B\.S\. in Computer Science/i.test(d.credential));
    state.degreeId = cs ? cs.id : (state.degrees[0] && state.degrees[0].id);
  }
}

export function degree() {
  return state.degrees.find(d => d.id === state.degreeId) || null;
}

/* ── prerequisites ───────────────────────────────────────── */

/* Evaluate a prerequisite tree against the set of courses taken *before* a
   term. Returns {ok, unknown}: `unknown` means the requirement contains a
   prose condition ("senior standing") that cannot be checked automatically,
   so the UI warns rather than claiming a violation. */
export function satisfies(tree, taken) {
  if (!tree) return { ok: true, unknown: false };
  if (tree.course) return { ok: taken.has(tree.course), unknown: false };
  if (tree.text) return { ok: true, unknown: true };
  if (tree.and) {
    const parts = tree.and.map(t => satisfies(t, taken));
    return { ok: parts.every(p => p.ok), unknown: parts.some(p => p.unknown) };
  }
  if (tree.or) {
    const parts = tree.or.map(t => satisfies(t, taken));
    return { ok: parts.some(p => p.ok), unknown: parts.some(p => p.unknown) && !parts.some(p => p.ok) };
  }
  return { ok: true, unknown: false };
}

/* Courses scheduled strictly before term index `i`. */
export function takenBefore(i) {
  const taken = new Set();
  for (let t = 0; t < i; t++) for (const id of state.plan[t]) taken.add(id);
  return taken;
}

export function prereqStatus(id, termIndex) {
  const course = state.byId.get(id);
  if (!course || !course.pre.tree) return { ok: true, unknown: false, missing: [] };
  const taken = takenBefore(termIndex);
  const result = satisfies(course.pre.tree, taken);
  const missing = course.pre.courses.filter(c => !taken.has(c));
  return { ...result, missing };
}

/* ── requirements ────────────────────────────────────────── */

export function plannedSet() {
  const all = new Set();
  for (const term of state.plan) for (const id of term) all.add(id);
  return all;
}

/* How many of a group's courses are in the plan, and whether that satisfies
   the group's (prose-derived) rule. */
export function groupProgress(group, planned) {
  const ids = new Set();
  for (const row of group.courses) {
    ids.add(row.id);
    for (const alt of row.alt) ids.add(alt);
  }
  const met = [...ids].filter(id => planned.has(id));

  // Alternatives are one requirement with several satisfiers, so count rows.
  const rowsMet = group.courses.filter(
    row => planned.has(row.id) || row.alt.some(a => planned.has(a))
  ).length;

  const rule = group.rule || { kind: 'any' };
  let need = null, done = false;
  if (rule.kind === 'all') { need = group.courses.length; done = rowsMet >= need; }
  else if (rule.kind === 'choose') { need = rule.n; done = rowsMet >= need; }
  else { need = null; done = rowsMet > 0; }

  return { met, rowsMet, need, done, total: group.courses.length };
}

/* ── meeting times ───────────────────────────────────────── */

export const DAY_ORDER = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

export function toMinutes(text) {
  if (!text) return null;
  const m = /^(\d{1,2}):(\d{2})(AM|PM)$/i.exec(text.trim());
  if (!m) return null;
  let hour = parseInt(m[1], 10) % 12;
  if (/pm/i.test(m[3])) hour += 12;
  return hour * 60 + parseInt(m[2], 10);
}

export function fmtTime(minutes) {
  const h24 = Math.floor(minutes / 60), m = minutes % 60;
  const h = h24 % 12 === 0 ? 12 : h24 % 12;
  return `${h}:${String(m).padStart(2, '0')} ${h24 < 12 ? 'AM' : 'PM'}`;
}

export function offeringKey(o) { return `${o.id}|${o.section}`; }

/* Pairs of picked sections that overlap in day and time. Conflicts are
   reported, never prevented — a student may deliberately plan around one. */
export function conflicts(list) {
  const pairs = [];
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j];
      if (a.tba || b.tba) continue;
      const shared = a.days.filter(d => b.days.includes(d));
      if (!shared.length) continue;
      const a1 = toMinutes(a.begin), a2 = toMinutes(a.end);
      const b1 = toMinutes(b.begin), b2 = toMinutes(b.end);
      if (a1 == null || b1 == null) continue;
      if (a1 < b2 && b1 < a2) pairs.push({ a, b, days: shared });
    }
  }
  return pairs;
}

/* ── search ──────────────────────────────────────────────── */

export function search(query, limit = 60) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const compact = q.replace(/[^a-z0-9]/g, '');
  const scored = [];
  for (const course of state.courses) {
    const id = course.id.toLowerCase();
    const idCompact = id.replace('-', '');
    const name = course.name.toLowerCase();
    let score = -1;
    if (idCompact === compact) score = 0;
    else if (idCompact.startsWith(compact) && compact.length >= 2) score = 1;
    else if (name.startsWith(q)) score = 2;
    else if (name.includes(q)) score = 3;
    else if (id.includes(q)) score = 4;
    if (score >= 0) scored.push([score, course]);
  }
  scored.sort((x, y) => x[0] - y[0] || x[1].id.localeCompare(y[1].id));
  return scored.slice(0, limit).map(s => s[1]);
}

export function offeringsFor(id) {
  return state.offerings.filter(o => o.id === id);
}
