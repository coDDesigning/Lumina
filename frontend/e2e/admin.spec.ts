import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import { open } from './support'

/**
 * The admin screen spends credits, bans accounts and changes roles — the three
 * most consequential writes in the product, and the ones a mistake is hardest
 * to undo. None had browser coverage.
 *
 * Every flow is driven against Ada rather than the signed-in administrator,
 * whose own row disables these controls so nobody can ban or demote themselves.
 */

const ADA = 'ada@example.com'

const notifications = (page: Page) => page.getByRole('region', { name: 'Notifications' })
const adaRow = (page: Page) => page.getByRole('row', { name: new RegExp(ADA) })

test.describe('adjusting an account balance', () => {
  test('applies the change and shows the new balance', async ({ page }) => {
    await open(page, '/admin')

    await expect(adaRow(page)).toContainText('6')

    await adaRow(page).getByRole('button', { name: 'Credits' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    await dialog.getByLabel('Credit change').fill('10')
    await dialog.getByLabel('Reason').selectOption('admin_grant')
    await dialog.getByLabel(/^Note/).fill('Compensating a failed generation.')

    const applied = page.waitForRequest(
      (request) =>
        request.method() === 'POST' && request.url().includes('/credits'),
    )

    await dialog.getByRole('button', { name: 'Apply' }).click()

    expect((await applied).postDataJSON()).toEqual({
      delta: 10,
      reason: 'admin_grant',
      note: 'Compensating a failed generation.',
    })

    await expect(notifications(page)).toContainText('Balance now 16')
    await expect(adaRow(page)).toContainText('16')
  })

  test('refuses to take credits away through a grant', async ({ page }) => {
    await open(page, '/admin')

    await adaRow(page).getByRole('button', { name: 'Credits' }).click()

    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Credit change').fill('-5')
    await dialog.getByLabel('Reason').selectOption('admin_grant')
    await dialog.getByRole('button', { name: 'Apply' }).click()

    await expect(dialog.getByRole('alert')).toContainText('can only add credits')
    await expect(adaRow(page)).toContainText('6')
  })
})

test.describe('banning an account', () => {
  test('asks first, then bans and says so', async ({ page }) => {
    await open(page, '/admin')

    await adaRow(page).getByRole('button', { name: 'Ban' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(ADA)

    const banned = page.waitForRequest(
      (request) => request.method() === 'PUT' && request.url().includes('/ban'),
    )

    await dialog.getByRole('button', { name: 'Ban', exact: true }).click()

    expect(new URL((await banned).url()).searchParams.get('is_banned')).toBe('true')
    await expect(notifications(page)).toContainText('Account banned')
    await expect(adaRow(page).getByRole('button', { name: 'Unban' })).toBeVisible()
  })
})

test.describe('changing a role', () => {
  test('promotes the account and shows the new role', async ({ page }) => {
    await open(page, '/admin')

    await expect(adaRow(page)).toContainText('Student')

    const promoted = page.waitForRequest(
      (request) => request.method() === 'PUT' && request.url().includes('/role'),
    )

    await adaRow(page).getByRole('button', { name: 'Promote' }).click()

    expect(new URL((await promoted).url()).searchParams.get('role')).toBe('admin')
    await expect(notifications(page)).toContainText('Role set to admin')
    await expect(adaRow(page).getByRole('button', { name: 'Demote' })).toBeVisible()
  })
})
