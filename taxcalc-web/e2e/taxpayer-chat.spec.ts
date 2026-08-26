// e2e/taxpayer-chat.spec.ts
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * The W4 D5 capstone happy-path: an already-authenticated session (see
 * global-setup.ts) opens the list, drills into a taxpayer, opens that
 * taxpayer's chat, drives a plain turn (streamed tokens) and a tool-calling
 * turn (lookupTaxpayer, orchestrated entirely server-side by streamText's
 * maxSteps loop - see server/api/chat.ts), then reloads and confirms the
 * transcript survived. Every locator below is role/accessible-name based;
 * nothing here needed a data-testid.
 */
test.describe('TaxLiability W4 capstone happy-path', () => {
  test('engineer opens a taxpayer, chats with the assistant, and history survives reload', async ({
    page,
  }) => {
    test.setTimeout(60_000);

    await page.goto('/taxpayers');
    await expect(page.getByRole('list', { name: 'taxpayer-list' })).toBeVisible();

    // Open the first taxpayer row by its accessible name.
    await page.getByRole('link', { name: /stub-1/i }).click();
    await expect(page).toHaveURL(/\/taxpayers\/stub-1$/);
    await expect(page.getByRole('heading')).toContainText('stub-1');

    // One a11y scan on this loaded page state is the budget - not one per
    // state in the flow. wcag2a/wcag2aa mirrors the tag set the two
    // jest-axe scans in TaxpayerListPage.test.tsx / TaxpayerSummaryPage.test.tsx
    // already use, so a real browser and jsdom are held to the same bar.
    const detailPageScan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
    expect(detailPageScan.violations).toEqual([]);

    // Navigate into the chat panel via the in-app link, not a raw goto.
    await page.getByRole('link', { name: 'Chat about stub-1' }).click();
    await expect(page).toHaveURL(/\/taxpayers\/stub-1\/chat$/);

    const transcript = page.getByRole('list', { name: 'chat-transcript' });
    const chatInput = page.getByRole('textbox', { name: 'chat-input' });

    // Plain turn: assert the streamed tokens land in the transcript.
    await chatInput.fill('hello there');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(transcript).toContainText('Hello from the stub tax assistant.', { timeout: 10_000 });

    // Tool-calling turn: the stub backend's own logic (dev/stub-spring-ai.ts)
    // returns a lookupTaxpayer tool call for any user message mentioning
    // "lookup"; the SDK executes it and continues the loop server-side, so
    // one submit is enough to see both the tool call card and its reply.
    await chatInput.fill('please lookup this taxpayer');
    await page.getByRole('button', { name: 'Send' }).click();
    await expect(page.getByRole('complementary', { name: 'tool-call' })).toBeVisible({ timeout: 10_000 });
    await expect(transcript).toContainText('Found stub taxpayer stub-1.', { timeout: 10_000 });

    // The transcript rendering this turn's text and useTaxpayerChatStore's
    // onFinish-triggered localStorage write are two different signals that
    // usually land within microtasks of each other but aren't ordered from
    // this test's perspective - reloading right after the DOM assertion
    // above raced the persist write under CI load and lost, so this waits
    // on the actual signal that matters before reloading: the write itself.
    await page.waitForFunction(
      () => localStorage.getItem('uc:taxpayer-chat')?.includes('Found stub taxpayer stub-1.') ?? false,
    );

    // Reload: initialMessages rehydrates from useTaxpayerChatStore's
    // persisted history, so both turns should still be visible with no
    // further network activity needed.
    await page.reload();
    await expect(transcript).toContainText('Hello from the stub tax assistant.');
    await expect(transcript).toContainText('Found stub taxpayer stub-1.');
  });
});
