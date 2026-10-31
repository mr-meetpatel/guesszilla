import { Download } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, type Envelope, type Event } from '../api';
import { useScope } from '../shell';
import { Badge, dateTime, ErrorBox } from '../ui';
import { Panel, Table, Title } from './shared';

export function LogsPage() {
  const { workspaces } = useScope();
  const [category, setCategory] = useState('');
  const [level, setLevel] = useState('');
  const [search, setSearch] = useState('');
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);
  pausedRef.current = paused;
  const [workspace, setWorkspace] = useState('');
  const [target, setTarget] = useState('');
  const [requestId, setRequestId] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [rows, setRows] = useState<Event[]>([]);
  const [error, setError] = useState('');
  const cursor = useRef(0);
  useEffect(() => {
    let cancelled = false;
    cursor.current = 0;
    setRows([]);
    async function load() {
      if (pausedRef.current || document.visibilityState === 'hidden') return;
      try {
        const filters = new URLSearchParams({
          tail: 'true',
          limit: '100',
          cursor: String(cursor.current),
          category,
          level,
          search,
          workspace_id: workspace,
          target,
          request_id: requestId,
        });
        if (start) filters.set('start', new Date(start).toISOString());
        if (end) filters.set('end', new Date(end).toISOString());
        const result = await api<Envelope<Event[]>>(`/events?${filters}`);
        if (!cancelled) {
          cursor.current = result.cursor || cursor.current;
          setRows((previous) => [...previous, ...result.data].slice(-500));
          setError('');
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Logs unavailable');
      }
    }
    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [category, level, search, workspace, target, requestId, start, end]);
  function download() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = 'wordle-events.json';
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <>
      <Title
        eyebrow="OBSERVABILITY"
        title="Nothing happens in the dark."
        action={
          <button onClick={download}>
            <Download size={15} /> Export visible
          </button>
        }
      >
        Redacted application logs, delivery events, and an audit trail of admin actions.
      </Title>
      <div className="period-bar">
        <div className="row">
          <input
            aria-label="Search logs"
            placeholder="Search event names…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            aria-label="Log category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">All categories</option>
            {['log', 'audit', 'delivery', 'job'].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <select aria-label="Log level" value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">All levels</option>
            {['INFO', 'WARNING', 'ERROR'].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <button onClick={() => setPaused(!paused)}>{paused ? 'Resume tail' : 'Pause tail'}</button>
      </div>
      <div className="form-grid log-filters">
        <label>
          Workspace
          <select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
            <option value="">All workspaces</option>
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Target or job ID
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="Exact ID"
          />
        </label>
        <label>
          Request ID
          <input
            value={requestId}
            onChange={(e) => setRequestId(e.target.value)}
            placeholder="Exact request ID"
          />
        </label>
        <label>
          From (your local time)
          <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          To (your local time)
          <input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {error && <ErrorBox error={new Error(error)} />}
      <Panel
        title="Event stream"
        caption="Latest 500 events in this view. Live tail updates every five seconds."
      >
        <Table
          rows={[...rows].reverse()}
          columns={[
            {
              label: 'Time',
              render: (e) => <span className="mono tiny">{dateTime(e.created_at)}</span>,
            },
            { label: 'Level', render: (e) => <Badge good={e.level === 'INFO'}>{e.level}</Badge> },
            { label: 'Event', render: (e) => <strong>{e.action}</strong> },
            { label: 'Category', render: (e) => e.category },
            {
              label: 'Context',
              render: (e) => (
                <details>
                  <summary className="mono tiny">{e.target || e.request_id || 'Details'}</summary>
                  <pre>{JSON.stringify(e.details, null, 2)}</pre>
                </details>
              ),
            },
          ]}
        />
      </Panel>
    </>
  );
}
