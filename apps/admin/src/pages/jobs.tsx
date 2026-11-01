import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, write, type Envelope, type Job } from '../api';
import { dateTime, ErrorBox, useAction } from '../ui';
import { Panel, Status, Table, Title } from './shared';

export function JobsPage() {
  const [status, setStatus] = useState('');
  const [cursor, setCursor] = useState(0);
  const action = useAction();
  const query = useQuery({
    queryKey: ['jobs', status, cursor],
    queryFn: () => api<Envelope<Job[]>>(`/jobs?cursor=${cursor}&status=${status}`),
  });
  return (
    <>
      <Title eyebrow="BEHIND THE SCENES" title="Every job, accounted for.">
        Track work, inspect failures, and retry only when it’s safe.
      </Title>
      <div className="period-bar">
        <strong>Job history</strong>
        <select
          aria-label="Job status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setCursor(0);
          }}
        >
          <option value="">All states</option>
          {['pending', 'running', 'done', 'failed'].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <Panel
        title="Background jobs"
        caption="Report deliveries have their own receipt-aware retry controls on the Reports screen."
      >
        {query.error && <ErrorBox error={query.error} />}
        <Table
          rows={query.data?.data || []}
          columns={[
            { label: 'Job', render: (j) => <strong>#{j.id}</strong> },
            { label: 'Type', render: (j) => j.kind.replaceAll('_', ' ') },
            { label: 'Status', render: (j) => <Status value={j.status} /> },
            { label: 'Attempts', render: (j) => j.attempts },
            { label: 'Created', render: (j) => dateTime(j.created_at) },
            { label: 'Last error', render: (j) => j.last_error || '—' },
            {
              label: 'Action',
              render: (j) =>
                j.status === 'failed' && j.kind !== 'report' ? (
                  <button
                    disabled={action.isPending}
                    onClick={() => action.mutate(() => write(`/jobs/${j.id}/retry`, {}))}
                  >
                    Retry
                  </button>
                ) : (
                  <span className="subtle">—</span>
                ),
            },
          ]}
        />
        <div className="pagination">
          <button disabled={!cursor} onClick={() => setCursor(Math.max(0, cursor - 50))}>
            Previous
          </button>
          <button
            disabled={query.data?.cursor == null}
            onClick={() => setCursor(query.data?.cursor || 0)}
          >
            Next
          </button>
        </div>
      </Panel>
    </>
  );
}
