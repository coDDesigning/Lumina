import type { Page } from '@playwright/test'
import { stubApi } from './api'

export { COURSE } from './api'

/**
 * The suite answers its own API calls, so a seeded token is enough to render a
 * protected route instead of bouncing to /login. It has to be in place before
 * the first navigation, because the app reads it on mount.
 */
export async function signIn(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('token', 'stub')
  })
}

export async function open(page: Page, route: string) {
  await stubApi(page)
  await signIn(page)
  await page.goto(route)
}

/** For the routes that must be reached signed out. */
export async function visit(page: Page, route: string) {
  await stubApi(page)
  await page.goto(route)
}
