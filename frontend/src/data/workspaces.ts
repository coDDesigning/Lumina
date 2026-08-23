import type { EducationLevel } from '../api/types'

export type Workspace = {
  id: string
  ownerId?: number
  name: string
  subjectArea: string
  educationLevel: EducationLevel
  semester: string
  examDate: string
  topics: string[]
  syllabus: string
  progress: number | null
  status: string
  updatedAt: string
  accent: 'blue' | 'violet' | 'rose' | 'amber'
}

export type WorkspaceDraft = {
  name: string
  subjectArea: string
  educationLevel: EducationLevel
  semester: string
  examDate: string
  topics: string
  syllabus: string
}
