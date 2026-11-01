import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { usePeriod } from '../src/pages/shared';

const scope = vi.hoisted(() => ({
  channel: {
    id: 'channel-1',
    rules: { timezone: 'Pacific/Kiritimati', weekdays: [0, 1, 2, 3, 4] },
  },
}));
vi.mock('../src/shell', () => ({ useScope: () => scope }));
const server = setupServer(
  http.get('/api/v1/channels/:id/analytics', () => HttpResponse.json({ data: {} })),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

it('updates the shared period when eligible weekdays change on the same channel', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result, rerender } = renderHook(() => usePeriod(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  const monday = result.current.start;
  const span = () => (Date.parse(result.current.end) - Date.parse(result.current.start)) / 86400000;
  expect(new Date(monday).getUTCDay()).toBe(1);
  expect(span()).toBe(4);
  scope.channel = {
    ...scope.channel,
    rules: { ...scope.channel.rules, weekdays: [0, 1, 2, 3, 4, 5, 6] },
  };
  rerender();
  await waitFor(() => expect(span()).toBe(6));
  expect(result.current.start).toBe(monday);
  scope.channel = { ...scope.channel, rules: { ...scope.channel.rules, weekdays: [5, 6] } };
  rerender();
  await waitFor(() => expect(new Date(result.current.start).getUTCDay()).toBe(6));
  expect(span()).toBe(1);
  await waitFor(() => expect(result.current.query.isSuccess).toBe(true));
});
