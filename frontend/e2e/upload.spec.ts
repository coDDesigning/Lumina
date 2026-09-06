import { expect, test } from '@playwright/test'

import { COURSE, open } from './support'

/**
 * Uploading is the flow everything else depends on: with no source in a course
 * there is nothing to quiz, summarise or cite. It had no browser coverage at
 * all, so a broken submit, a request built with the wrong shape, or a row that
 * never appears would have reached production unseen.
 */

const FILE = {
  name: 'week-09-dijkstra-worked-examples.pdf',
  mimeType: 'application/pdf',
  buffer: Buffer.from('%PDF-1.4 stub'),
}

test.describe('adding a source to a course', () => {
  test('sends the file and shows the new source in the list', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    const sources = page.getByRole('region', { name: 'Sources' })
    await expect(sources.getByText('lecture-07-graph-traversals.pdf')).toBeVisible()
    await expect(sources.getByText(FILE.name)).toHaveCount(0)

    const upload = page.waitForRequest(
      (request) =>
        request.method() === 'POST' &&
        request.url().endsWith(`/api/courses/${COURSE.id}/documents`),
    )

    await page.locator('section[aria-label="Sources"] input[type="file"]').setInputFiles(FILE)

    const request = await upload
    expect(request.method()).toBe('POST')

    await expect(sources.getByText(FILE.name)).toBeVisible()
    await expect(sources.getByText('lecture-07-graph-traversals.pdf')).toBeVisible()
  })

  test('sends the material kind the student chose', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByLabel('Adding as').selectOption('textbook')

    const upload = page.waitForRequest(
      (request) =>
        request.method() === 'POST' &&
        request.url().endsWith(`/api/courses/${COURSE.id}/documents`),
    )

    await page.locator('section[aria-label="Sources"] input[type="file"]').setInputFiles(FILE)

    const body = (await upload).postData() ?? ''
    expect(body).toContain('textbook')
    expect(body).toContain(FILE.name)
  })

  test('removes a source once the student confirms', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    const sources = page.getByRole('region', { name: 'Sources' })
    await sources.getByRole('button', { name: 'Remove lecture-07-graph-traversals.pdf' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Remove it' }).click()

    await expect(sources.getByText('lecture-07-graph-traversals.pdf')).toHaveCount(0)
  })
})
