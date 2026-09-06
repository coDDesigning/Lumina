import { expect, test } from '@playwright/test'

import { COURSE, open } from './support'

/**
 * Course settings has two independent forms and two independent requests. Both
 * announce that they saved, and a screen claiming persistence it never
 * performed is the defect `src/features/honesty.test.ts` exists to catch — so
 * the claim is worth proving against a real request in a real browser.
 */

test.describe('saving course details', () => {
  test('sends what the form holds and says so once it lands', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}/settings`)

    await expect(page.getByLabel('Course name')).toHaveValue(COURSE.title)

    await page.getByLabel('Course name').fill('Algorithms and Data Structures')
    await page.getByLabel(/^Subject area/).fill('Computer Engineering')
    await page.getByLabel(/^Term/).fill('Spring 2027')

    const saved = page.waitForRequest(
      (request) =>
        request.method() === 'PUT' && request.url().endsWith(`/api/courses/${COURSE.id}`),
    )

    await page.getByRole('button', { name: 'Save details' }).click()

    const body = (await saved).postDataJSON()
    expect(body).toMatchObject({
      title: 'Algorithms and Data Structures',
      subject_area: 'Computer Engineering',
      semester: 'Spring 2027',
    })

    await expect(page.getByRole('region', { name: 'Notifications' })).toContainText('Course details saved')
  })

  test('keeps the saved name after the screen is reloaded', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}/settings`)

    await page.getByLabel('Course name').fill('Algorithms and Data Structures')
    await page.getByRole('button', { name: 'Save details' }).click()
    await expect(page.getByRole('region', { name: 'Notifications' })).toContainText('Course details saved')

    await page.reload()

    await expect(page.getByLabel('Course name')).toHaveValue('Algorithms and Data Structures')
  })
})

test.describe('saving course defaults', () => {
  test('sends every default the student changed', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}/settings`)

    await expect(page.getByLabel('Study mode')).toHaveValue('Exam')

    await page.getByLabel('Study mode').selectOption('General')
    await page.getByLabel('Quiz difficulty').selectOption('Hard')
    await page.getByLabel('Guide length').selectOption('Long')
    await page.getByLabel('Guide depth').selectOption('Detailed')

    const saved = page.waitForRequest(
      (request) =>
        request.method() === 'PATCH' &&
        request.url().endsWith(`/api/courses/${COURSE.id}/settings`),
    )

    await page.getByRole('button', { name: 'Save defaults' }).click()

    expect((await saved).postDataJSON()).toMatchObject({
      study_mode: 'General',
      difficulty: 'Hard',
      summary_length: 'Long',
      detail_level: 'Detailed',
    })

    await expect(page.getByRole('region', { name: 'Notifications' })).toContainText('Defaults saved')
  })

  test('keeps the saved defaults after the screen is reloaded', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}/settings`)

    await page.getByLabel('Quiz difficulty').selectOption('Hard')
    await page.getByRole('button', { name: 'Save defaults' }).click()
    await expect(page.getByRole('region', { name: 'Notifications' })).toContainText('Defaults saved')

    await page.reload()

    await expect(page.getByLabel('Quiz difficulty')).toHaveValue('Hard')
  })
})
