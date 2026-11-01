import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:18081',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: '../../.venv/bin/python ../../scripts/e2e_server.py',
      url: 'http://127.0.0.1:18080/health/live',
      timeout: 60000,
      reuseExistingServer: false,
    },
    {
      command: 'pnpm exec vite --host 127.0.0.1 --port 18081',
      url: 'http://127.0.0.1:18081',
      env: { WORDLE_API_TARGET: 'http://127.0.0.1:18080' },
      timeout: 60000,
      reuseExistingServer: false,
    },
  ],
});
