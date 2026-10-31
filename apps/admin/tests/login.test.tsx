import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { Login } from '../src/auth';

const server = setupServer(
  http.post('/api/v1/auth/login', () =>
    HttpResponse.json({ error: 'Invalid credentials' }, { status: 401 }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

it('shows a generic invalid-credentials error without leaking the password', async () => {
  render(<Login onSuccess={() => {}} />);
  await userEvent.type(screen.getByLabelText('Username'), 'admin');
  await userEvent.type(screen.getByLabelText('Password'), 'wrong-password');
  await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid credentials');
  expect(screen.getByRole('alert')).not.toHaveTextContent('wrong-password');
});

it('never refreshes a failed login request', async () => {
  let calls = 0;
  server.use(
    http.post('/api/v1/auth/refresh', () => {
      calls++;
      return HttpResponse.json({}, { status: 401 });
    }),
  );
  render(<Login onSuccess={() => {}} />);
  await userEvent.type(screen.getByLabelText('Username'), 'admin');
  await userEvent.type(screen.getByLabelText('Password'), 'wrong-password');
  await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await screen.findByRole('alert');
  expect(calls).toBe(0);
});
