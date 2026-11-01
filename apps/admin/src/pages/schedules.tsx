import { useQuery } from '@tanstack/react-query';
import { Clock3, Plus, ShieldCheck } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { api, write, type Envelope, type Schedule } from '../api';
import { useScope } from '../shell';
import { ActionFeedback, Badge, dateTime, Empty, ErrorBox, Modal, useAction } from '../ui';
import { DayPicker, Status, Table, Timezone, Title, weekdays } from './shared';

export function SchedulesPage() {
  const { channel } = useScope();
  const query = useQuery({
    queryKey: ['schedules', channel?.id],
    queryFn: () => api<Envelope<Schedule[]>>(`/schedules?channel_id=${channel?.id}`),
    enabled: !!channel,
  });
  return (
    <>
      <Title
        eyebrow="SET THE RHYTHM"
        title="A ritual that runs itself."
        action={
          channel && (
            <Modal
              title="Create schedule"
              description="New schedules are paused until you explicitly enable them."
              trigger={
                <button className="primary">
                  <Plus size={16} /> New schedule
                </button>
              }
            >
              <ScheduleForm />
            </Modal>
          )
        }
      >
        Daily or weekly. Local time, durable execution, and no blind retries.
      </Title>
      <div className="info-banner">
        <ShieldCheck size={18} />
        <p>
          One report per channel/date range. After an outage, only the latest missed run within 24
          hours is caught up. Uncertain Slack sends are held for review.
        </p>
      </div>
      {query.error && <ErrorBox error={query.error} />}
      <div className="schedule-cards">
        {query.data?.data.map((schedule) => (
          <ScheduleCard key={schedule.id} schedule={schedule} />
        ))}
      </div>
      {!query.data?.data.length && (
        <Empty title="Give your week a rhythm.">
          Create a Friday 5 PM schedule, choose its report window, then enable it when you’re ready.
        </Empty>
      )}
    </>
  );
}

function ScheduleCard({ schedule }: { schedule: Schedule }) {
  const action = useAction();
  return (
    <div className="schedule-card">
      <div className="row spread">
        <span className="schedule-icon">
          <Clock3 size={20} />
        </span>
        <Badge good={!schedule.paused}>{schedule.paused ? 'Paused' : 'Enabled'}</Badge>
      </div>
      <h2>{schedule.name}</h2>
      <div className="schedule-time">
        {schedule.wall_time} <small>{schedule.timezone}</small>
      </div>
      <p className="subtle">
        {schedule.frequency === 'daily'
          ? 'Every day'
          : schedule.weekdays.map((d) => weekdays[d]).join(', ')}{' '}
        · {schedule.window.replaceAll('_', ' ')}
      </p>
      <div className="next-run">
        <span>Next occurrence</span>
        <strong>{dateTime(schedule.next_at, schedule.timezone)}</strong>
      </div>
      <div className="row">
        <button
          disabled={action.isPending}
          onClick={() =>
            action.mutate(() =>
              write(
                `/schedules/${schedule.id}`,
                { version: schedule.version, paused: !schedule.paused },
                'PATCH',
              ),
            )
          }
        >
          {schedule.paused ? 'Enable schedule' : 'Pause schedule'}
        </button>
        <Modal
          title="Edit schedule"
          description="Changes apply prospectively; queued reports keep their original snapshot."
          trigger={<button>Edit</button>}
        >
          <ScheduleForm schedule={schedule} />
        </Modal>
        <Modal
          title="Run history"
          description="Upcoming times are shown alongside the durable occurrence ledger."
          trigger={<button>History</button>}
        >
          <ScheduleHistory schedule={schedule} />
        </Modal>
      </div>
    </div>
  );
}

function ScheduleForm({ schedule }: { schedule?: Schedule }) {
  const { channel } = useScope();
  const action = useAction();
  const [version] = useState(schedule?.version);
  const [days, setDays] = useState(schedule?.weekdays || [4]);
  const [zone, setZone] = useState(schedule?.timezone || channel?.rules.timezone || 'Asia/Kolkata');
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const data = {
      ...values,
      weekdays: days,
      timezone: zone,
      rolling_days: Number(values.rolling_days),
      paused: schedule?.paused ?? true,
      ...(schedule ? { version } : {}),
    };
    action.mutate(() =>
      write(
        schedule ? `/schedules/${schedule.id}` : `/channels/${channel?.id}/schedules`,
        data,
        schedule ? 'PATCH' : 'POST',
      ),
    );
  }
  return (
    <form onSubmit={submit}>
      <label>
        Name
        <input
          name="name"
          required
          maxLength={120}
          defaultValue={schedule?.name || 'Friday Wordle roundup'}
        />
      </label>
      <div className="form-grid">
        <label>
          Frequency
          <select name="frequency" defaultValue={schedule?.frequency || 'weekly'}>
            <option value="weekly">Weekly</option>
            <option value="daily">Daily</option>
          </select>
        </label>
        <label>
          Send at
          <input
            name="wall_time"
            type="time"
            required
            defaultValue={schedule?.wall_time || '17:00'}
          />
        </label>
      </div>
      <label>
        Weekly weekdays
        <DayPicker value={days} onChange={setDays} />
      </label>
      <label>
        Schedule timezone
        <Timezone value={zone} onChange={setZone} />
      </label>
      <label>
        Report window
        <select name="window" defaultValue={schedule?.window || 'current_week'}>
          <option value="current_week">Current reporting week</option>
          <option value="previous_week">Previous reporting week</option>
          <option value="rolling">Trailing calendar days</option>
        </select>
      </label>
      <label>
        Trailing window length
        <input
          name="rolling_days"
          type="number"
          min="1"
          max="366"
          defaultValue={schedule?.rolling_days || 7}
        />
      </label>
      <p className="tiny">
        Eligible weekdays and the royalty threshold come from channel settings. Daily runs with the
        same report date range are suppressed. Choose a rolling window for a changing daily report.
      </p>
      <button className="primary" disabled={action.isPending}>
        {action.isPending ? 'Saving…' : 'Save schedule'}
      </button>
      <ActionFeedback action={action} />
    </form>
  );
}

function ScheduleHistory({ schedule }: { schedule: Schedule }) {
  const query = useQuery({
    queryKey: ['occurrences', schedule.id],
    queryFn: () =>
      api<
        Envelope<{
          upcoming: string[];
          history: { id: string; scheduled_at: string; status: string; report_id: string | null }[];
        }>
      >(`/schedules/${schedule.id}/occurrences`),
  });
  return (
    <>
      {query.error && <ErrorBox error={query.error} />}
      <h3>Next five occurrences</h3>
      <ul>
        {query.data?.data.upcoming.map((time) => (
          <li key={time}>{dateTime(time, schedule.timezone)}</li>
        ))}
      </ul>
      <Table
        rows={query.data?.data.history || []}
        columns={[
          { label: 'Scheduled', render: (o) => dateTime(o.scheduled_at, schedule.timezone) },
          { label: 'Status', render: (o) => <Status value={o.status} /> },
          {
            label: 'Report',
            render: (o) => <span className="mono tiny">{o.report_id || '—'}</span>,
          },
        ]}
      />
    </>
  );
}
