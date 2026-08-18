export type WorkspaceSource = {
  id: string | number
  name: string
  description: string
}

export type Workspace = {
  id: string
  name: string
  semester: string
  examDate: string
  topics: string[]
  syllabus: string
  progress: number
  status: string
  updatedAt: string
  accent: 'blue' | 'violet' | 'rose' | 'amber'
  sources: WorkspaceSource[]
}

export type WorkspaceDraft = {
  name: string
  semester: string
  examDate: string
  topics: string
  syllabus: string
}

const psychologySources: WorkspaceSource[] = [1, 2, 3].map((number) => ({
  id: number,
  name: `Source ${number}`,
  description: 'Source description.',
}))

export const initialWorkspaces: Workspace[] = [
  {
    id: 'introduction-to-psychology',
    name: 'Introduction to Psychology',
    semester: 'Fall 2026',
    examDate: '2026-12-14',
    topics: ['Cognitive biases', 'Perception', 'Accountability', 'Escapism'],
    syllabus:
      'Explore the foundations of human behavior, cognition, and emotional development through weekly readings and guided practice.',
    progress: 68,
    status: 'Exam prep',
    updatedAt: 'Updated today',
    accent: 'violet',
    sources: psychologySources,
  },
  {
    id: 'data-structures',
    name: 'Data Structures',
    semester: 'Fall 2026',
    examDate: '2026-12-18',
    topics: ['Trees', 'Graphs', 'Hash tables'],
    syllabus:
      'Core data structures, complexity analysis, and implementation patterns.',
    progress: 42,
    status: 'In progress',
    updatedAt: 'Updated yesterday',
    accent: 'blue',
    sources: [
      { id: 1, name: 'Lecture Notes.pdf', description: '12 chapters.' },
      { id: 2, name: 'Practice Problems.md', description: '32 exercises.' },
    ],
  },
  {
    id: 'modern-history',
    name: 'Modern History',
    semester: 'Spring 2027',
    examDate: '',
    topics: ['Industrialization', 'Social change', 'Global conflicts'],
    syllabus: 'A survey of political and social change in the modern world.',
    progress: 24,
    status: 'Reading',
    updatedAt: 'Updated 3 days ago',
    accent: 'rose',
    sources: [
      { id: 1, name: 'Course Reader.pdf', description: 'Primary source collection.' },
    ],
  },
  {
    id: 'physics-mechanics',
    name: 'Physics: Mechanics',
    semester: '',
    examDate: '2027-01-09',
    topics: ['Motion', 'Forces', 'Energy'],
    syllabus: '',
    progress: 81,
    status: 'On track',
    updatedAt: 'Updated last week',
    accent: 'amber',
    sources: [
      { id: 1, name: 'Mechanics.pdf', description: 'Course textbook.' },
      { id: 2, name: 'Formula Sheet.pdf', description: 'Exam reference.' },
      { id: 3, name: 'Lab Notes.txt', description: 'Experiment notes.' },
      { id: 4, name: 'Review.md', description: 'Weekly review.' },
    ],
  },
]
