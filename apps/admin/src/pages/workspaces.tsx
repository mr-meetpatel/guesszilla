import { Globe2, Plus, RefreshCw } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { write, type Channel, type Rules } from '../api';
import { useScope } from '../shell';
import { ActionFeedback, Confirm, dateTime, Empty, Modal, useAction } from '../ui';
import { DayPicker, Panel, Status, Table, Timezone, Title } from './shared';

export function WorkspacesPage() {
  const { workspaces, channels, workspace } = useScope();
  const action = useAction();
  return (
    <>
      <Title
        eyebrow="CONNECTIONS"
        title="Your Wordle communities."
        action={
          <button
            className="primary"
            disabled={action.isPending}
            onClick={() =>
              action.mutate(async () => {
                const value = await write<{ url: string }>('/workspaces/connect', {});
                window.location.assign(value.data.url);
              })
            }
          >
            <Plus size={16} /> Connect workspace
          </button>
        }
      >
        Connect Slack, discover channels, and decide where the game happens.
      </Title>
      <div className="workspace-cards">
        {workspaces.map((w) => (
          <div className="workspace-card" key={w.id}>
            <div className="workspace-logo">
              <Globe2 size={23} />
            </div>
            <h2>{w.name}</h2>
            <p className="mono subtle">{w.id}</p>
            <div className="row spread">
              <Status value={w.active ? 'Active' : 'paused'} />
              <Confirm
                label={w.active ? 'Pause' : 'Resume'}
                disabled={!w.connected}
                description="This changes event tracking and future deliveries. A Slack request already in flight may finish."
                onConfirm={() => write('/workspaces/' + w.id, { active: !w.active }, 'PATCH')}
              />
              <Confirm
                label="Disconnect"
                disabled={!w.connected}
                description="Remove the stored bot credential, stop tracking and pause schedules. Pending unsent reports are cancelled; historical records and uncertain receipts remain. A request already in flight may finish. Reconnect through Slack OAuth to restore access."
                onConfirm={() => write('/workspaces/' + w.id, {}, 'DELETE')}
              />
            </div>
          </div>
        ))}
      </div>
      {!workspaces.length && (
        <Empty title="Connect your first workspace.">
          Authorize the Slack app, then discover the channels you want to track.
        </Empty>
      )}
      <Panel
        title={workspace ? `Channels in ${workspace.name}` : 'Tracked channels'}
        caption="Newly discovered channels start paused. Invite the bot before enabling tracking."
        action={
          workspace && (
            <button
              disabled={action.isPending}
              onClick={() => action.mutate(() => write(`/workspaces/${workspace.id}/sync`, {}))}
            >
              <RefreshCw size={15} /> Discover channels
            </button>
          )
        }
      >
        <Table
          rows={channels}
          columns={[
            { label: 'Channel', render: (c) => <strong># {c.name}</strong> },
            { label: 'Tracking', render: (c) => <Status value={c.active ? 'Active' : 'paused'} /> },
            { label: 'Timezone', render: (c) => c.rules.timezone },
            {
              label: 'Roster',
              render: (c) => (c.roster_synced_at ? dateTime(c.roster_synced_at) : 'Not synced'),
            },
            {
              label: 'Actions',
              render: (c) => (
                <div className="row">
                  <button onClick={() => action.mutate(() => write(`/channels/${c.id}/sync`, {}))}>
                    Sync roster
                  </button>
                  <Modal
                    title={`# ${c.name} settings`}
                    description="Changes affect new report evaluations. Existing report snapshots remain unchanged."
                    trigger={<button>Configure</button>}
                  >
                    <ChannelForm channel={c} />
                  </Modal>
                </div>
              ),
            },
          ]}
        />
      </Panel>
    </>
  );
}

function ChannelForm({ channel }: { channel: Channel }) {
  const [rules, setRules] = useState<Rules>(channel.rules);
  const [version] = useState(channel.version);
  const action = useAction();
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    action.mutate(() =>
      write(
        `/channels/${channel.id}`,
        {
          version,
          rules,
          active: form.get('active') === 'on',
          ...(form.get('password') ? { password: form.get('password') } : {}),
        },
        'PATCH',
      ),
    );
  }
  return (
    <form onSubmit={submit}>
      <label>
        Reporting timezone
        <Timezone
          value={rules.timezone}
          onChange={(timezone) => setRules({ ...rules, timezone })}
        />
      </label>
      <label>
        Eligible weekdays
        <DayPicker
          value={rules.weekdays}
          onChange={(days) => setRules({ ...rules, weekdays: days })}
        />
      </label>
      <label>
        Days to become royalty
        <input
          type="number"
          min="1"
          max="366"
          value={rules.threshold}
          onChange={(e) => setRules({ ...rules, threshold: Number(e.target.value) })}
        />
      </label>
      <label>
        Report title
        <input
          maxLength={120}
          value={rules.title}
          onChange={(e) => setRules({ ...rules, title: e.target.value })}
        />
      </label>
      <div className="form-grid">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={rules.announce}
            onChange={(e) => setRules({ ...rules, announce: e.target.checked })}
          />{' '}
          Announce @channel
        </label>
        <label className="checkbox">
          <input name="active" type="checkbox" defaultChecked={channel.active} /> Track this channel
        </label>
      </div>
      <details>
        <summary>Customize reactions</summary>
        <div className="reaction-grid">
          {Object.entries(rules.reactions).map(([score, emoji]) => (
            <label key={score}>
              {score} guesses
              <input
                value={emoji}
                maxLength={64}
                onChange={(e) =>
                  setRules({ ...rules, reactions: { ...rules.reactions, [score]: e.target.value } })
                }
              />
            </label>
          ))}
        </div>
      </details>
      <label>
        New slash-command password
        <input
          type="password"
          name="password"
          autoComplete="new-password"
          minLength={12}
          maxLength={256}
          placeholder={
            channel.has_password
              ? 'Leave blank to keep current password'
              : 'Set a password for /send-stats'
          }
        />
      </label>
      <button className="primary" disabled={action.isPending}>
        Save settings
      </button>
      <ActionFeedback action={action} />
      <p className="tiny">
        If another administrator edits these settings first, reload before saving.
      </p>
    </form>
  );
}
