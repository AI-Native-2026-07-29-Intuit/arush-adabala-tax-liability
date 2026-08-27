// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';

/**
 * One chromium project, driving the real happy path against three
 * concurrently-booted local servers - the Vite dev server plus the two
 * dev-only stand-ins documented in dev/stub-spring-ai.ts and
 * server/index.ts's own header comments (neither is a production build;
 * both exist purely so this suite can drive a real browser without the
 * full Spring stack). `reuseExistingServer` outside CI lets a developer
 * already running `pnpm dev` in another terminal skip the respawn.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    storageState: 'e2e/.auth/user.json',
  },
  globalSetup: './e2e/global-setup.ts',
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'pnpm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'pnpm run server',
      url: 'http://localhost:3001/health',
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'pnpm run stub-backend',
      url: 'http://localhost:8080/health',
      reuseExistingServer: !process.env.CI,
    },
  ],
});
