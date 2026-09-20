import { expect, test } from '@playwright/test';
import { open } from './support';

const ROUTE = '/admin/system-settings';

function notifications(page: import('@playwright/test').Page) {
  return page.getByRole('region', { name: 'Notifications' });
}

test('lists every supported key with its source and locks the ones a restart cannot apply', async ({
  page,
}) => {
  await open(page, ROUTE);

  await expect(page.getByRole('heading', { name: 'System settings', level: 1 })).toBeVisible();
  await expect(page.getByText('RETRIEVAL_CHUNK_LIMIT')).toBeVisible();
  await expect(page.getByText('LUMINA_PORT')).toBeVisible();
  await expect(
    page.getByText('Requires editing .env and re-running docker compose up -d.'),
  ).toBeVisible();
  await expect(page.getByLabel('Published port value')).toHaveCount(0);
});

test('never prefills a secret', async ({ page }) => {
  await open(page, ROUTE);

  await expect(
    page.getByRole('textbox', { name: 'Gemini API key value' }),
  ).toHaveValue('');
  await expect(page.getByText(/Leave blank to keep the current value/)).toBeVisible();
});

test('saves a change, reports it pending, then restarts and confirms it is active', async ({
  page,
}) => {
  await open(page, ROUTE);

  const field = page.getByLabel('Retrieval chunk limit value');
  await field.fill('48');

  const saved = page.waitForRequest(
    (request) =>
      request.method() === 'PATCH' && request.url().includes('/api/admin/system-settings'),
  );
  await page.getByRole('button', { name: 'Save 1 change' }).click();
  expect((await saved).postDataJSON()).toEqual({
    expected_revision: 0,
    values: { RETRIEVAL_CHUNK_LIMIT: '48' },
  });

  await expect(notifications(page).getByText('Settings saved')).toBeVisible();
  await expect(page.getByText('Changes are waiting for a restart')).toBeVisible();

  const requested = page.waitForRequest(
    (request) =>
      request.method() === 'POST' && request.url().includes('/system-settings/restarts'),
  );
  await page.getByRole('button', { name: 'Restart Lumina' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('RETRIEVAL_CHUNK_LIMIT')).toBeVisible();
  await dialog.getByLabel(/Type RESTART/).fill('RESTART');
  await dialog.getByRole('button', { name: 'Restart now' }).click();

  expect((await requested).postDataJSON()).toEqual({ expected_revision: 1 });
  await expect(notifications(page).getByText('Restart requested')).toBeVisible();
  await expect(notifications(page).getByText('Lumina restarted')).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText('Changes are waiting for a restart')).toHaveCount(0);
});

test('resets an override back to the deployment value', async ({ page }) => {
  await open(page, ROUTE);

  await page.getByLabel('Retrieval chunk limit value').fill('48');
  await page.getByRole('button', { name: 'Save 1 change' }).click();
  await expect(notifications(page).getByText('Settings saved')).toBeVisible();

  const reset = page.waitForRequest(
    (request) =>
      request.method() === 'DELETE' &&
      request.url().includes('/system-settings/RETRIEVAL_CHUNK_LIMIT'),
  );
  await page.getByRole('button', { name: 'Reset RETRIEVAL_CHUNK_LIMIT' }).click();
  await reset;

  await expect(
    notifications(page).getByText('RETRIEVAL_CHUNK_LIMIT reset'),
  ).toBeVisible();
  await expect(page.getByLabel('Retrieval chunk limit value')).toHaveValue('24');
});

test('filters the inventory by search term', async ({ page }) => {
  await open(page, ROUTE);
  await expect(page.getByText('Showing 3 of 3 settings')).toBeVisible();

  await page.getByPlaceholder('Search by name, key or description').fill('gemini');

  await expect(page.getByText('Showing 1 of 3 settings')).toBeVisible();
  await expect(page.getByText('RETRIEVAL_CHUNK_LIMIT')).toHaveCount(0);
});

test('refuses to restart while edits are unsaved', async ({ page }) => {
  await open(page, ROUTE);

  await page.getByLabel('Retrieval chunk limit value').fill('48');
  await page.getByRole('button', { name: 'Save 1 change' }).click();
  await expect(notifications(page).getByText('Settings saved')).toBeVisible();
  await page.getByLabel('Retrieval chunk limit value').fill('64');

  await expect(
    page.getByText('Save or discard your edits before restarting.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restart Lumina' })).toHaveCount(0);
});
