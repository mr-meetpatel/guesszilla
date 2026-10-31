import { useQuery } from '@tanstack/react-query';
import { CalendarDays, Trophy, Users } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { api, type Envelope, type Snapshot } from '../api';
import { useScope } from '../shell';
import { Badge, Empty } from '../ui';
export const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export const timezones = [
  ...new Set(['Asia/Kolkata', 'UTC', ...Intl.supportedValuesOf('timeZone')]),
];

export function Title({
  eyebrow,
  title,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="subtle">{children}</p>
      </div>
      {action}
    </div>
  );
}

export function Panel({
  title,
  caption,
  children,
  action,
}: {
  title: string;
  caption?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {caption && <p>{caption}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Table<T>({
  rows,
  columns,
}: {
  rows: T[];
  columns: { label: string; render: (row: T) => ReactNode }[];
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.label}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((c) => (
                <td key={c.label}>{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <Empty title="No matching records." />}
    </div>
  );
}

export function Status({ value }: { value: string }) {
  return (
    <Badge good={['delivered', 'sent', 'done', 'healthy', 'Active'].includes(value)}>
      {value.replaceAll('_', ' ')}
    </Badge>
  );
}

export function usePeriod() {
  const { channel } = useScope();
  function defaults() {
    const today = new Date(
      new Intl.DateTimeFormat('en-CA', {
        timeZone: channel?.rules.timezone || 'Asia/Kolkata',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).format(new Date()) + 'T12:00:00Z',
    );
    today.setUTCDate(today.getUTCDate() - ((today.getUTCDay() + 6) % 7));
    const start = today.toISOString().slice(0, 10);
    today.setUTCDate(today.getUTCDate() + 4);
    return [start, today.toISOString().slice(0, 10)];
  }
  const [start, setStart] = useState(() => defaults()[0]);
  const [end, setEnd] = useState(() => defaults()[1]);
  useEffect(() => {
    const [a, b] = defaults();
    setStart(a);
    setEnd(b);
  }, [channel?.id]);
  const query = useQuery({
    queryKey: ['analytics', channel?.id, start, end],
    queryFn: () =>
      api<Envelope<Snapshot>>(`/channels/${channel?.id}/analytics?start=${start}&end=${end}`),
    enabled: !!channel && !!start && !!end,
  });
  return { start, end, setStart, setEnd, query, snapshot: query.data?.data, channel };
}

export function Period({ period }: { period: ReturnType<typeof usePeriod> }) {
  return (
    <div className="period-bar">
      <div className="row">
        <CalendarDays size={17} />
        <strong>Reporting period</strong>
      </div>
      <div className="row">
        <label className="inline">
          From
          <input
            aria-label="Start date"
            type="date"
            value={period.start}
            onChange={(e) => period.setStart(e.target.value)}
          />
        </label>
        <span>—</span>
        <label className="inline">
          To
          <input
            aria-label="End date"
            type="date"
            value={period.end}
            onChange={(e) => period.setEnd(e.target.value)}
          />
        </label>
        <Badge>{period.channel?.rules.timezone || 'Asia/Kolkata'}</Badge>
      </div>
    </div>
  );
}

export function Metrics({ snapshot }: { snapshot?: Snapshot }) {
  const cards = [
    [
      'Participation',
      snapshot?.ratio == null ? '—' : `${Math.round(snapshot.ratio * 100)}%`,
      `${snapshot?.participating ?? 0} of ${snapshot?.roster_size ?? 0} eligible players`,
      Users,
    ],
    [
      'Royalties',
      snapshot?.royalties ?? 0,
      `${snapshot?.threshold ?? 5} days to earn a crown`,
      Trophy,
    ],
    ['Eligible days', snapshot?.eligible_days ?? 5, 'Within the selected date range', CalendarDays],
  ] as const;
  return (
    <div className="metrics">
      {cards.map(([title, value, hint, Icon]) => (
        <div className="metric" key={title}>
          <div className="metric-top">
            {title}
            <Icon size={17} />
          </div>
          <strong>{value}</strong>
          <p>{hint}</p>
        </div>
      ))}
    </div>
  );
}

export function PlayerTable({ snapshot }: { snapshot?: Snapshot }) {
  if (!snapshot?.players.length)
    return (
      <Empty title="The leaderboard is waiting.">
        Sync your channel roster to see eligible players, including zero participation.
      </Empty>
    );
  return (
    <Table
      rows={snapshot.players}
      columns={[
        {
          label: 'Participant',
          render: (p) => (
            <div className="person">
              <span className="avatar pale">{p.name.slice(0, 1)}</span>
              <strong>{p.name}</strong>
            </div>
          ),
        },
        {
          label: 'Participation',
          render: (p) => (
            <div className="progress-cell">
              <div className="progress-track">
                <i
                  style={{
                    width: `${snapshot.eligible_days ? Math.min(100, (p.days / snapshot.eligible_days) * 100) : 0}%`,
                  }}
                />
              </div>
              <span>
                {p.days}/{snapshot.eligible_days}
              </span>
            </div>
          ),
        },
        {
          label: 'Status',
          render: (p) => (
            <Badge good={p.days >= snapshot.threshold}>
              {p.days >= snapshot.threshold ? '♛ Royalty' : p.days ? 'In progress' : 'Not started'}
            </Badge>
          ),
        },
      ]}
    />
  );
}

export function DayPicker({
  value,
  onChange,
}: {
  value: number[];
  onChange: (days: number[]) => void;
}) {
  return (
    <div className="day-picker">
      {weekdays.map((day, i) => (
        <label key={day} className={value.includes(i) ? 'selected' : ''}>
          <input
            type="checkbox"
            checked={value.includes(i)}
            onChange={() =>
              onChange(value.includes(i) ? value.filter((v) => v !== i) : [...value, i])
            }
          />
          {day}
        </label>
      ))}
    </div>
  );
}

export function Timezone({ value, onChange }: { value: string; onChange: (zone: string) => void }) {
  return (
    <select aria-label="Timezone" value={value} onChange={(e) => onChange(e.target.value)}>
      {timezones.map((zone) => (
        <option key={zone}>{zone}</option>
      ))}
    </select>
  );
}
