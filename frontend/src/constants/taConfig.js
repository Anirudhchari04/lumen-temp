// TA (and utility agent) definitions — colors, icons, labels.
// The icon is a small SVG component; imported into nav/chip/card UIs.

import {
  IconClock, IconPlus, IconCode, IconGrid, IconPeople, IconShield,
} from '../components/icons.jsx'

export const TAS = [
  {
    id: 'lumen',
    name: 'Lumen',
    subject: 'Your personal companion',
    color: '#c8762a',
    bgColor: '#fdf0e0',
    dotColor: '#c8762a',
    icon: IconClock,
  },
  {
    id: 'math-ta',
    name: 'Math TA',
    subject: 'Mathematics',
    color: '#c8762a',
    bgColor: '#f0ebe0',
    dotColor: '#c8762a',
    icon: IconPlus,
  },
  {
    id: 'cs-ta',
    name: 'CS TA',
    subject: 'Computer Science',
    color: '#5c6bc0',
    bgColor: '#e8eaf6',
    dotColor: '#5c6bc0',
    icon: IconCode,
  },
  {
    id: 'calendar',
    name: 'Calendar',
    subject: 'Plan & remind',
    color: '#4caf50',
    bgColor: '#e8f4ee',
    dotColor: '#4caf50',
    icon: IconGrid,
  },
]

export const TAS_BY_ID = Object.fromEntries(TAS.map(t => [t.id, t]))

export const NAV_EXTRAS = [
  { id: 'peers',   name: 'Peers',   icon: IconPeople, path: '/peers' },
  { id: 'privacy', name: 'Privacy', icon: IconShield, path: '/privacy' },
]

// Concept journey samples (for the SidePanel ConceptJourney tree).
// In a real build these come from /lumen/state?ta_id=...
export const SAMPLE_JOURNEY = {
  'math-ta': [
    { id: 'nums',     label: 'Number sense',      status: 'done' },
    { id: 'algebra',  label: 'Algebra basics',    status: 'done' },
    { id: 'vars',     label: 'Variables',         status: 'done' },
    { id: 'eq',       label: 'Equations',         status: 'in-progress' },
    { id: 'funcs',    label: 'Functions',         status: 'locked' },
    { id: 'lintran',  label: 'Linear transforms', status: 'locked' },
  ],
  'cs-ta': [
    { id: 'types',    label: 'Variables & types', status: 'done' },
    { id: 'flow',     label: 'Control flow',      status: 'done' },
    { id: 'loops',    label: 'Loops',             status: 'in-progress' },
    { id: 'funcs',    label: 'Functions',         status: 'locked' },
    { id: 'abs',      label: 'Abstraction',       status: 'locked' },
  ],
  'calendar': [
    { id: 'goal',     label: 'Set this week\u2019s goal', status: 'done' },
    { id: 'sched',    label: 'Schedule 3 sessions', status: 'in-progress' },
    { id: 'review',   label: 'Weekly review',     status: 'locked' },
  ],
}

export const SAMPLE_MODULE_PROGRESS = {
  'math-ta':  { label: 'Module 3 · Equations',   pct: 62 },
  'cs-ta':    { label: 'Module 2 · Loops',       pct: 40 },
  'calendar': { label: 'This week',              pct: 70 },
}
