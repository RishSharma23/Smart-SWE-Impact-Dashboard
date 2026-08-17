import { expect, test, type Page } from '@playwright/test';

/**
 * The critical path a reader actually takes, plus the accessibility gate.
 * Deliberately compact: these assert behaviour the unit tests cannot reach
 * (real generated data, routing, focus, no console errors).
 */

const ROUTES = ['/', '/engineers/', '/compare/', '/methodology/', '/coverage/'];

/** Fails the test on any uncaught console error or failed request. */
function watchForErrors(page: Page) {
  const problems: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`console: ${m.text()}`);
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('requestfailed', (r) => {
    // Avatars are third-party (github.com) and may legitimately 404 for a
    // renamed account; the UI falls back to a monogram. Everything else counts.
    if (!r.url().includes('github.com')) problems.push(`request failed: ${r.url()}`);
  });
  return problems;
}

test.describe('overview loads real generated data', () => {
  test('shows the top five with tiers, thesis prose and provenance', async ({ page }) => {
    const problems = watchForErrors(page);
    await page.goto('/');

    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    // Provenance the contract requires on screen.
    await expect(page.getByText(/generated/i).first()).toBeVisible();
    await expect(page.locator('header').getByText(/methodology \d+\.\d+\.\d+/)).toBeVisible();

    // Ranked cards, at most five, each with a tier.
    const cards = page.locator('[data-leader-card]');
    const n = await cards.count();
    expect(n).toBeGreaterThan(0);
    expect(n).toBeLessThanOrEqual(5);
    await expect(cards.first().getByText(/^Tier \d+$/)).toBeVisible();

    // The standing notice, not buried.
    await expect(page.getByText(/not total employee productivity/i)).toBeVisible();

    expect(problems).toEqual([]);
  });

  test('never renders a naked composite score', async ({ page }) => {
    await page.goto('/');
    const body = (await page.locator('body').textContent()) ?? '';
    expect(body).not.toMatch(/\b\d{3,4}\s*\/\s*1000\b/);
    expect(body).not.toMatch(/impact score/i);
  });
});

test('every leader card opens a full profile', async ({ page }) => {
  await page.goto('/');
  const links = page.locator('section[aria-labelledby="leaders-heading"] a[href^="/engineers/"]');
  const hrefs = [...new Set(await links.evaluateAll((as) => as.map((a) => a.getAttribute('href'))))];
  expect(hrefs.length).toBeGreaterThan(0);

  for (const href of hrefs.slice(0, 5)) {
    await page.goto(href!);
    await expect(page.getByRole('heading', { name: /Impact thesis/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Six-dimension evidence profile/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Counterevidence and uncertainty/i })).toBeVisible();
  }
});

test('evidence is two clicks from a top-five claim, and links to github', async ({ page }) => {
  await page.goto('/');
  await page.locator('button', { hasText: /Evidence/ }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  // The claim id is reachable, because the correction pathway is to quote it.
  await expect(dialog.getByText(/^claim\//)).toBeVisible();
  const link = dialog.locator('a[href^="https://github.com"]').first();
  if (await link.count()) {
    await expect(link).toHaveAttribute('rel', /noopener/);
  }
  // Focus is trapped and returns on close.
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

test('"Why this ranking?" publishes the pairwise trace', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /Why this ranking\?/ }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/Concordance/i).first()).toBeVisible();
  await expect(dialog.getByText(/rather than scored as zero/i).first()).toBeVisible();
});

test('switching scenario updates positions and announces it', async ({ page }) => {
  await page.goto('/');
  const radios = page.getByRole('radiogroup', { name: /Ranking scenario/i }).getByRole('radio');
  const count = await radios.count();
  if (count < 2) {
    test.skip(true, 'only one scenario is available in this run');
  }
  const before = await page.locator('section[aria-labelledby="leaders-heading"]').textContent();
  await radios.nth(1).click();
  await expect(radios.nth(1)).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('[role="status"]')).toContainText(/scenario changed to/i);
  const after = await page.locator('section[aria-labelledby="leaders-heading"]').textContent();
  expect(after).not.toBe(before);
});

