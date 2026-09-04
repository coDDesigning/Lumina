import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { open } from './support'

/**
 * jsdom has no layout engine, so no component test can catch a contrast,
 * landmark or focus-order defect. These run axe against the real thing.
 *
 * Only serious and critical violations fail. The lighter rules are advisory and
 * would make the suite noisy without making the product better.
 */

const BLOCKING = ['serious', 'critical']

/**
 * One known contrast gap, allowed by colour rather than by route so it cannot
 * quietly cover a second one. `--text-subtle` is #7e8899, which clears 3.58:1 on
 * white. `src/styles/contrast.test.ts` deliberately holds it to 3:1 as
 * supporting text, but several screens use it at 12-13px, where WCAG AA asks
 * 4.5:1. Darkening the token is a palette decision rather than a test fix, so it
 * is recorded here and tracked separately; every other failing pair still fails.
 */
const KNOWN_CONTRAST_GAP = {
  foreground: '#7e8899',
  reason:
    '--text-subtle at 12-13px clears 3.58:1, not 4.5:1. Held to 3:1 on purpose by contrast.test.ts; darkening the token needs a palette decision.',
}

const ROUTES = [
  { name: 'the dashboard', path: '/dashboard' },
  { name: 'a course', path: '/courses/1' },
  { name: 'course progress', path: '/courses/1/progress' },
  { name: 'course settings', path: '/courses/1/settings' },
  { name: 'the account', path: '/account' },
  { name: 'account background', path: '/account/background' },
  { name: 'account AI preferences', path: '/account/ai' },
  { name: 'account security', path: '/account/security' },
  { name: 'the admin screen', path: '/admin' },
  { name: 'Exam Mode', path: '/courses/1/exam-mode' },
  { name: 'an exam plan', path: '/courses/1/exam-mode/plans/601' },
  { name: 'an exam topic', path: '/courses/1/exam-mode/plans/601/topics/graph-traversal-algorithms' },
  { name: 'a timed sitting', path: '/courses/1/practice/9/sessions/55' },
]

function isKnownGap(node: { any: { message?: string }[] }): boolean {
  return node.any.some((check) =>
    (check.message ?? '').toLowerCase().includes(KNOWN_CONTRAST_GAP.foreground),
  )
}

async function assertNoBlockingViolations(page: Page, contextName: string) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()

  const blocking = violations
    .filter((violation) => BLOCKING.includes(violation.impact ?? ''))
    .flatMap((violation) =>
      violation.nodes
        .filter((node) => !(violation.id === 'color-contrast' && isKnownGap(node)))
        .map((node) => `${violation.id}: ${node.target.join(' ')}`),
    )

  expect(
    blocking,
    `${contextName} must not ship a serious or critical violation. The only allowance is ${KNOWN_CONTRAST_GAP.reason}`,
  ).toEqual([])
}

for (const route of ROUTES) {
  test(`${route.name} has no serious accessibility violation`, async ({ page }) => {
    await open(page, route.path)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await assertNoBlockingViolations(page, route.path)
  })
}

const MODAL_FLOWS = [
  {
    name: 'the create course dialog',
    path: '/dashboard',
    openModal: async (page: Page) => {
      await page.getByRole('button', { name: /new course/i }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    },
  },
  {
    name: 'the practice quiz modal',
    path: '/courses/1',
    openModal: async (page: Page) => {
      await page.getByRole('button', { name: 'Practice quiz' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    },
  },
  {
    name: 'the study guide modal',
    path: '/courses/1',
    openModal: async (page: Page) => {
      await page.getByRole('button', { name: 'Study guide' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    },
  },
  {
    name: 'the flashcard modal',
    path: '/courses/1',
    openModal: async (page: Page) => {
      await page.getByRole('button', { name: 'Flashcards' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    },
  },
  {
    name: 'the exam roadmap modal',
    path: '/courses/1',
    openModal: async (page: Page) => {
      await page.getByRole('button', { name: 'Exam roadmap' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    },
  },
  {
    name: 'the delete course confirmation dialog',
    path: '/courses/1/settings',
    openModal: async (page: Page) => {
      await page.getByRole('button', { name: /^delete /i }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    },
  },
]

for (const modal of MODAL_FLOWS) {
  test(`${modal.name} has no serious accessibility violation when opened`, async ({ page }) => {
    await open(page, modal.path)
    await modal.openModal(page)
    await assertNoBlockingViolations(page, modal.name)
  })
}

test('the known contrast gap is still only the one colour', async ({ page }) => {
  test.slow()
  const pairs = new Set<string>()

  for (const route of ROUTES) {
    await open(page, route.path)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    const { violations } = await new AxeBuilder({ page }).withTags(['wcag2aa']).analyze()

    for (const violation of violations.filter((entry) => entry.id === 'color-contrast')) {
      for (const node of violation.nodes) {
        const message = node.any[0]?.message ?? ''
        const foreground = message.match(/foreground color: (#[0-9a-f]{6})/i)
        if (foreground) {
          pairs.add(foreground[1].toLowerCase())
        }
      }
    }
  }

  expect(
    Array.from(pairs).sort(),
    'A new contrast failure appeared. Only --text-subtle is a known gap.',
  ).toEqual([KNOWN_CONTRAST_GAP.foreground])
})

test('every screen names itself with exactly one top-level heading', async ({ page }) => {
  for (const route of ROUTES) {
    await open(page, route.path)
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1)
    await expect(page.getByRole('main')).toHaveCount(1)
  }
})

test('no screen scrolls sideways at 360px', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })

  for (const route of ROUTES) {
    await open(page, route.path)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    )
    expect(overflows, `${route.path} scrolls sideways at 360px`).toBe(false)
  }
})
