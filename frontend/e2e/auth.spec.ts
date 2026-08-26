import { expect, test } from '@playwright/test'
import { visit } from './support'

test.describe('signing in', () => {
  test('sends an unauthenticated visitor to the sign-in form', async ({ page }) => {
    await visit(page, '/dashboard')

    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByLabel(/email/i)).toBeVisible()
    await expect(page.getByLabel(/password/i)).toBeVisible()
  })

  test('signs in and lands on the dashboard', async ({ page }) => {
    await visit(page, '/login')

    await page.getByLabel(/email/i).fill('bora@example.com')
    await page.getByLabel(/password/i).fill('correct-horse')
    await page.getByRole('button', { name: /sign in/i }).click()

    await expect(page).toHaveURL(/\/dashboard$/)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  })

  test('puts the sign-in form in a main landmark, with one heading', async ({ page }) => {
    await visit(page, '/login')

    await expect(page.getByRole('main')).toHaveCount(1)
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1)
  })

  test('refuses mismatched passwords before asking the server', async ({ page }) => {
    await visit(page, '/register')

    await page.getByLabel(/^name/i).fill('Ada Lovelace')
    await page.getByLabel(/email/i).fill('ada@example.com')
    await page.getByLabel(/^password$/i).fill('correct-horse-battery')
    await page.getByLabel(/confirm/i).fill('something-else')
    await page.getByRole('button', { name: /create|register|sign up/i }).click()

    await expect(page).toHaveURL(/\/register$/)
    await expect(page.getByText(/match/i)).toBeVisible()
  })
})
