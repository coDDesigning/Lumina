import { expect, test } from '@playwright/test'

import { open } from './support'

/**
 * Background notes steer every generation in the product, so a note that looks
 * saved but was not silently changes what the student is taught. The screen had
 * no browser coverage, and the fixture layer answered a create with the whole
 * list, which is a shape the screen cannot read.
 */

test.describe('keeping background notes', () => {
  test('adds a note and lists it', async ({ page }) => {
    await open(page, '/account/background')

    await expect(page.getByText('University and department')).toBeVisible()
    await expect(page.getByText('How exams are set')).toHaveCount(0)

    await page.getByRole('button', { name: 'Add a note' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByLabel('Topic Name').fill('How exams are set')
    await dialog
      .getByLabel('Knowledge Details & Background')
      .fill('Two-hour written papers, no notes allowed.')

    const created = page.waitForRequest(
      (request) =>
        request.method() === 'POST' && request.url().includes('/api/profile-knowledge'),
    )

    await dialog.getByRole('button', { name: 'Save Topic' }).click()

    expect((await created).postDataJSON()).toEqual({
      topic: 'How exams are set',
      detail: 'Two-hour written papers, no notes allowed.',
    })

    await expect(page.getByText('How exams are set')).toBeVisible()
    await expect(page.getByText('Two-hour written papers, no notes allowed.')).toBeVisible()
    await expect(page.getByRole('status')).toContainText('Knowledge topic added successfully.')
  })

  test('keeps the added note after the screen is reloaded', async ({ page }) => {
    await open(page, '/account/background')

    await page.getByRole('button', { name: 'Add a note' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('Topic Name').fill('How exams are set')
    await dialog.getByLabel('Knowledge Details & Background').fill('Two-hour written papers.')
    await dialog.getByRole('button', { name: 'Save Topic' }).click()

    await expect(page.getByText('How exams are set')).toBeVisible()

    await page.reload()

    await expect(page.getByText('How exams are set')).toBeVisible()
  })

  test('removes a note once the student confirms', async ({ page }) => {
    await open(page, '/account/background')

    await page.getByRole('button', { name: 'Delete University and department' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()

    const removed = page.waitForRequest(
      (request) =>
        request.method() === 'DELETE' && request.url().includes('/api/profile-knowledge/'),
    )

    await dialog.getByRole('button', { name: 'Remove', exact: true }).click()

    await removed
    await expect(page.getByText('University and department')).toHaveCount(0)
    await expect(page.getByRole('status')).toContainText('Knowledge topic removed.')
  })

  test('imports several notes at once', async ({ page }) => {
    await open(page, '/account/background')

    await page.getByRole('button', { name: 'Paste several' }).click()

    const dialog = page.getByRole('dialog')
    await dialog
      .getByLabel('Your notes')
      .fill('How exams are set: two-hour papers\nStudy time: about six hours a week')

    const imported = page.waitForRequest(
      (request) =>
        request.method() === 'POST' && request.url().includes('/profile-knowledge/import'),
    )

    await dialog.getByRole('button', { name: /Save 2 notes/ }).click()

    expect((await imported).postDataJSON()).toEqual({
      items: [
        { topic: 'How exams are set', detail: 'two-hour papers' },
        { topic: 'Study time', detail: 'about six hours a week' },
      ],
    })

    await expect(page.getByText('How exams are set')).toBeVisible()
    await expect(page.getByText('Study time')).toBeVisible()
  })
})
