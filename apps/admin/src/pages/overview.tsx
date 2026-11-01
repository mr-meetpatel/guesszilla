import { useQuery } from '@tanstack/react-query';
import { ArrowUpRight, CheckCircle2, Clock3 } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type Envelope, type Overview } from '../api';
import { Badge, Empty, ErrorBox } from '../ui';
import { Metrics, Panel, Period, PlayerTable, Status, Title, usePeriod } from './shared';

export function OverviewPage() {
  const period = usePeriod();
  const snapshot = period.snapshot;
  const health = useQuery({
    queryKey: ['overview'],
    queryFn: () => api<Envelope<Overview>>('/overview'),
  });
  const chart =
    snapshot?.eligible_dates.map((day) => ({
      day: new Intl.DateTimeFormat('en', { weekday: 'short' }).format(new Date(day + 'T12:00:00')),
      players: snapshot.players.filter((p) => p.dates.includes(day)).length,
    })) || [];
  return (
    <>
      <Title
        eyebrow="THE BIG PICTURE"
        title="A good week starts here."
        action={<Badge good>Workspace overview</Badge>}
      >
        A pulse on participation, your players, and the systems behind them.
      </Title>
      <Period period={period} />
      {health.error && <ErrorBox error={health.error} />}
      <Metrics snapshot={snapshot} />
      <div className="dashboard-grid">
        <Panel
          title="Consistency, one day at a time"
          caption="Eligible players who shared a Wordle each day"
        >
          <div className="chart">
            {chart.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart}>
                  <CartesianGrid vertical={false} stroke="#edf0eb" />
                  <XAxis
                    dataKey="day"
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 12, fill: '#7b837c' }}
                  />
                  <YAxis
                    allowDecimals={false}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fontSize: 12, fill: '#7b837c' }}
                  />
                  <Tooltip cursor={{ fill: '#f3f5f0' }} />
                  <Bar
                    dataKey="players"
                    fill="#789166"
                    radius={[5, 5, 0, 0]}
                    maxBarSize={48}
                    isAnimationActive={false}
                  />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Empty title="Your next streak starts here." />
            )}
          </div>
        </Panel>
        <Panel title="System pulse" caption="Live component health">
          <div className="health-list">
            {[
              ['API & database', health.data?.data.database === 'healthy'],
              ['Queue worker', health.data?.data.worker],
              ['Schedule runner', health.data?.data.scheduler],
            ].map(([name, ready]) => (
              <div key={String(name)}>
                <span>
                  <i className={ready ? 'dot green' : 'dot amber'} />
                  {name}
                </span>
                <Status value={ready ? 'healthy' : 'unavailable'} />
              </div>
            ))}
            <div>
              <span>Uncertain deliveries</span>
              <strong>{health.data?.data.deliveries.uncertain || 0}</strong>
            </div>
            <div>
              <span>Failed jobs</span>
              <strong>{health.data?.data.jobs.failed || 0}</strong>
            </div>
          </div>
        </Panel>
      </div>
      {health.data?.data.alerts.length ? (
        <div className="alerts">
          {health.data.data.alerts.map((alert) => (
            <p key={alert}>
              <Clock3 size={15} />
              {alert}
            </p>
          ))}
        </div>
      ) : (
        <div className="all-clear">
          <CheckCircle2 size={17} /> All caught up. No operational alerts.
        </div>
      )}
      <Panel
        title="This week’s people"
        caption="A little progress deserves a little recognition"
        action={
          <a href="/participants">
            View participants <ArrowUpRight size={15} />
          </a>
        }
      >
        {period.query.error ? (
          <ErrorBox error={period.query.error} />
        ) : (
          <PlayerTable snapshot={snapshot} />
        )}
      </Panel>
    </>
  );
}
