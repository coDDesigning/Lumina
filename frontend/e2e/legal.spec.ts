import { expect, test } from '@playwright/test'
import { visit } from './support'

const POLICIES = [
  ['/legal/privacy', 'Privacy Notice'],
  ['/legal/terms', 'Terms of Service'],
  ['/legal/acceptable-use', 'Acceptable Use Policy'],
  ['/legal/cookies', 'Cookie & Browser Storage Policy'],
  ['/legal/ai-disclosure', 'AI / Educational Disclosure'],
  ['/legal/security', 'Security & Responsible Disclosure'],
  ['/legal/open-source', 'Open Source & Third-Party Notices'],
] as const

for (const [path, heading] of POLICIES) {
  test(`${path} opens directly without authentication`, async ({ page }) => {
    await visit(page, path)

    await expect(page).toHaveURL(new RegExp(`${path}$`))
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await expect(page.getByText(/effective 12 september 2026/i)).toBeVisible()

    await page.reload()
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
  })
}

test('registration exposes required Terms and Privacy acknowledgement separately', async ({ page }) => {
  await visit(page, '/register')

  const acknowledgement = page.getByRole('checkbox', { name: /by creating an account/i })
  await expect(acknowledgement).toBeVisible()
  await expect(acknowledgement).not.toBeChecked()
  await expect(page.getByRole('link', { name: 'Terms of Service' })).toHaveAttribute('href', '/legal/terms')
  await expect(page.getByRole('link', { name: 'Privacy Notice' })).toHaveAttribute('href', '/legal/privacy')
  await expect(page.getByText(/not consent to optional advertising or analytics/i)).toBeVisible()
})
