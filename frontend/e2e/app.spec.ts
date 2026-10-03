import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const DOCS = path.resolve(__dirname, "../../data/documents/pdf");

async function ask(page: Page, text: string) {
  const box = page.getByRole("textbox", { name: "Message the agent" });
  await box.fill(text);
  await box.press("Enter");
}

const lastAgentMessage = (page: Page) => page.locator(".msg.agent").last();

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What needs shipping today?" })).toBeVisible();
  // API reachable (the status dot is visible at every width; its text only on desktop)
  await expect(page.getByRole("img", { name: "Agent online" })).toBeVisible();
});

test("tracking question shows the answer and the tool trace", async ({ page }) => {
  await page.getByRole("button", { name: "Why is order 4518 stuck?" }).click();
  await expect(lastAgentMessage(page)).toContainText("customs hold");
  await expect(lastAgentMessage(page)).toContainText("HS code");

  const trace = page.getByRole("region", { name: "Agent trace" });
  await expect(trace.getByText("Track shipment", { exact: true })).toBeVisible();
  await expect(trace.locator(".badge.ok").first()).toBeVisible();
  await trace.getByText("Arguments and result").first().click();
  await expect(trace.locator("pre").first()).toContainText('"order_id": "4518"');
});

test("carrier outage: the agent says so instead of guessing", async ({ page }) => {
  await ask(page, "Where is order 4540?");
  await expect(lastAgentMessage(page)).toContainText("won't guess", { timeout: 20_000 });
  await expect(page.getByRole("region", { name: "Agent trace" }).locator(".badge.fail")).toBeVisible();
});

test("confirmation card: approving books the shipment", async ({ page }) => {
  await ask(page, "Ship order 4550 with Standard");
  const docket = page.getByRole("region", { name: "Confirm shipment" });
  await expect(docket.getByRole("heading", { name: "Book this shipment?" })).toBeVisible();
  await expect(docket).toContainText("Njeri Mwangi");
  await expect(docket).toContainText("$147.39");
  await expect(page.locator(".badge.wait")).toHaveText("waiting for you");

  await docket.getByRole("button", { name: "Book shipment" }).click();
  await expect(docket.getByRole("heading", { name: "Shipment booked" })).toBeVisible();
  await expect(docket.getByRole("status")).toHaveText("Booked");
  await expect(docket).toContainText(/MC2\d{8}/);
  await expect(lastAgentMessage(page)).toContainText("Shipment created");
  await expect(docket.getByRole("button", { name: "Book shipment" })).toBeHidden();
});

test("confirmation card: cancelling books nothing", async ({ page }) => {
  await ask(page, "Ship order 4553 with economy");
  const docket = page.getByRole("region", { name: "Confirm shipment" });
  await expect(docket).toContainText("Yaw Owusu");

  await docket.getByRole("button", { name: "Cancel" }).click();
  await expect(docket.getByRole("heading", { name: "Booking cancelled" })).toBeVisible();
  await expect(docket.getByRole("status")).toHaveText("Cancelled");
  await expect(docket).not.toContainText(/MC2\d{8}/);
  await expect(lastAgentMessage(page)).toContainText("not created");
});

test("document upload flags a missing HS code", async ({ page }) => {
  const docs = page.getByRole("region", { name: "Shipping documents" });
  await docs.locator('input[type="file"]').setInputFiles(path.join(DOCS, "p02_invoice.pdf"));

  const card = docs.locator(".doc").filter({ hasText: "p02_invoice.pdf" });
  await expect(card).toContainText("Commercial invoice");
  await expect(card).toContainText("has no HS code");

  await card.getByRole("button", { name: "Ask the agent about this document" }).click();
  await expect(lastAgentMessage(page)).toContainText("no HS code");
});

test("sample packet: the agent catches the invoice/BoL weight mismatch", async ({ page }) => {
  await page.getByRole("button", { name: /weights disagree/ }).click();
  await expect(lastAgentMessage(page)).toContainText("Gross weight differs");
  await expect(lastAgentMessage(page)).toContainText("60.68");
});

test.describe("mobile width", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("no horizontal overflow; trace drawer opens and closes on send", async ({ page }) => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);

    const toggle = page.getByRole("button", { name: "Trace & documents" });
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(page.getByRole("heading", { name: /What the agent did/ })).toBeVisible();

    await page.getByRole("button", { name: /weights disagree/ }).click();
    await expect(page.getByRole("button", { name: "Trace & documents" })).toBeVisible();
    await expect(lastAgentMessage(page)).toContainText("Gross weight differs");

    await ask(page, "Ship order 4550 with Standard");
    const docket = page.getByRole("region", { name: "Confirm shipment" });
    await expect(docket.getByRole("button", { name: "Book shipment" })).toBeVisible();
    const docketOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(docketOverflow).toBeLessThanOrEqual(0);
  });
});

test("eval results page shows the summary table", async ({ page }) => {
  await page.getByRole("link", { name: "Eval results" }).click();
  await expect(page.getByRole("heading", { name: "Original evaluation" })).toBeVisible();
  const table = page.getByRole("region", { name: "Original evaluation" });
  await expect(table.getByRole("rowheader", { name: /gemini-3\.1-flash-lite/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Hard set/ })).toBeVisible();
  await page.getByRole("link", { name: "Back to the agent" }).click();
  await expect(page.getByRole("heading", { name: "What needs shipping today?" })).toBeVisible();
});
