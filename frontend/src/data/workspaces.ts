export type WorkspaceSource = {
  id: string | number
  name: string
  description: string
}

export type Workspace = {
  id: string
  ownerId?: number
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
