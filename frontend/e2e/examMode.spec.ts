import { expect, test } from '@playwright/test'
import { open } from './support'

const PLAN = '/courses/1/exam-mode/plans/601'
const OVERVIEW = '/courses/1/exam-mode'

/** Every element that can scroll vertically, so a nested one shows up by name. */
async function scrollers(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const found: string[] = []
    const walk = (node: Element) => {
      const style = getComputedStyle(node)
      const scrolls = /auto|scroll/.test(style.overflowY)
      if (scrolls && node.scrollHeight > node.clientHeight + 1) {
        found.push(node.tagName.toLowerCase() + (node.id ? `#${node.id}` : ''))
      }
      for (const child of Array.from(node.children)) walk(child)
    }
    walk(document.documentElement)
    if (document.documentElement.scrollHeight > window.innerHeight + 1) {
      found.push('document')
    }
    return found
  })
}

test.describe('Exam Mode layout', () => {
  test('scrolls in exactly one place', async ({ page }) => {
    // Two scrollbars means the reader has to work out which one moves the
    // content. The shell owns the scroll; a page inside it must not add another.
    await open(page, PLAN)
    await expect(page.getByRole('heading', { name: 'What to study, in order' })).toBeVisible()

    expect(await scrollers(page)).toEqual(['main#main'])
  })

  test('never scrolls sideways at 360px', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 720 })
    await open(page, PLAN)
    await expect(page.getByRole('heading', { name: 'What to study, in order' })).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('the overview never scrolls sideways at 360px', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 720 })
    await open(page, OVERVIEW)
    await expect(page.getByRole('heading', { name: 'Choose what to read' })).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})

test.describe('Exam Mode content', () => {
  test('says what a plan warning means rather than printing its code', async ({ page }) => {
    await open(page, PLAN)
    await expect(page.getByRole('heading', { name: 'What to study, in order' })).toBeVisible()

    // Read the rendered text rather than a locator, so a raw code cannot hide
    // behind an element boundary.
    const text = await page.locator('#main').innerText()

    expect(text).not.toContain('no_syllabus_evidence')
    expect(text).not.toContain('unmapped_mastery_labels')
    expect(text).toContain('No syllabus evidence was available')
  })

  test('reaches a saved plan from the overview', async ({ page }) => {
    await open(page, OVERVIEW)

    await page.getByRole('link', { name: /Version 3/ }).click()

    await expect(page).toHaveURL(/\/exam-mode\/plans\/601$/)
  })

  test('opens Exam Mode from the conversation modes', async ({ page }) => {
    await open(page, '/courses/1')

    await page.getByRole('link', { name: /Exam Mode/ }).click()

    await expect(page).toHaveURL(/\/courses\/1\/exam-mode$/)
  })
})
