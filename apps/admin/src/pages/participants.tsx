import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { api, write, type Envelope, type Player } from '../api';
import { Empty, ErrorBox, useAction } from '../ui';
import { Metrics, Panel, Period, Status, Table, Title, usePeriod } from './shared';

export function ParticipantsPage() {
  const period = usePeriod();
  const { channel } = period;
  const action = useAction();
  const roster = useQuery({
    queryKey: ['roster', channel?.id],
    queryFn: () => api<Envelope<Player[]>>(`/channels/${channel?.id}/participants`),
    enabled: !!channel,
  });
  return (
    <>
      <Title
        eyebrow="PEOPLE & PROGRESS"
        title="Every day counts."
        action={
          channel && (
            <button onClick={() => action.mutate(() => write(`/channels/${channel.id}/sync`, {}))}>
              <RefreshCw size={15} /> Sync roster
            </button>
          )
        }
      >
        A clear view of who’s showing up, with eligibility you control.
      </Title>
      <Period period={period} />
      <Metrics snapshot={period.snapshot} />
      {period.query.error && <ErrorBox error={period.query.error} />}
      <Panel
        title="Participation calendar"
        caption="A filled square means at least one credited Wordle that local day."
      >
        {period.snapshot?.players.length ? (
          <Table
            rows={period.snapshot.players}
            columns={[
              { label: 'Participant', render: (p) => <strong>{p.name}</strong> },
              ...period.snapshot.eligible_dates.map((day) => ({
                label: day.slice(5),
                render: (p: Player) => (
                  <span className={'calendar-cell ' + (p.dates.includes(day) ? 'filled' : '')}>
                    {p.dates.includes(day) ? '✓' : '·'}
                  </span>
                ),
              })),
              {
                label: 'Total',
                render: (p) => (
                  <strong>
                    {p.days}/{period.snapshot?.eligible_days}
                  </strong>
                ),
              },
            ]}
          />
        ) : (
          <Empty title="No eligible participants." />
        )}
      </Panel>
      <Panel
        title="Eligible roster"
        caption="New human members are included by default after sync. Historical report rosters stay frozen."
      >
        {roster.error ? (
          <ErrorBox error={roster.error} />
        ) : (
          <Table
            rows={roster.data?.data || []}
            columns={[
              { label: 'Name', render: (p) => <strong>{p.name}</strong> },
              {
                label: 'Slack ID',
                render: (p) => <span className="mono subtle">{p.user_id}</span>,
              },
              {
                label: 'Membership',
                render: (p) => <Status value={p.active ? 'Active' : 'inactive'} />,
              },
              {
                label: 'Included in reports',
                render: (p) => (
                  <input
                    aria-label={`Include ${p.name}`}
                    type="checkbox"
                    checked={p.included}
                    disabled={action.isPending}
                    onChange={(e) =>
                      action.mutate(() =>
                        write(
                          `/channels/${channel?.id}/participants/${p.user_id}`,
                          { included: e.target.checked },
                          'PATCH',
                        ),
                      )
                    }
                  />
                ),
              },
            ]}
          />
        )}
      </Panel>
    </>
  );
}
