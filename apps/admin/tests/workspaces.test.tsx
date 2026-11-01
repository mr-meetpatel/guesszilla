import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { WorkspacesPage } from '../src/pages/workspaces';

vi.mock('../src/shell', () => ({
  useScope: () => ({ workspaces: [], channels: [], workspace: undefined }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

it('offers channels for the OAuth workspace and tracks only the selected channel', async () => {
  const updates: unknown[] = [];
  server.use(
    http.get('/api/v1/channels', ({ request }) => {
      expect(new URL(request.url).searchParams.get('workspace_id')).toBe('TNEW');
      return HttpResponse.json({
        data: [
          { id: 'channel-1', name: 'wordle', version: 3 },
          { id: 'channel-2', name: 'general', version: 1 },
        ],
      });
    }),
    http.patch('/api/v1/channels/:id', async ({ request, params }) => {
      updates.push({ id: params.id, body: await request.json() });
      return HttpResponse.json({ data: { ok: true } });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={['/workspaces?oauth=connected&workspace_id=TNEW']}>
        <WorkspacesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole('option', { name: '# wordle' });
  expect(screen.getByRole('button', { name: 'Connect channel' })).toBeDisabled();
  expect(updates).toEqual([]);
  await userEvent.selectOptions(screen.getByLabelText('Slack channel'), 'channel-1');
  await userEvent.type(screen.getByLabelText('Slash-command password'), 'a-channel-password');
  await userEvent.click(screen.getByRole('button', { name: 'Connect channel' }));
  await waitFor(() =>
    expect(screen.queryByText('Choose a channel to connect')).not.toBeInTheDocument(),
  );
  expect(updates).toEqual([
    { id: 'channel-1', body: { version: 3, active: true, password: 'a-channel-password' } },
  ]);
  expect(screen.queryByText('Choose a channel to connect')).not.toBeInTheDocument();
});
