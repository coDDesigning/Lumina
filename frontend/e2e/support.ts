import type { Page } from '@playwright/test'

/**
 * The stub API accepts any bearer token, so seeding one is enough to render a
 * protected route instead of bouncing to /login. The app reads it from
 * localStorage on mount, so it has to be in place before the first navigation.
 */
export async function signIn(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('token', 'stub')
  })
}

export async function open(page: Page, route: string) {
  await signIn(page)
  await page.goto(route)
}

export const COURSE = {
  id: 1,
  title: 'Fundamental Structures of Computer Science',
}
