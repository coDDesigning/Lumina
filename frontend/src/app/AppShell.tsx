import { LayoutGrid, LogOut, Moon, Shield, Sun, UserRound } from 'lucide-react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { cx } from '@/lib/cx';
import { Brandmark } from '@/ui/Brandmark';
import { useTheme } from './themeContext';
import styles from './AppShell.module.css';

interface RailLink {
  to: string;
  label: string;
  icon: typeof LayoutGrid;
  end?: boolean;
}

export function AppShell() {
  const { user, logout } = useAuth();
  const { resolved, setPreference } = useTheme();
  const navigate = useNavigate();

  const links: RailLink[] = [
    { to: '/dashboard', label: 'Courses', icon: LayoutGrid, end: true },
    { to: '/account', label: 'Account', icon: UserRound },
  ];

  if (user?.role === 'admin') {
    links.push({ to: '/admin', label: 'Admin', icon: Shield });
  }

  function handleSignOut() {
    logout();
    navigate('/login');
  }

  const nextTheme = resolved === 'dark' ? 'light' : 'dark';

  return (
    <div className={styles.shell}>
      <nav className={styles.rail} aria-label="Main">
        <NavLink to="/dashboard" className={styles.brand} aria-label="Lumina home">
          <Brandmark size="md" />
        </NavLink>

        {links.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            title={label}
            className={({ isActive }) => cx(styles.item, isActive && styles.itemActive)}
          >
            <Icon aria-hidden="true" />
            <span className="visually-hidden">{label}</span>
          </NavLink>
        ))}

        <span className={styles.spacer} />

        <button
          type="button"
          className={styles.item}
          onClick={() => setPreference(nextTheme)}
          title={nextTheme === 'dark' ? 'Switch to dark theme' : 'Switch to light theme'}
          aria-label={nextTheme === 'dark' ? 'Switch to dark theme' : 'Switch to light theme'}
        >
          {resolved === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
        </button>

        <button
          type="button"
          className={styles.item}
          onClick={handleSignOut}
          title="Sign out"
          aria-label="Sign out"
        >
          <LogOut aria-hidden="true" />
        </button>
      </nav>

      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
