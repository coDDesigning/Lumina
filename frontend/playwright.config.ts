import { defineConfig, devices } from '@playwright/test'

const HOST = 'http://localhost:4173'

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: HOST,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'python ../.user/scripts/stub_api.py',
      url: 'http://localhost:8000/api/auth/me',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: 'npm run preview -- --port 4173 --strictPort',
      url: HOST,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
})
