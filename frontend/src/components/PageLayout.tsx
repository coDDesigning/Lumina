import type { PropsWithChildren } from 'react'
import type { LucideIcon } from 'lucide-react'
import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router-dom'

type PageLayoutProps = PropsWithChildren<{
  title: string
  description: string
  eyebrow: string
  icon: LucideIcon
  workspaceId?: string
}>

function PageLayout({
  title,
  description,
  eyebrow,
  icon: Icon,
  workspaceId,
  children,
}: PageLayoutProps) {
  const workspacePath = workspaceId ? `/workspaces/${workspaceId}` : '/'

  return (
    <div className="detail-page-shell">
      <section className="panel detail-page-panel">
        <header className="detail-page-header">
          <div className="detail-title-group">
            <span className="detail-title-icon" aria-hidden="true">
              <Icon />
            </span>
            <div>
              <p className="detail-eyebrow">{eyebrow}</p>
              <h1>{title}</h1>
              <p className="detail-description">{description}</p>
            </div>
          </div>

          <Link className="back-link" to={workspacePath}>
            <ArrowLeft aria-hidden="true" />
            {workspaceId ? 'Back to workspace' : 'Back to workspaces'}
          </Link>
        </header>

        {children}
      </section>
    </div>
  )
}

export default PageLayout
