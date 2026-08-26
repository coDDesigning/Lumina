import { expect, test } from '@playwright/test'
import { COURSE, open } from './support'

/**
 * A citation is only worth anything if a student can read it, so this covers
 * the two places a source is rendered from a real response in a real browser:
 * an answer in the workspace thread, and a question in the quiz review.
 */

test.describe('source citations', () => {
  test('names the document and pages behind an answer', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('textbox', { name: 'Enter prompt' }).fill('How does BFS work?')
    await page.getByRole('button', { name: 'Send' }).click()

    await expect(page.getByText(/Breadth-first search settles vertices/)).toBeVisible()
    await expect(page.getByText('Lecture 4 · pp. 12–14')).toBeVisible()
  })

  test('never renders a citation marker as raw text', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('textbox', { name: 'Enter prompt' }).fill('How does BFS work?')
    await page.getByRole('button', { name: 'Send' }).click()

    await expect(page.getByText(/Breadth-first search settles vertices/)).toBeVisible()
    await expect(page.getByText('[S1]', { exact: true })).toHaveCount(0)
  })

  test('names the source of a question in the quiz review', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}/practice/4/attempts/12`)

    await expect(page.getByText('Lecture 4 · pp. 12–14').first()).toBeVisible()
  })
})
