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

test('revert refreshes the controlled edit fields', async ({ page }) => {
  const edited = {
    employee_id: 'emp-test',
    first_name: 'Test Employee',
    department: 'Validation',
    hire_date: '2026-08-18',
    salary: '333333.00',
    status: 'active',
    source_salary: '93183.00',
    source_status: 'active',
    salary_override: '333333.00',
    status_override: null,
    is_new: false,
    is_deleted: false,
    editor: 'tester@databricks.com',
    updated_at: '2026-08-18T12:00:00Z',
    object_origin: 'app-edited',
  };
  const reverted = {
    ...edited,
    salary: '93183.00',
    salary_override: null,
    updated_at: '2026-08-18T12:01:00Z',
  };
  let isReverted = false;

  await page.route('**/api/employees**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname.endsWith('/revert')) {
      isReverted = true;
      await route.fulfill({ json: reverted });
      return;
    }
    if (url.pathname === '/api/employees/emp-test') {
      await route.fulfill({ json: isReverted ? reverted : edited });
      return;
    }
    await route.fulfill({ json: [isReverted ? reverted : edited] });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Inspect' }).click();
  await expect(page.getByLabel('Salary override')).toHaveValue('333333.00');
  await page.getByRole('button', { name: 'Revert salary' }).click();
  await expect(page.getByLabel('Salary override')).toHaveValue('93183.00');
});
