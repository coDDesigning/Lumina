import { LayoutGrid, Pencil, Settings, Smile } from 'lucide-react'
import { NavLink } from 'react-router-dom'

type WorkspaceNavigationProps = {
  workspaceId?: string
}

function WorkspaceNavigation({ workspaceId }: WorkspaceNavigationProps) {
  const navigationItems = [
    { label: 'Workspaces', path: '/', icon: LayoutGrid },
    {
      label: 'Edit',
      path: workspaceId ? `/workspaces/${workspaceId}/edit` : '/',
      icon: Pencil,
    },
    { label: 'Settings', path: '/settings', icon: Settings },
    { label: 'Profile', path: '/profile', icon: Smile },
  ]

  return (
    <nav className="top-actions" aria-label="Workspace controls">
      {navigationItems.map(({ label, path, icon: Icon }) => (
        <NavLink
          className={({ isActive }) =>
            `top-action${isActive ? ' active' : ''}`
          }
          to={path}
          end={path === '/'}
          key={path}
        >
          <Icon aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}

export default WorkspaceNavigation
