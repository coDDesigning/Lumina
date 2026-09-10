import { expect, test } from '@playwright/test'

import { COURSE, open } from './support'

function manySources(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: `${index}`.padStart(8, '0') + '-1111-1111-1111-111111111111',
    original_file_name: `lecture${`${index}`.padStart(2, '0')}.pdf`,
    file_type: 'pdf',
    mime_type: 'application/pdf',
    material_kind: 'slides',
    file_size: 500_000,
    course_id: COURSE.id,
    status: 'ready',
    created_at: '2026-08-10T09:00:00Z',
    updated_at: '2026-08-10T09:04:00Z',
  }))
}

test.describe('workspace layout', () => {
  test('a long source list scrolls inside its panel, not the page', async ({ page }) => {
    await open(page, `/courses/${COURSE.id}`)

    await page.route('**/api/courses/*/documents*', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: manySources(14) }),
      })
    })
    await page.reload()

    const sources = page.getByRole('region', { name: 'Sources' })
    await expect(sources.getByText('lecture13.pdf')).toBeAttached()

    const overflow = await page.evaluate(() => {
      const main = document.querySelector('main')
      if (!main) {
        throw new Error('the shell rendered no main element')
      }
      return { scrollHeight: main.scrollHeight, clientHeight: main.clientHeight }
    })

    expect(overflow.scrollHeight).toBeLessThanOrEqual(overflow.clientHeight + 1)
  })
})
