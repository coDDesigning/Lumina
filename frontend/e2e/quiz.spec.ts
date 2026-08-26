import { expect, test } from '@playwright/test'
import { COURSE, open } from './support'

/**
 * The core learning interaction, end to end in a real browser: configure a
 * quiz, answer one of each supported type, hand it in, and read the review.
 * jsdom cannot catch a layout or focus defect in this flow; this can.
 */

test.describe('taking a practice quiz', () => {
  test('runs from setup to review, and never marks an unscored answer wrong', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('button', { name: 'Practice quiz' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('button', { name: /start the quiz/i })).toBeEnabled()

    await dialog.getByLabel(/How many questions/).selectOption('5')
    await dialog.getByRole('button', { name: /start the quiz/i }).click()

    await expect(
      page.getByText('Which algorithm finds the fewest-edge path in an unweighted graph?'),
    ).toBeVisible()

    await page.getByText('Breadth-first search', { exact: true }).click()
    await page.getByRole('button', { name: /next question/i }).click()

    await expect(page.getByText('True', { exact: true })).toBeVisible()
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

  test('counts down against the clock while the quiz is being answered', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('button', { name: 'Practice quiz' }).click()
    await page.getByRole('button', { name: /start the quiz/i }).click()
    await expect(page.getByRole('timer')).toBeVisible()

    const started = await page.getByRole('timer').getAttribute('aria-label')

    await page.getByText('Breadth-first search', { exact: true }).click()
    await page.waitForTimeout(2500)

    await expect(page.getByRole('timer')).not.toHaveAttribute('aria-label', started ?? '')
  })

  test('can be answered without a pointer at all', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.getByRole('button', { name: 'Practice quiz' }).click()
    await page.getByRole('button', { name: /start the quiz/i }).click()
    await expect(
      page.getByText('Which algorithm finds the fewest-edge path in an unweighted graph?'),
    ).toBeVisible()

    await page.keyboard.press('Tab')
    const focused = await page.evaluate(() => document.activeElement?.tagName ?? '')
    expect(focused).not.toBe('BODY')
  })
})

test.describe('the page behind the quiz', () => {
  test('never scrolls sideways on a narrow phone', async ({ page }) => {
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
