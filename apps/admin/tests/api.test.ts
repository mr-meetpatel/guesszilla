import { http, HttpResponse, delay } from 'msw';
import { setupServer } from 'msw/node';
import { api } from '../src/api';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

it('refreshes an expired access token before revoking the session on sign-out', async () => {
  let refreshed = false;
  let logouts = 0;
  server.use(
    http.post('/api/v1/auth/refresh', () => {
      refreshed = true;
      return HttpResponse.json({ ok: true });
    }),
    http.post('/api/v1/auth/logout', () => {
      logouts++;
      return HttpResponse.json({ ok: refreshed }, { status: refreshed ? 200 : 401 });
    }),
  );
  expect(await api('/auth/logout', { method: 'POST' })).toEqual({ ok: true });
  expect(logouts).toBe(2);
});

it('shares one refresh across concurrent expired requests', async () => {
  let refreshed = false;
  let refreshes = 0;
  server.use(
    http.post('/api/v1/auth/refresh', async () => {
      refreshes++;
      await delay(25);
      refreshed = true;
      return HttpResponse.json({ ok: true });
    }),
    http.get('/api/v1/overview', () =>
      HttpResponse.json({ ok: refreshed }, { status: refreshed ? 200 : 401 }),
    ),
  );
  const responses = await Promise.all([api('/overview'), api('/overview')]);
  expect(responses).toEqual([{ ok: true }, { ok: true }]);
  expect(refreshes).toBe(1);
});
