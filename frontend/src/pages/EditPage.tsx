import { FormEvent, useState } from 'react'
import { BookOpenCheck, CalendarDays, ListChecks, Pencil } from 'lucide-react'
import PageLayout from '../components/PageLayout'
import type { Workspace } from '../data/workspaces'

type EditPageProps = {
  workspace: Workspace
  onSave: (workspace: Workspace) => void
}

function getCourseForm(workspace: Workspace) {
  return {
    name: workspace.name,
    semester: workspace.semester,
    examDate: workspace.examDate,
    topics: workspace.topics.join(', '),
    syllabus: workspace.syllabus,
  }
}

function EditPage({ workspace, onSave }: EditPageProps) {
  const [course, setCourse] = useState(() => getCourseForm(workspace))
  const [saved, setSaved] = useState(false)

  const updateCourse = (field: keyof typeof course, value: string) => {
    setCourse((current) => ({ ...current, [field]: value }))
    setSaved(false)
  }

  const saveCourse = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSave({
      ...workspace,
      name: course.name.trim(),
      semester: course.semester.trim(),
      examDate: course.examDate,
      topics: course.topics
        .split(',')
        .map((topic) => topic.trim())
        .filter(Boolean),
      syllabus: course.syllabus.trim(),
      updatedAt: 'Updated just now',
    })
    setSaved(true)
  }

  const resetCourse = () => {
    setCourse(getCourseForm(workspace))
    setSaved(false)
  }

  const topicList = course.topics
    .split(',')
    .map((topic) => topic.trim())
    .filter(Boolean)

  return (
    <PageLayout
      title="Edit Course"
      description="Keep the workspace focused by updating the course details used throughout your study flow."
      eyebrow="Course workspace"
      icon={Pencil}
      workspaceId={workspace.id}
    >
      <form className="detail-form" onSubmit={saveCourse}>
        <div className="form-section-grid">
          <section className="form-section form-section-wide">
            <header className="form-section-header">
              <BookOpenCheck aria-hidden="true" />
              <div>
                <h2>Course information</h2>
                <p>Core details shown across summaries, quizzes, and progress.</p>
              </div>
            </header>

            <div className="field-grid">
              <label className="form-field field-span-two">
                <span>Course name</span>
                <input
                  value={course.name}
                  onChange={(event) => updateCourse('name', event.target.value)}
                  placeholder="Enter course name"
                  required
                />
              </label>

              <label className="form-field">
                <span>Semester</span>
                <select
                  value={course.semester}
                  onChange={(event) =>
                    updateCourse('semester', event.target.value)
                  }
                >
                  <option value="">Not specified</option>
                  <option>Fall 2026</option>
                  <option>Spring 2027</option>
                  <option>Summer 2027</option>
                </select>
              </label>

              <label className="form-field">
                <span>Exam date</span>
                <span className="input-with-icon">
                  <CalendarDays aria-hidden="true" />
                  <input
                    type="date"
                    value={course.examDate}
                    onChange={(event) =>
                      updateCourse('examDate', event.target.value)
                    }
                  />
                </span>
              </label>
            </div>
          </section>

          <section className="form-section">
            <header className="form-section-header">
              <ListChecks aria-hidden="true" />
              <div>
                <h2>Study topics</h2>
                <p>Separate topics with commas.</p>
              </div>
            </header>

            <label className="form-field">
              <span>Topics</span>
              <textarea
                className="topics-input"
                value={course.topics}
                onChange={(event) => updateCourse('topics', event.target.value)}
                placeholder="Topic one, Topic two"
              />
            </label>

            <div className="topic-preview" aria-label="Topic preview">
              {topicList.map((topic) => (
                <span key={topic}>{topic}</span>
              ))}
            </div>
          </section>

          <section className="form-section">
            <header className="form-section-header">
              <BookOpenCheck aria-hidden="true" />
              <div>
                <h2>Syllabus information</h2>
                <p>Add context for future study activities.</p>
              </div>
            </header>

            <label className="form-field">
              <span>Syllabus overview</span>
              <textarea
                className="syllabus-input"
                value={course.syllabus}
                onChange={(event) => updateCourse('syllabus', event.target.value)}
                placeholder="Describe the course scope and learning goals"
              />
            </label>
          </section>
        </div>

        <div className="form-footer">
          <p className="form-feedback" role="status">
            {saved
              ? 'Course changes saved for this demo session.'
              : 'Changes stay in the frontend and are not sent to a server.'}
          </p>
          <div className="form-actions">
            <button className="secondary-button" type="button" onClick={resetCourse}>
              Reset
            </button>
            <button className="primary-button" type="submit">
              Save changes
            </button>
          </div>
        </div>
      </form>
    </PageLayout>
  )
}

export default EditPage
