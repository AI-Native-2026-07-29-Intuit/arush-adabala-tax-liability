// e2e/global-setup.ts
import { chromium, type FullConfig } from '@playwright/test';

/**
 * Runs once before the whole suite: signs in through the real UI (there's
 * no login API to hit directly - `LoginPage` writes its stub JWT straight
 * to `localStorage` client-side) and persists the resulting storage state
 * so every spec starts already authenticated, without re-driving the
 * sign-in click on every test.
 */
export default async function globalSetup(config: FullConfig): Promise<void> {
  const { baseURL } = config.projects[0]?.use ?? {};
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(`${baseURL}/login`);
  // Not real credentials - LoginPage's stub sign-in never validates these
  // against anything, but the button stays disabled until both are filled.
  await page.getByLabel('Email').fill('engineer@uptimecrew.example.internal');
  await page.getByLabel('Password').fill('synthetic-test-pwd');
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForURL(/\/taxpayers/);

  await page.context().storageState({ path: 'e2e/.auth/user.json' });
  await browser.close();
}
