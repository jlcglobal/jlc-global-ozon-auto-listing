import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const outputDir = path.join(root, "qa-artifacts");
const baseUrl = "http://127.0.0.1:4175/";
const executablePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ executablePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1, reducedMotion: "no-preference" });
const consoleErrors = [];
const pageErrors = [];
const checks = [];
page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
page.on("pageerror", (error) => pageErrors.push(error.message));
const check = (name, passed, detail = "") => {
  checks.push({ name, passed: Boolean(passed), detail });
  if (!passed) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
};

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  check("core heading visible", await page.getByRole("heading", { name: "商品制作工作台" }).isVisible());
  check("prototype safety boundary visible", await page.getByText(/不连接 Factory \/ Ozon/).first().isVisible());
  check("real product id visible", await page.getByText("P000001", { exact: true }).isVisible());
  check("real uploaded state visible", await page.getByText("全部步骤完成", { exact: true }).isVisible());
  check("desktop has no page overflow", await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
  await page.screenshot({ path: path.join(outputDir, "desktop-default.png"), fullPage: true });

  await page.getByRole("tab", { name: "SKU" }).click();
  check("SKU inspector works", await page.getByText("5931524204563", { exact: true }).isVisible());
  await page.getByRole("tab", { name: "店铺" }).click();
  check("shop inspector works", await page.getByText("5081018919", { exact: true }).isVisible());
  await page.getByRole("tab", { name: "资料" }).click();

  const initialImage = await page.locator(".hero-product").getAttribute("src");
  await page.getByRole("button", { name: "下一张图片" }).click();
  check("next image changes the asset", (await page.locator(".hero-product").getAttribute("src")) !== initialImage);
  await page.getByRole("button", { name: "查看400ml 主图" }).click();
  await page.getByRole("button", { name: "查看大图" }).click();
  check("image lightbox works", await page.getByRole("dialog", { name: "400ml 主图大图" }).isVisible());
  await page.keyboard.press("Escape");
  check("escape closes image lightbox", !(await page.getByRole("dialog", { name: "400ml 主图大图" }).isVisible().catch(() => false)));

  await page.keyboard.press("Meta+K");
  check("global search opens", await page.getByRole("dialog", { name: "全局搜索" }).isVisible());
  await page.getByPlaceholder("搜索商品、SKU、任务、店铺或现有功能").fill("多店");
  check("capability search returns existing ability", await page.getByText("一份主档多店发布", { exact: true }).isVisible());
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "手动检查" }).click();
  check("mode toggle changes", await page.getByRole("button", { name: "自动审核" }).isVisible());
  await page.getByRole("button", { name: "功能总览" }).click();
  check("capability drawer opens", await page.getByRole("dialog", { name: "Factory 现有功能总览" }).isVisible());
  check("drawer states no new functionality", await page.getByText(/没有新增业务功能/).isVisible());
  await page.getByRole("button", { name: "返回工作台" }).click();

  await page.getByRole("button", { name: "查看上架结果" }).click();
  check("publication result opens", await page.getByRole("dialog", { name: "Ozon 上架结果" }).isVisible());
  check("publication modal states no remote request", await page.getByText(/没有发起任何远端请求/).isVisible());
  await page.screenshot({ path: path.join(outputDir, "desktop-publication-result.png"), fullPage: true });
  await page.getByRole("button", { name: "完成", exact: true }).click();

  await page.getByRole("button", { name: "商品更多操作" }).click();
  check("product actions opens", await page.getByRole("dialog", { name: "商品更多操作" }).isVisible());
  check("permanent delete remains guarded", await page.getByText(/预览中禁用/).isVisible());
  await page.keyboard.press("Escape");

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(350);
  check("1024 has no page overflow", await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
  check("1024 keeps primary action", await page.getByRole("button", { name: "查看上架结果" }).isVisible());
  await page.screenshot({ path: path.join(outputDir, "desktop-1024.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(350);
  check("mobile has no page overflow", await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
  check("mobile keeps product canvas", await page.locator(".visual-canvas").isVisible());
  check("mobile keeps primary action", await page.getByRole("button", { name: "查看上架结果" }).isVisible());
  await page.screenshot({ path: path.join(outputDir, "mobile-390.png"), fullPage: true });
} finally {
  await writeFile(path.join(outputDir, "validation-results.json"), JSON.stringify({
    url: baseUrl,
    checks,
    consoleErrors,
    pageErrors,
    passed: checks.every((item) => item.passed) && consoleErrors.length === 0 && pageErrors.length === 0,
    capturedAt: new Date().toISOString(),
  }, null, 2));
  await browser.close();
}

if (consoleErrors.length || pageErrors.length) throw new Error(`Browser errors: ${JSON.stringify({ consoleErrors, pageErrors })}`);
console.log(`Validated ${checks.length} checks. Artifacts: ${outputDir}`);
