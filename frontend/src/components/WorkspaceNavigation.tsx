import { LayoutGrid, LogOut, Pencil, Settings, Smile } from 'lucide-react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

type WorkspaceNavigationProps = {
  workspaceId?: string
}

function WorkspaceNavigation({ workspaceId }: WorkspaceNavigationProps) {
  const { logout, user } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const navigationItems = [
    { label: 'Workspaces', path: '/dashboard', icon: LayoutGrid },
    {
      label: 'Edit',
      path: workspaceId ? `/workspaces/${workspaceId}/edit` : '/dashboard',
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
          end={path === '/dashboard'}
          key={path}
        >
          <Icon aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}

      <button
        type="button"
        className="top-action logout-action"
        onClick={handleLogout}
        title={user ? `Signed in as ${user.email} - Click to logout` : 'Sign out'}
        aria-label="Sign out"
      >
        <LogOut aria-hidden="true" />
        <span>Logout</span>
      </button>
    </nav>
  )
}

export default WorkspaceNavigation
