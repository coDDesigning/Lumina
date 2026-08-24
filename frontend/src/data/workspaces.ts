import type { CourseProgressSummary, EducationLevel } from '../api/types'

export type WorkspaceProgressStatus = 'Not started' | 'In progress' | 'Mastered'

export type WorkspaceProgress = {
  averageScore: number | null
  lastActivity: string | null
  status: WorkspaceProgressStatus
}

export function toWorkspaceProgress(
  summary: CourseProgressSummary,
): WorkspaceProgress {
  const averageScore =
    summary.average_score === null ? null : Math.round(summary.average_score * 100)

  let status: WorkspaceProgressStatus = 'In progress'
  if (summary.attempts_count === 0 && summary.last_activity === null) {
    status = 'Not started'
  } else if (averageScore !== null && averageScore >= 80) {
    status = 'Mastered'
  }

  return { averageScore, lastActivity: summary.last_activity, status }
}

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
  progress: WorkspaceProgress | null
  updatedAt: string
  accent: 'blue' | 'violet' | 'rose' | 'amber'
  isArchived?: boolean
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
