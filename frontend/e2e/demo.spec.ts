// Drives the 90-second demo story and saves a video to docs/demo/demo.webm.
// Run:  npx playwright test -c playwright.demo.config.ts      (replay, no keys)
//       DEMO_PACE=1.7 npx playwright test -c playwright.demo.config.ts   (~90 s, for voice-over)
// Narration to read over it: docs/DEMO_SCRIPT.md
import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const OUT = path.resolve(__dirname, "../../docs/demo");
const SIZE = { width: 1280, height: 800 };
// DEMO_PACE stretches the reading pauses: 1 = ~60 s video, 1.7 = ~90 s (voice-over pace)
const PACE = Number(process.env.DEMO_PACE ?? 1);

const replies = (page: Page) => page.locator(".messages > .msg.agent, .messages > .docket");

async function step(page: Page, action: () => Promise<void>) {
  const before = await replies(page).count();
  await action();
  await expect(replies(page)).toHaveCount(before + 1);
  await page.waitForTimeout(400); // let the reply finish rendering before the viewer reads it
}

async function type(page: Page, text: string) {
  const box = page.getByRole("textbox", { name: "Message the agent" });
  await box.click();
  await box.pressSequentially(text, { delay: 28 }); // visible typing for the viewer
  await page.waitForTimeout(300);
  await box.press("Enter");
}

test("demo", async ({ browser }) => {
  fs.mkdirSync(OUT, { recursive: true });
  const context = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: path.join(OUT, "raw"), size: SIZE },
  });
  const page = await context.newPage();
  const pause = (ms: number) => page.waitForTimeout(ms * PACE);
  const t0 = Date.now();
  const timeline: string[] = [];
  const scene = (name: string) => {
    const s = Math.round((Date.now() - t0) / 1000);
    timeline.push(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}  ${name}`);
  };

  scene("Opening: empty dispatch desk");
  await page.goto("/");
  await expect(page.getByRole("img", { name: "Agent online" })).toBeVisible();
  await pause(2500);

  scene("1. Tracking: why is order 4518 stuck?");
  // 1. Tracking in plain language: a customs hold
  await step(page, () => page.getByRole("button", { name: "Why is order 4518 stuck?" }).click());
  await pause(1500);
  await page.getByText("Arguments and result").first().click(); // show the raw tool call
  await pause(4500);

  scene("2. Customer email draft");
  // 2. Grounded customer email
  await step(page, () => type(page, "Draft a delay email to the customer about it."));
  await pause(5000);

  scene("3. Rate comparison");
  // 3. Rate comparison with a recommendation
  await step(page, () =>
    type(page, "What are the shipping options for order 4550 to Nairobi? It's 4.8 kg."),
  );
  await pause(5500);

  scene("4. Confirmation card -> Book");
  // 4. Booking needs explicit approval: the confirmation card
  await step(page, () => type(page, "Book it with Standard."));
  const docket = page.getByRole("region", { name: "Confirm shipment" }).last();
  await expect(docket.getByRole("button", { name: "Book shipment" })).toBeVisible();
  await docket.scrollIntoViewIfNeeded();
  await pause(4000);
  await step(page, () => docket.getByRole("button", { name: "Book shipment" }).click());
  await expect(docket.getByRole("status")).toHaveText("Booked");
  await pause(4000);

  scene("5. Documents: weight mismatch");
  // 5. Documents: invoice vs bill of lading weight mismatch
  await step(page, () => page.getByRole("button", { name: /weights disagree/ }).click());
  await pause(5500);

  // A replay must be 100% recorded model output: any unrecorded request would fall back to
  // the rule-based policy and show up in the trace.
  if (process.env.DEMO_LLM !== "record") {
    await expect(page.getByText(/rule-based-baseline/)).toHaveCount(0);
  }

  scene("6. Eval results");
  // 6. The evaluation results
  await page.getByRole("link", { name: "Eval results" }).click();
  await expect(page.getByRole("heading", { name: "Original evaluation" })).toBeVisible();
  await pause(6000);

  scene("End");
  fs.writeFileSync(path.join(OUT, "timeline.txt"), `${timeline.join("\n")}\n`);
  const video = page.video();
  await context.close();
  if (video) {
    await video.saveAs(path.join(OUT, "demo.webm"));
    await video.delete();
  }
  fs.rmSync(path.join(OUT, "raw"), { recursive: true, force: true });
});
