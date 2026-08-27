import type { CourseProgressSummary, CourseStatus, EducationLevel } from '../api/types'

export type WorkspaceProgressStatus = CourseStatus

export type WorkspaceProgress = {
  averageScore: number | null
  timeSpentSeconds: number | null
  lastActivity: string | null
  status: WorkspaceProgressStatus
}

export function toWorkspaceProgress(
  summary: CourseProgressSummary,
): WorkspaceProgress {
  return {
    averageScore:
      summary.average_score === null ? null : Math.round(summary.average_score * 100),
    timeSpentSeconds: summary.total_time_spent_seconds ?? null,
    lastActivity: summary.last_activity,
    status: summary.status as WorkspaceProgressStatus,
  }
}

export type Workspace = {
  id: string
  ownerId?: number
  ownerName?: string | null
  ownerEmail?: string | null
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
