/* Prerequisite graph.

   The requirement is a boolean expression, not a flat list, so the drawing has
   to preserve grouping: 15-451 is "15-210 and 21-241 and (15-251 or 21-228)",
   and flattening that into one row would label every gap "and" and quietly
   misstate the requirement. Top-level operands are laid out left to right,
   joined by the outer operator; any operand that is itself a group is drawn as
   a bracketed cluster with its own operator inside.

   Courses already in the plan are tinted, so what is left to take is obvious. */

import { state } from './data.js';

const NODE_W = 74, NODE_H = 26;
const GAP = 12;          // between nodes inside a cluster
const OP_GAP = 30;       // room for the operator label between operands
const PAD = 8;           // cluster padding
const ROW_GAP = 46;      // vertical space between operands and the root

const SVG = 'http://www.w3.org/2000/svg';
const el = (name, attrs = {}) => {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
};

/* Normalise the tree into a list of top-level operands plus the operator that
   joins them. A bare leaf becomes a single operand. */
function operands(tree) {
  if (!tree) return { op: null, parts: [] };
  if (tree.and) return { op: 'and', parts: tree.and };
  if (tree.or) return { op: 'or', parts: tree.or };
  return { op: null, parts: [tree] };
}

/* Leaves of an operand, with the operator joining them if it is a group. */
function cluster(part) {
  if (part.and) return { op: 'and', leaves: part.and.flatMap(flatten) };
  if (part.or) return { op: 'or', leaves: part.or.flatMap(flatten) };
  return { op: null, leaves: [part] };
}
function flatten(node) {
  if (node.and) return node.and.flatMap(flatten);
  if (node.or) return node.or.flatMap(flatten);
  return [node];
}

function leafLabel(leaf) {
  if (leaf.course) return leaf.course;
  const text = leaf.text || '';
  return text.length > 11 ? text.slice(0, 10) + '…' : text;
}

export function prereqGraph(courseId) {
  const course = state.byId.get(courseId);
  if (!course || !course.pre.tree) return null;

  const planned = new Set();
  for (const term of state.plan) for (const id of term) planned.add(id);

  const { op, parts } = operands(course.pre.tree);
  const groups = parts.map(cluster).filter(g => g.leaves.length);
  if (!groups.length) return null;

  // Measure first so the SVG can be sized to its contents.
  const widths = groups.map(g => g.leaves.length * NODE_W + (g.leaves.length - 1) * GAP + (g.leaves.length > 1 ? PAD * 2 : 0));
  const totalW = widths.reduce((a, b) => a + b, 0) + (groups.length - 1) * OP_GAP;
  const width = Math.max(totalW, NODE_W) + 24;
  const height = NODE_H * 2 + ROW_GAP + 28;

  const svg = el('svg', {
    viewBox: `0 0 ${width} ${height}`, width, height,
    role: 'img', 'aria-label': `Prerequisites for ${courseId}`,
  });

  const rootX = width / 2 - NODE_W / 2;
  const rootY = height - NODE_H - 12;
  const topY = 14;

  let x = (width - totalW) / 2;
  groups.forEach((group, gi) => {
    const groupW = widths[gi];
    const multi = group.leaves.length > 1;

    if (multi) {
      svg.appendChild(el('rect', {
        x, y: topY - PAD + 2, width: groupW, height: NODE_H + PAD * 2 - 4,
        rx: 10, class: 'gcluster',
      }));
    }

    let nodeX = x + (multi ? PAD : 0);
    group.leaves.forEach((leaf, li) => {
      // one connector per cluster, drawn from its centre
      if (li === 0) {
        const fromX = x + groupW / 2;
        const midY = (topY + NODE_H + rootY) / 2;
        svg.appendChild(el('path', {
          d: `M${fromX},${topY + NODE_H + (multi ? PAD - 2 : 0)} C${fromX},${midY} ${rootX + NODE_W / 2},${midY} ${rootX + NODE_W / 2},${rootY}`,
          class: 'gedge',
        }));
      }
      svg.appendChild(nodeEl(leaf, nodeX, topY, planned));

      if (li < group.leaves.length - 1 && group.op) {
        svg.appendChild(opLabel(group.op, nodeX + NODE_W + GAP / 2, topY + NODE_H / 2 + 4));
      }
      nodeX += NODE_W + GAP;
    });

    if (gi < groups.length - 1 && op) {
      svg.appendChild(opLabel(op, x + groupW + OP_GAP / 2, topY + NODE_H / 2 + 4, true));
    }
    x += groupW + OP_GAP;
  });

  svg.appendChild(nodeEl({ course: courseId }, rootX, rootY, planned, true));
  return svg;
}

function opLabel(text, x, y, strong = false) {
  const label = el('text', { x, y, 'text-anchor': 'middle', class: strong ? 'gop gop-strong' : 'gop' });
  label.textContent = text;
  return label;
}

function nodeEl(leaf, x, y, planned, isRoot = false) {
  const isCourse = Boolean(leaf.course);
  const classes = ['gnode'];
  if (isRoot) classes.push('root');
  else if (isCourse && planned.has(leaf.course)) classes.push('taken');
  if (!isCourse) classes.push('prose');

  const g = el('g', { class: classes.join(' ') });
  g.appendChild(el('rect', { x, y, width: NODE_W, height: NODE_H, rx: 7 }));

  const text = el('text', { x: x + NODE_W / 2, y: y + NODE_H / 2 + 4, 'text-anchor': 'middle' });
  text.textContent = leafLabel(leaf);
  if (!isCourse) {
    const title = el('title');
    title.textContent = leaf.text;
    g.appendChild(title);
  }
  g.appendChild(text);
  if (isCourse) g.dataset.course = leaf.course;
  return g;
}
