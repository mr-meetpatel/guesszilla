import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('test-admin-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'A good week starts here.' })).toBeVisible();
});

test('dashboard and participant roster include zero players and alphabetical names', async ({
  page,
}) => {
  await expect(page.getByText('67%', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/overview.png', fullPage: true });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'A good week starts here.' })).toBeVisible();
  await page.getByRole('link', { name: 'Participants', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Every day counts.' })).toBeVisible();
  const firstTable = page.locator('table').first();
  await expect(firstTable.locator('tbody tr').nth(0)).toContainText('Amy');
  await expect(firstTable.locator('tbody tr').nth(1)).toContainText('Bob');
  await expect(firstTable.locator('tbody tr').nth(2)).toContainText('Charlie');
  await expect(firstTable.locator('tbody tr').nth(2)).toContainText('0/5');
});

test('create a paused schedule and inspect future occurrences', async ({ page }) => {
  await page.getByRole('link', { name: 'Schedules', exact: true }).click();
  await page.getByRole('button', { name: 'New schedule' }).click();
  await page.getByLabel('Name', { exact: true }).fill('Acceptance Friday report');
  await page.getByLabel('Send at').fill('17:00');
  await page.getByRole('button', { name: 'Save schedule' }).click();
  await expect(page.getByRole('status')).toContainText('Changes saved');
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Acceptance Friday report' })).toBeVisible();
  await expect(page.getByText('Paused', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'History', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Next five occurrences' })).toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(5);
});

test('manual repeated sends reuse the same report and expose per-part receipts', async ({
  page,
}) => {
  await page.getByRole('link', { name: 'Reports', exact: true }).click();
  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: 'Send stats to Slack', exact: true }).click();
    await page.getByRole('button', { name: 'Confirm', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Changes saved');
    await expect(page.locator('tbody tr')).toHaveCount(1);
  }
  await page.getByRole('button', { name: 'View delivery' }).click();
  await expect(page.getByRole('heading', { name: 'Delivery receipts' })).toBeVisible();
  await expect(page.getByText('Not confirmed', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry confirmed failure' })).toHaveCount(0);
});

test('audit UI is disabled by default and session management remains available', async ({
  page,
}) => {
  await expect(page.getByRole('link', { name: 'Logs & audit' })).toHaveCount(0);
  await page.goto('/logs');
  await expect(page).toHaveURL('/');
  await page.getByRole('link', { name: 'Administration', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Active session families' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
});

test('mobile navigation keeps participant controls accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Toggle navigation' }).click();
  await page.getByRole('link', { name: 'Participants', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Every day counts.' })).toBeVisible();
  await expect(page.getByLabel('Include Charlie')).toBeVisible();
  await page.screenshot({ path: 'test-results/mobile-participants.png', fullPage: true });
});
