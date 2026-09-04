import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { COURSE, open } from './support'

/**
 * The core learning interaction, end to end in a real browser. Opening a quiz
 * from the course page configures it in a dialog and then hands off to
 * `/courses/:id/practice/:quizId`, so the flow deliberately crosses that
 * boundary rather than stopping at the modal.
 */

async function generateQuiz(page: Page) {
  await open(page, `/courses/${COURSE.id}`)

  await page.getByRole('button', { name: 'Practice quiz' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel(/How many questions/).selectOption('5')
  await dialog.getByRole('button', { name: /start the quiz/i }).click()

  await page
    .getByRole('list', { name: 'Background generations' })
    .getByRole('button', { name: /practice quiz/i })
    .click()

  await expect(page).toHaveURL(/\/practice\/4$/)
  await expect(
    page.getByText('Which algorithm finds the fewest-edge path in an unweighted graph?'),
  ).toBeVisible()
}

test.describe('taking a practice quiz', () => {
  test('runs from setup to review, and never marks an unscored answer wrong', async ({ page }) => {
    await generateQuiz(page)

    await page.getByText('Breadth-first search', { exact: true }).click()
    await page.getByRole('button', { name: /next question/i }).click()

    await expect(page.getByText('False', { exact: true })).toBeVisible()
    await page.getByText('False', { exact: true }).click()
    await page.getByRole('button', { name: /next question/i }).click()

    const written = page.getByRole('textbox', { name: /your answer/i })
    await expect(written).toBeVisible()
    await written.fill('A vertex finishes only after all of its descendants have finished.')

    await page.getByRole('button', { name: /hand it in/i }).click()

    await expect(page.getByRole('heading', { name: /50%/ })).toBeVisible()
    await expect(page.getByText(/1 of 2 marked answers correct/)).toBeVisible()

    await expect(page.getByText('Not scored').first()).toBeVisible()
    await expect(page.getByText(/count neither for nor against/)).toBeVisible()
    await expect(page.getByText('A reference answer')).toBeVisible()
  })

  test('is not sat against a clock the browser made up', async ({ page }) => {
    // The quiz came back with no time limit, so there is no deadline to show.
    // A countdown here would be one the browser invented, and the student would
    // be timed on a paper nobody timed.
    await generateQuiz(page)

    await expect(page.getByText('Breadth-first search', { exact: true })).toBeVisible()
    await expect(page.getByRole('timer')).toHaveCount(0)
  })

  test('asks before handing in a quiz that is still unanswered', async ({ page }) => {
    await generateQuiz(page)

    await page.getByRole('button', { name: /next question/i }).click()
    await page.getByRole('button', { name: /next question/i }).click()
    await page.getByRole('button', { name: /hand it in/i }).click()

    await expect(page.getByText('Hand it in unfinished?')).toBeVisible()
  })
})

test.describe('the quiz setup dialog', () => {
  test('keeps the keyboard inside it while it is open', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('button', { name: 'Practice quiz' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()

    for (let press = 0; press < 25; press += 1) {
      await page.keyboard.press('Tab')

      const inside = await page.evaluate(() => {
        const active = document.activeElement
        const dialog = document.querySelector('[role="dialog"]')
        return active !== null && dialog !== null && dialog.contains(active)
      })

      expect(inside, `focus left the dialog after ${press + 1} tabs`).toBe(true)
    }
  })

  test('never scrolls the page sideways on a narrow phone', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 })
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('button', { name: 'Practice quiz' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    )
    expect(overflows).toBe(false)
  })
})
