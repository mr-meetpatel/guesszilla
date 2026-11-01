import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { api, write, type Delivery, type Envelope, type Report } from '../api';
import { Confirm, dateTime, ErrorBox, Loading, Modal, useAction } from '../ui';
import { Panel, Period, Status, Table, Title, usePeriod } from './shared';

export function ReportsPage() {
  const period = usePeriod();
  const action = useAction();
  const { channel } = period;
  const [cursor, setCursor] = useState(0);
  const reports = useQuery({
    queryKey: ['reports', channel?.id, cursor],
    queryFn: () => api<Envelope<Report[]>>(`/reports?channel_id=${channel?.id}&cursor=${cursor}`),
    enabled: !!channel,
  });
  return (
    <>
      <Title eyebrow="THE WEEKLY RITUAL" title="Progress worth sharing.">
        Preview the message, review its delivery, and keep every report accountable.
      </Title>
      <Period period={period} />
      {period.query.error && <ErrorBox error={period.query.error} />}
      <div className="dashboard-grid">
        <Panel
          title="Report preview"
          caption="Preview uses current rules and roster. Sending creates an immutable snapshot."
        >
          <div className="slack-preview">
            <div className="slack-bot">W</div>
            <div>
              <strong>
                WordleCounterBot <span>APP</span>
              </strong>
              {period.snapshot && (
                <>
                  <p>
                    Stats for Attempted WORDLE for the week of {period.start} to {period.end}
                    <br />
                    There are {period.snapshot.royalties} royalties this week.. 👑
                    <br />
                    Check your stats below 🎉
                  </p>
                  <strong>Leaderboard 🏆</strong>
                  {period.snapshot.players
                    .filter((p) => p.days >= period.snapshot!.threshold)
                    .map((p) => (
                      <p key={p.user_id}>- @{p.name}</p>
                    ))}
                  <strong>Remaining Players List 👏</strong>
                  {period.snapshot.players
                    .filter((p) => p.days < period.snapshot!.threshold)
                    .map((p) => (
                      <p key={p.user_id}>
                        - {p.name} ({p.days}/{period.snapshot?.eligible_days})
                      </p>
                    ))}
                </>
              )}
            </div>
          </div>
        </Panel>
        <Panel
          title="Ready when you are"
          caption="Manual and scheduled sends share the same duplicate protection."
        >
          <p className="body-copy">
            A report already queued or delivered for these dates is reused. An intentional resend
            needs a new, confirmed revision.
          </p>
          <Confirm
            label="Send stats to Slack"
            disabled={!channel || !period.snapshot}
            description={`Send the ${period.start} to ${period.end} report to #${channel?.name}? This may notify the channel.`}
            onConfirm={() =>
              write(
                `/channels/${channel?.id}/reports`,
                { start: period.start, end: period.end },
                'POST',
                { 'Idempotency-Key': crypto.randomUUID() },
              )
            }
          />
          <p className="tiny">
            Destination: #{channel?.name || 'Select a channel'}
            <br />
            Timezone: {channel?.rules.timezone}
            <br />
            Royalty threshold: {channel?.rules.threshold} days
          </p>
        </Panel>
      </div>
      <Panel
        title="Report history"
        caption="Each revision preserves its roster, rules, counts, content, and receipts."
      >
        {reports.error ? (
          <ErrorBox error={reports.error} />
        ) : (
          <Table
            rows={reports.data?.data || []}
            columns={[
              {
                label: 'Period',
                render: (r) => (
                  <strong>
                    {r.start} → {r.end}
                  </strong>
                ),
              },
              { label: 'Revision', render: (r) => `#${r.revision}` },
              { label: 'Status', render: (r) => <Status value={r.status} /> },
              { label: 'Created', render: (r) => dateTime(r.created_at, channel?.rules.timezone) },
              {
                label: 'Actions',
                render: (r) => (
                  <div className="row">
                    <Modal
                      title="Delivery receipts"
                      description="Confirmed parts are never reposted. An uncertain send requires reconciliation."
                      trigger={<button>View delivery</button>}
                    >
                      <DeliveryView report={r} />
                    </Modal>
                    <Confirm
                      label="Resend"
                      disabled={!['delivered', 'failed', 'cancelled'].includes(r.status)}
                      description="This intentionally posts a new report revision. New rules, roster and participation are evaluated. Other administrators racing this action will receive a conflict."
                      onConfirm={() =>
                        write(
                          `/channels/${r.channel_id}/reports`,
                          {
                            start: r.start,
                            end: r.end,
                            expected_revision: r.revision,
                            confirm_resend: true,
                          },
                          'POST',
                          { 'Idempotency-Key': crypto.randomUUID() },
                        )
                      }
                    />
                  </div>
                ),
              },
            ]}
          />
        )}
        <div className="pagination">
          <button disabled={!cursor} onClick={() => setCursor(Math.max(0, cursor - 50))}>
            Previous
          </button>
          <button
            disabled={reports.data?.cursor == null}
            onClick={() => setCursor(reports.data?.cursor || 0)}
          >
            Next <ChevronRight size={14} />
          </button>
        </div>
      </Panel>
    </>
  );
}

function DeliveryView({ report }: { report: Report }) {
  const action = useAction();
  const query = useQuery({
    queryKey: ['deliveries', report.id],
    queryFn: () => api<Envelope<Delivery[]>>(`/reports/${report.id}/deliveries`),
  });
  if (query.isPending) return <Loading />;
  return (
    <>
      {query.error && <ErrorBox error={query.error} />}
      <p className="tiny">
        Snapshot cutoff: {dateTime(report.snapshot.cutoff)} · {report.snapshot.timezone}
        <br />
        Roster captured from sync: {dateTime(report.snapshot.roster_synced_at)}
      </p>
      {query.data?.data.map((d) => (
        <div key={d.id} className="delivery-card">
          <div className="row spread">
            <strong>Part {d.part + 1}</strong>
            <Status value={d.status} />
          </div>
          <p className="tiny">
            Attempts: {d.attempts} · Receipt: {d.slack_ts || 'Not confirmed'}
          </p>
          <pre>{d.text}</pre>
          {d.last_error && <p className="error">{d.last_error}</p>}
          {d.status === 'uncertain' && (
            <button
              disabled={action.isPending}
              onClick={() => action.mutate(() => write(`/deliveries/${d.id}/reconcile`, {}))}
            >
              Check Slack history
            </button>
          )}
          {d.status === 'failed' && (
            <button onClick={() => action.mutate(() => write(`/deliveries/${d.id}/retry`, {}))}>
              Retry confirmed failure
            </button>
          )}
        </div>
      ))}
    </>
  );
}
