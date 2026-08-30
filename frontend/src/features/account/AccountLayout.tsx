import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useAuth } from '@/context/AuthContext';
import { cx } from '@/lib/cx';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Card';
import { PageHeader } from '@/ui/PageHeader';
import styles from './AccountPage.module.css';

const SECTIONS = [
  { to: '/account', label: 'You', end: true },
  { to: '/account/background', label: 'Your background', end: false },
  { to: '/account/ai', label: 'AI', end: false },
  { to: '/account/api-keys', label: 'API keys', end: false },
  { to: '/account/appearance', label: 'Appearance', end: false },
  { to: '/account/security', label: 'Security', end: false },
];

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return '?';
  }
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

export default function AccountLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  useDocumentTitle('Account');

  function handleSignOut() {
    logout();
    navigate('/login');
  }

  const isAdmin = user?.role?.toLowerCase() === 'admin';
  const visibleSections = SECTIONS.filter(
    (section) => section.to !== '/account/api-keys' || isAdmin,
  );

  return (
    <div className={styles.page}>
      <PageHeader crumbs={[{ label: 'Account' }]} />

      <div className={styles.body}>
        <h1 className={styles.title}>Account</h1>

        <Card className={styles.identity}>
          <span className={styles.avatar} aria-hidden="true">
            {initials(user?.name ?? '')}
          </span>
          <div className={styles.identityText}>
            <p className={styles.name}>{user?.name}</p>
            <p className={styles.email}>{user?.email}</p>
            <div className={styles.identityMeta}>
              {user?.role ? <Badge tone="accent">{user.role}</Badge> : null}
            </div>
          </div>
          <Button variant="ghost" icon={<LogOut aria-hidden="true" />} onClick={handleSignOut}>
            Sign out
          </Button>
        </Card>

        <nav className={styles.sectionNav} aria-label="Account sections">
          {visibleSections.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              end={section.end}
              className={({ isActive }) => cx(styles.sectionLink, isActive && styles.sectionCurrent)}
            >
              {section.label}
            </NavLink>
          ))}
        </nav>

        <Outlet />
      </div>
    </div>
  );
}
