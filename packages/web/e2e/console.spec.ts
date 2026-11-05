import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const live = process.env.E2E_API === "live";
const answerTimeout = live ? 100_000 : 15_000;

async function ask(page: Page, question: string) {
  await page.goto("/");
  await page.getByLabel("What did the customer say?").fill(question);
  await page.getByRole("button", { name: "Diagnose", exact: true }).click();
  return page.getByRole("article", { name: "Diagnosis" });
}

test("a CS member asks about a seeded case and sees the correct root cause", async ({ page }) => {
  // GR-11854 is an F07 case in the default seed: the clinic's PMS token expired.
  const answer = await ask(page, "The specialist says GR-11854 never arrived in their system.");

  await expect(answer.getByText("Clinic PMS authorisation failed")).toBeVisible({
    timeout: answerTimeout,
  });
  await expect(answer.getByText("F07_CLINIC_PMS_AUTH_FAILED")).toBeVisible();
  await expect(page.getByRole("region", { name: "Progress" })).toContainText(
    "Looking up the referral in both systems",
  );
  await answer.getByText(/Evidence \(\d+ records\)/).click();
  if (!live) {
    // The oracle cites the case's expected evidence, which the tools really showed.
    await expect(answer.getByText("dl_538183")).toBeVisible();
  }
});

test("feedback is accepted and the diagnosis appears in the history", async ({ page }) => {
  // GR-10080 is an F06 case: the clinic has no active account.
  const question = `Kowhai says they never got GR-10080 (${String(Date.now())}).`;
  const answer = await ask(page, question);
  await expect(answer.getByText("Routed for manual delivery")).toBeVisible({
    timeout: answerTimeout,
  });

  await answer.getByRole("button", { name: /not right/i }).click();
  await answer.getByLabel("What was the right root cause?").selectOption("F06_MANUAL_ROUTING");
  await answer.getByRole("button", { name: "Send feedback" }).click();
  await expect(answer.getByText(/thanks for the feedback/i)).toBeVisible();

  // The history lists it, also after a reload, and opens it.
  await page.reload();
  await page.getByRole("region", { name: "History" }).getByText(question).click();
  await expect(page.getByRole("article", { name: "Diagnosis" })).toContainText(
    "Routed for manual delivery",
  );
});

test("the ask and answer screens pass basic accessibility checks", async ({ page }) => {
  await page.goto("/");
  const before = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(before.violations).toEqual([]);

  const answer = await ask(page, "The specialist says GR-11854 never arrived in their system.");
  await expect(answer).toBeVisible({ timeout: answerTimeout });
  await answer.getByText(/Evidence \(\d+ records\)/).click();
  const after = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(after.violations).toEqual([]);
});

test("a long question in the history does not widen the page", async ({ page }) => {
  // History entries are truncated on one line; on a phone, one long question once stretched
  // the single-column grid to its full width, so the whole page scrolled sideways.
  const question =
    "Hi team, About referral GR-11213: The specialist never replied. Could you look into it? " +
    `They have asked twice now and would like an answer today (${String(Date.now())}).`;
  const answer = await ask(page, question);
  await expect(answer).toBeVisible({ timeout: answerTimeout });
  await expect(page.getByRole("region", { name: "History" })).toContainText("GR-11213");

  const widths = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    screen: document.documentElement.clientWidth,
  }));
  expect(widths.page).toBeLessThanOrEqual(widths.screen);
});