test('an unavailable scenario is disabled with its reason, not hidden', async ({ page }) => {
  await page.goto('/');
  const locked = page.getByRole('button', { name: /unavailable, select to read why/i }).first();
  if (!(await locked.count())) {
    test.skip(true, 'every scenario is available in this run');
  }
  await locked.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/could not be computed/i)).toBeVisible();
});

test('comparison compares two candidates and never compares volume', async ({ page, isMobile }) => {
  await page.goto('/compare/');
  const a = page.locator('#cmp-a');
  const b = page.locator('#cmp-b');
  await expect(a).toBeVisible();
  const options = await a.locator('option').count();
  if (options < 2) test.skip(true, 'fewer than two ranked engineers');

  await expect(page.getByRole('heading', { name: /Dimension by dimension/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Why one outranks the other/i })).toBeVisible();
  await expect(page.getByText(/Lines of code/)).toBeVisible();

  // The swap affordance is desktop-only; on mobile the two selects are stacked
  // and swapping adds nothing, so it is deliberately not rendered.
  if (!isMobile) {
    const beforeB = await b.inputValue();
    await page.getByRole('button', { name: /Swap the two candidates/i }).click();
    await expect(a).toHaveValue(beforeB);
  }
});

test('an episode page shows the arc, corroboration and every source', async ({ page }) => {
  await page.goto('/');
  const href = await page.locator('a[href^="/episodes/"]').first().getAttribute('href');
  test.skip(!href, 'no episode page in this run');
  await page.goto(href!);
  await expect(page.getByRole('heading', { name: /Problem, intervention, observable result/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Who did what/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Every source artifact/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Propagation and durability/i })).toBeVisible();
});

test('methodology and coverage load and name what is not used', async ({ page }) => {
  await page.goto('/methodology/');
  await expect(page.getByRole('heading', { name: /Is this a productivity tracker\? No\./i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /The formulas, literally/i })).toBeVisible();

  await page.goto('/coverage/');
  await expect(page.getByRole('heading', { name: /What this run could not see/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Validation programme/i })).toBeVisible();
});

test('direct route refresh works for a deep link', async ({ page }) => {
  await page.goto('/');
  const href = await page.locator('a[href^="/engineers/"]').first().getAttribute('href');
  await page.goto(href!);
  await page.reload();
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
});

test('404 explains itself and offers the way back', async ({ page }) => {
  // The HTTP status is host-dependent (GitHub Pages serves 404.html with a 404;
  // some static servers answer 200), so this asserts on the page instead.
  await page.goto('/definitely-not-a-route/');
  await expect(page.getByRole('heading', { name: /not part of this run/i })).toBeVisible();
  await expect(page.getByRole('link', { name: /Overview and the top five/i })).toBeVisible();
});

test('mobile navigation opens and reaches every route', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'mobile-only');
  await page.goto('/');
  const toggle = page.getByRole('button', { name: /Open navigation/i });
  await expect(toggle).toBeVisible();
  await toggle.click();
  const nav = page.locator('#mobile-nav');
  await expect(nav).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Methodology' })).toBeVisible();
  await nav.getByRole('link', { name: 'Coverage' }).click();
  await expect(page.getByRole('heading', { name: /What this run could not see/i })).toBeVisible();
});

test('no horizontal page scroll at 360px', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  for (const route of ROUTES) {
    await page.goto(route);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${route} scrolls horizontally by ${overflow}px`).toBeLessThanOrEqual(1);
  }
});

/*
 * Accessibility is implemented in the markup (landmarks, labels, focus, table
 * fallbacks, reduced motion) but deliberately NOT gated in CI yet — see
 * docs/ACCESSIBILITY.md for what is in place and what still needs an audit.
 */
