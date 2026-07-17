import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const outputDir = path.resolve(import.meta.dirname, "../qa-artifacts/real-before");
await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1 });
const consoleErrors = [];
const pageErrors = [];
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
page.on("pageerror", (error) => pageErrors.push(error.message));

const captures = [];
const capture = async (name) => {
  await page.waitForTimeout(500);
  const pageTitle = await page.locator("#page-title").innerText();
  const file = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  captures.push({ name, pageTitle, file });
};

try {
  await page.goto("http://127.0.0.1:8765/workbench", { waitUntil: "networkidle" });
  await capture("01-inbox");
  for (const [view, name] of [
    ["market", "02-market"],
    ["attention", "03-attention"],
    ["listed", "04-listed"],
    ["finance", "05-finance"],
    ["shops", "06-shops"],
    ["settings", "07-settings"],
  ]) {
    const nav = page.locator(`[data-view="${view}"]`);
    if (await nav.count()) {
      await nav.click();
      await capture(name);
    }
  }
  await page.locator('[data-view="listed"]').click();
  const openResult = page.getByRole("button", { name: "查看上架结果" });
  if (await openResult.count()) {
    await openResult.first().click();
    await capture("08-product-review");
  }
} finally {
  await writeFile(path.join(outputDir, "capture-results.json"), JSON.stringify({ captures, consoleErrors, pageErrors }, null, 2));
  await browser.close();
}

console.log(`Captured ${captures.length} real workbench states in ${outputDir}`);
