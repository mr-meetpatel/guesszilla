import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, ShieldCheck } from 'lucide-react';
import { type FormEvent } from 'react';
import { api, write, type Envelope, type User } from '../api';
import { ActionFeedback, Confirm, dateTime, Modal, useAction } from '../ui';
import { Panel, Status, Table, Title } from './shared';

interface AdminRow {
  id: string;
  username: string;
  active: boolean;
  must_change_password: boolean;
}

interface SessionRow {
  id: string;
  admin_id: string;
  revoked: boolean;
  expires_at: string;
}

export function AdministrationPage({ user }: { user: User }) {
  const action = useAction();
  const queryClient = useQueryClient();
  const admins = useQuery({
    queryKey: ['admins'],
    queryFn: () => api<Envelope<AdminRow[]>>('/admins'),
    enabled: !user.must_change_password,
  });
  const sessions = useQuery({
    queryKey: ['sessions'],
    queryFn: () => api<Envelope<SessionRow[]>>('/sessions'),
    enabled: !user.must_change_password,
  });
  async function changePassword(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    action.mutate(async () => {
      await api(
        '/auth/password',
        { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) },
        false,
      );
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' });
      queryClient.setQueryData(['me'], null);
    });
  }
  return (
    <>
      <Title
        eyebrow="ACCESS & SECURITY"
        title="The right people. Full control."
        action={
          !user.must_change_password && (
            <Modal
              title="Create super-admin"
              description="This account has full access to every workspace. A temporary password must be changed at first sign-in."
              trigger={
                <button className="primary">
                  <Plus size={15} /> Add admin
                </button>
              }
            >
              <AdminPasswordForm />
            </Modal>
          )
        }
      >
        Named accounts, revocable sessions, and a clear record of every change.
      </Title>
      {user.must_change_password && (
        <div className="info-banner">
          <ShieldCheck size={20} /> Change your temporary password to access the platform.
        </div>
      )}
      <Panel
        title="Change your password"
        caption="Changing your password revokes all of your sessions, including this one."
      >
        <form className="compact-form" onSubmit={changePassword}>
          <label>
            Current password
            <input
              type="password"
              name="current_password"
              required
              autoComplete="current-password"
            />
          </label>
          <label>
            New password
            <input
              type="password"
              name="password"
              required
              minLength={12}
              maxLength={256}
              autoComplete="new-password"
            />
          </label>
          <button className="primary" disabled={action.isPending}>
            Change password & sign out
          </button>
        </form>
      </Panel>
      {!user.must_change_password && (
        <>
          <Panel title="Super-administrators" caption="The last active admin cannot be disabled.">
            <Table
              rows={admins.data?.data || []}
              columns={[
                { label: 'Username', render: (a) => <strong>{a.username}</strong> },
                {
                  label: 'Status',
                  render: (a) => <Status value={a.active ? 'Active' : 'disabled'} />,
                },
                {
                  label: 'Actions',
                  render: (a) => (
                    <div className="row">
                      <Confirm
                        label={a.active ? 'Disable' : 'Enable'}
                        description="All sessions for this administrator will be revoked."
                        onConfirm={() => write('/admins/' + a.id, { active: !a.active }, 'PATCH')}
                      />
                      <Modal
                        title="Reset admin password"
                        description="This revokes all sessions and requires a password change at the next login."
                        trigger={<button>Reset password</button>}
                      >
                        <AdminPasswordForm adminId={a.id} />
                      </Modal>
                    </div>
                  ),
                },
              ]}
            />
          </Panel>
          <Panel
            title="Active session families"
            caption="Revocation takes effect on the next API request across every replica."
          >
            <Table
              rows={sessions.data?.data || []}
              columns={[
                { label: 'Session', render: (s) => <span className="mono tiny">{s.id}</span> },
                {
                  label: 'Admin',
                  render: (s) =>
                    admins.data?.data.find((a) => a.id === s.admin_id)?.username || s.admin_id,
                },
                { label: 'Expires', render: (s) => dateTime(s.expires_at) },
                { label: 'Status', render: (s) => (s.revoked ? 'Revoked' : 'Active') },
                {
                  label: 'Actions',
                  render: (s) =>
                    !s.revoked && (
                      <button
                        onClick={() =>
                          action.mutate(() => write('/sessions/' + s.id + '/revoke', {}))
                        }
                      >
                        Revoke session
                      </button>
                    ),
                },
              ]}
            />
          </Panel>
        </>
      )}
    </>
  );
}

function AdminPasswordForm({ adminId }: { adminId?: string }) {
  const action = useAction();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(e.currentTarget));
        action.mutate(() =>
          write(adminId ? '/admins/' + adminId : '/admins', data, adminId ? 'PATCH' : 'POST'),
        );
      }}
    >
      {!adminId && (
        <label>
          Username
          <input name="username" required maxLength={80} autoComplete="off" />
        </label>
      )}
      <label>
        Temporary password
        <input
          name="password"
          type="password"
          required
          minLength={12}
          maxLength={256}
          autoComplete="new-password"
        />
      </label>
      <button className="primary" disabled={action.isPending}>
        Save admin
      </button>
      <ActionFeedback action={action} />
    </form>
  );
}
