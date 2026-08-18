import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

let artifactDirectory: string;
let consoleErrors: string[];
let pageErrors: string[];

test.beforeEach(async ({ page }) => {
  artifactDirectory = join(process.cwd(), '.smoke-test');
  mkdirSync(artifactDirectory, { recursive: true });
  consoleErrors = [];
  pageErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
});

test.afterEach(async ({ page }, testInfo) => {
  const name = testInfo.title.replaceAll(' ', '-').toLowerCase();
  await page.screenshot({
    path: join(artifactDirectory, `${name}.png`),
    fullPage: true,
  });
  writeFileSync(join(artifactDirectory, `${name}.log`), [...consoleErrors, ...pageErrors].join('\n'));
});

test('object storage console loads', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('heading', { name: 'Object Storage Lab' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Source truth stays still. User intent moves.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create object' })).toBeVisible();
  await expect(page.getByLabel('Search employees')).toBeVisible();
});
