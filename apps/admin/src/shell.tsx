import { createContext, useContext, useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  CalendarClock,
  FileText,
  Globe2,
  ListTodo,
  LogOut,
  Settings2,
  Users,
  ShieldCheck,
  Menu,
  X,
} from 'lucide-react';
import { api, type User, type Workspace, type Channel, type Envelope } from './api';
import { Login } from './auth';
import { Notices, Loading, ErrorBox } from './ui';
import {
  OverviewPage,
  WorkspacesPage,
  ParticipantsPage,
  ReportsPage,
  SchedulesPage,
  JobsPage,
  LogsPage,
  AdministrationPage,
} from './pages';

interface Scope {
  workspace?: Workspace;
  channel?: Channel;
  channels: Channel[];
  workspaces: Workspace[];
}
const ScopeContext = createContext<Scope>({ channels: [], workspaces: [] });
export const useScope = () => useContext(ScopeContext);
const navigation = [
  ['/', 'Overview', BarChart3],
  ['/workspaces', 'Workspaces', Globe2],
  ['/participants', 'Participants', Users],
  ['/reports', 'Reports', FileText],
  ['/schedules', 'Schedules', CalendarClock],
  ['/jobs', 'Background jobs', ListTodo],
  ['/logs', 'Logs & audit', Activity],
  ['/administration', 'Administration', Settings2],
] as const;
const persistLogs = import.meta.env.VITE_PERSIST_LOGS === 'true';

export function App() {
  const client = useQueryClient();
  const user = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api<User>('/auth/me');
      } catch {
        return null;
      }
    },
    refetchInterval: false,
  });
  const [notice, setNotice] = useState('');
  useEffect(() => {
    const expired = () => client.setQueryData(['me'], null);
    window.addEventListener('auth:expired', expired);
    return () => window.removeEventListener('auth:expired', expired);
  }, [client]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 6000);
    return () => clearTimeout(timer);
  }, [notice]);
  if (user.isPending) return <Loading />;
  if (!user.data)
    return (
      <Login
        onSuccess={(value) => {
          client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' });
          client.setQueryData(['me'], value);
        }}
      />
    );
  return (
    <Notices.Provider value={setNotice}>
      <Layout user={user.data} />
      {notice && (
        <div role="status" className="toast">
          {notice}
          <button aria-label="Dismiss notification" onClick={() => setNotice('')}>
            <X size={14} />
          </button>
        </div>
      )}
    </Notices.Provider>
  );
}

function Layout({ user }: { user: User }) {
  const client = useQueryClient();
  const notify = useContext(Notices);
  const location = useLocation();
  const [workspaceId, setWorkspaceId] = useState(
    () => new URLSearchParams(location.search).get('workspace_id') || '',
  );
  const [channelId, setChannelId] = useState('');
  const [mobile, setMobile] = useState(false);
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api<Envelope<Workspace[]>>('/workspaces'),
  });
  const workspace =
    workspaces.data?.data.find((w) => w.id === workspaceId) || workspaces.data?.data[0];
  const channels = useQuery({
    queryKey: ['channels', workspace?.id],
    queryFn: () => api<Envelope<Channel[]>>('/channels?workspace_id=' + workspace?.id),
    enabled: !!workspace,
  });
  const channel =
    channels.data?.data.find((c) => c.id === channelId) ||
    channels.data?.data.find((c) => c.active) ||
    channels.data?.data[0];
  const title = navigation.find(([path]) => path === location.pathname)?.[1] || 'Control room';
  return (
    <ScopeContext.Provider
      value={{
        workspace,
        channel,
        channels: channels.data?.data || [],
        workspaces: workspaces.data?.data || [],
      }}
    >
      <div className="app-shell">
        <aside className={mobile ? 'sidebar mobile-open' : 'sidebar'}>
          <NavLink to="/" className="brand">
            <span className="brand-mark">W</span>
            <span>
              Wordle<span className="brand-light">Control</span>
            </span>
          </NavLink>
          <div className="workspace-label">YOUR CONTROL ROOM</div>
          <nav>
            {navigation
              .filter(([path]) => persistLogs || path !== '/logs')
              .map(([path, text, Icon]) => (
                <NavLink key={path} to={path} end={path === '/'} onClick={() => setMobile(false)}>
                  <Icon size={18} />
                  {text}
                </NavLink>
              ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="secure-note">
              <ShieldCheck size={15} /> Super-admin session
            </div>
            <div className="profile">
              <span className="avatar">{user.username.slice(0, 1).toUpperCase()}</span>
              <div>
                <strong>{user.username}</strong>
                <small>Full platform access</small>
              </div>
              <button
                aria-label="Sign out"
                onClick={async () => {
                  try {
                    await api('/auth/logout', { method: 'POST' });
                    client.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' });
                    client.setQueryData(['me'], null);
                  } catch (error) {
                    notify(error instanceof Error ? error.message : 'Sign-out failed. Try again.');
                  }
                }}
              >
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </aside>
        <section className="app-main">
          <header className="topbar">
            <button
              className="mobile-toggle"
              aria-label="Toggle navigation"
              onClick={() => setMobile(!mobile)}
            >
              <Menu size={20} />
            </button>
            <span className="breadcrumb">
              Control room <span>/</span> <strong>{title}</strong>
            </span>
            <div className="scope-selectors">
              <select
                aria-label="Workspace"
                value={workspace?.id || ''}
                onChange={(e) => {
                  setWorkspaceId(e.target.value);
                  setChannelId('');
                }}
              >
                <option value="" disabled>
                  Select workspace
                </option>
                {workspaces.data?.data.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
              <select
                aria-label="Channel"
                value={channel?.id || ''}
                onChange={(e) => setChannelId(e.target.value)}
              >
                <option value="" disabled>
                  Select channel
                </option>
                {channels.data?.data.map((c) => (
                  <option key={c.id} value={c.id}>
                    # {c.name}
                  </option>
                ))}
              </select>
              <span className="live-indicator">
                <i /> Live
              </span>
            </div>
          </header>
          <main>
            {workspaces.error && <ErrorBox error={workspaces.error} />}
            {channels.error && <ErrorBox error={channels.error} />}
            <Routes>
              <Route
                path="/"
                element={
                  user.must_change_password ? (
                    <Navigate to="/administration" replace />
                  ) : (
                    <OverviewPage />
                  )
                }
              />
              <Route path="/workspaces" element={<WorkspacesPage />} />
              <Route path="/participants" element={<ParticipantsPage />} />
              <Route path="/reports" element={<ReportsPage />} />
              <Route path="/schedules" element={<SchedulesPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route
                path="/logs"
                element={persistLogs ? <LogsPage /> : <Navigate to="/" replace />}
              />
              <Route path="/administration" element={<AdministrationPage user={user} />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <footer>
            Wordle Control <span>Report dates use each channel’s timezone.</span>
          </footer>
        </section>
      </div>
    </ScopeContext.Provider>
  );
}
