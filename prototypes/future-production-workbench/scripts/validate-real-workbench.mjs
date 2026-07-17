import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const baseUrl = "http://127.0.0.1:8765/workbench";
const outputDir = path.resolve(import.meta.dirname, "../qa-artifacts/real-after");
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const consoleErrors = [];
const pageErrors = [];
const assertions = [];
const captures = [];

page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
page.on("pageerror", (error) => pageErrors.push(error.message));

const check = async (name, test) => {
  try {
    const result = await test();
    assertions.push({ name, passed: Boolean(result) });
  } catch (error) {
    assertions.push({ name, passed: false, error: error.message });
  }
};

const capture = async (name, fullPage = true) => {
  await page.waitForTimeout(450);
  const pageTitle = await page.locator("#page-title").innerText();
  const file = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage });
  captures.push({ name, pageTitle, file });
};

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator(".notice").waitFor({ state: "hidden", timeout: 9000 }).catch(() => {});
  await check("future stylesheet loaded", async () => (await page.locator('link[href*="workbench-future.css"]').count()) === 1);
  await check("seven core navigation entries", async () => (await page.locator(".nav-item[data-view]").count()) === 7);
  await check("icon library visible", async () => (await page.locator(".nav-item .ph").count()) === 7);
  await capture("01-inbox");

  for (const [view, name] of [
    ["market", "02-market"],
    ["attention", "03-attention"],
    ["listed", "04-listed"],
    ["finance", "05-finance"],
    ["shops", "06-shops"],
    ["settings", "07-settings"],
  ]) {
    await page.locator(`[data-view="${view}"]`).click();
    if (view === "market") await page.locator(".market-hero, .market-empty-state").first().waitFor({ state: "visible", timeout: 10000 });
    await check(`${view} navigation active`, async () => await page.locator(`[data-view="${view}"]`).evaluate((element) => element.classList.contains("active")));
    await capture(name);
  }

  await page.locator('[data-view="listed"]').click();
  await page.locator("[data-card-product]").first().waitFor({ state: "visible", timeout: 5000 });
  const card = page.locator("[data-card-product]").first();
  if (await card.count()) {
    await card.evaluate((element) => element.click());
    await page.locator(".future-review-shell").waitFor({ state: "visible" });
    await check("review uses three-pane workbench", async () => (await page.locator(".future-flow-panel, .future-image-stage, .future-inspector").count()) === 3);
    await check("review keeps seven editing panels", async () => (await page.locator("[data-future-review-tab]").count()) >= 7);
    for (const tab of ["content", "images", "sku", "price", "category", "store", "risk"]) {
      await page.locator(`[data-future-review-tab="${tab}"]`).first().click();
      await check(`${tab} panel opens`, async () => await page.locator(`[data-future-review-pane="${tab}"].active`).isVisible());
    }
    const prompt = page.locator('[data-image-action="prompt"]');
    if (await prompt.count() && await prompt.isEnabled()) {
      await prompt.click();
      await check("image prompt editor opens without running task", async () => await page.locator(".future-image-stage.editing .prompt-editor").isVisible());
      await prompt.click();
    }
    const imageNext = page.locator('[data-image-cycle="next"]');
    if (await imageNext.count() && await imageNext.isEnabled()) {
      const previous = await page.locator(".future-image-meta strong").innerText();
      await imageNext.click();
      await check("image navigation changes selected slot", async () => (await page.locator(".future-image-meta strong").innerText()) !== previous);
    }
    await page.locator("[data-image-select]").first().click();
    await page.locator('[data-future-review-tab="content"]').first().click();
    await capture("08-product-review", false);

    await page.setViewportSize({ width: 390, height: 844 });
    await check("mobile review keeps inspector in document", async () => await page.locator(".future-inspector").isVisible());
    await page.locator(".future-review-grid").evaluate((element) => { element.scrollTop = element.scrollHeight; });
    await check("mobile inspector can be reached by scrolling", async () => {
      const box = await page.locator(".future-inspector").boundingBox();
      return Boolean(box && box.y < 844 && box.y + box.height > 0);
    });
    await capture("09-product-review-mobile", false);
  }
} finally {
  const report = {
    baseUrl,
    captures,
    assertions,
    consoleErrors,
    pageErrors,
    passed: assertions.every((item) => item.passed) && consoleErrors.length === 0 && pageErrors.length === 0,
  };
  await writeFile(path.join(outputDir, "validation-results.json"), JSON.stringify(report, null, 2));
  await browser.close();
}

console.log(JSON.stringify({
  passed: assertions.every((item) => item.passed) && consoleErrors.length === 0 && pageErrors.length === 0,
  assertions: assertions.filter((item) => !item.passed),
  consoleErrors,
  pageErrors,
  outputDir,
}, null, 2));
