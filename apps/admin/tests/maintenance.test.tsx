import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { CleanupForm } from '../src/pages/maintenance';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
function show(component: React.ReactNode) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {component}
    </QueryClientProvider>,
  );
}

it('requires explicit workspace confirmation and password before submitting cleanup', async () => {
  let saved: unknown;
  server.use(
    http.post('/api/v1/workspaces/T1/cleanup', async ({ request }) => {
      saved = await request.json();
      return HttpResponse.json({ data: { deleted: {} } });
    }),
  );
  show(<CleanupForm workspaceId="T1" />);
  await userEvent.click(
    screen.getByRole('button', { name: 'Permanently clear workspace history' }),
  );
  expect(saved).toBeUndefined();
  await userEvent.type(screen.getByLabelText('Type T1 to confirm'), 'T1');
  await userEvent.type(screen.getByLabelText('Current admin password'), 'admin-password');
  await userEvent.click(
    screen.getByRole('button', { name: 'Permanently clear workspace history' }),
  );
  await screen.findByText('Changes saved. You can close this form.');
  expect(saved).toEqual({ confirmation: 'T1', password: 'admin-password' });
});
