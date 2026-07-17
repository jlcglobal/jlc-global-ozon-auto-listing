import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const outputDir = path.join(root, "qa-artifacts");
const baseUrl = "http://127.0.0.1:4174/";
const executablePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ executablePath, headless: true });
const page = await browser.newPage({
  viewport: { width: 1487, height: 1058 },
  deviceScaleFactor: 1,
  reducedMotion: "no-preference",
});

const consoleErrors = [];
const pageErrors = [];
const checks = [];

page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => pageErrors.push(error.message));

const check = (name, passed, detail = "") => {
  checks.push({ name, passed: Boolean(passed), detail });
  if (!passed) throw new Error(`${name}${detail ? `: ${detail}` : ""}`);
};

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForTimeout(1400);

  check("desktop title visible", await page.getByRole("heading", { name: "三栏AI工作室" }).isVisible());
  check("real product visible", await page.getByText("P000001", { exact: false }).first().isVisible());
  check("real progress visible", await page.getByText("94%", { exact: true }).first().isVisible());
  check("desktop has no horizontal overflow", await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));

  await page.screenshot({ path: path.join(outputDir, "desktop-default.png"), fullPage: true });

  await page.getByRole("button", { name: "搜索和筛选" }).click();
  const searchInput = page.getByPlaceholder("搜索商品或任务号");
  await searchInput.fill("P000001");
  check("search keeps matching product", await page.getByRole("button", { name: /P000001/ }).isVisible());
  await searchInput.fill("不存在");
  check("search shows empty state", await page.getByText("没有匹配的商品", { exact: true }).isVisible());
  await searchInput.fill("");

  const statusSelect = page.locator(".compact-select select");
  await statusSelect.selectOption("attention");
  check("status filter shows empty state", await page.getByText("没有匹配的商品", { exact: true }).isVisible());
  await statusSelect.selectOption("all");
  check("status reset restores product", await page.getByRole("button", { name: /P000001/ }).isVisible());

  await page.getByRole("button", { name: "最新在前" }).click();
  const sortToast = page.getByText("当前只有 1 件商品，已按最新在前排列", { exact: true });
  await sortToast.waitFor({ state: "visible", timeout: 1200 });
  check("sort feedback appears", await sortToast.isVisible());

  await page.getByRole("button", { name: "查看全部活动" }).click();
  check("activity list expands", await page.getByText("上传前检查完成", { exact: true }).isVisible());
  await page.getByRole("button", { name: "收起活动" }).click();
  await sortToast.waitFor({ state: "hidden", timeout: 4000 });

  await page.locator(".focus-actions .primary-cta").click();
  check("upload check modal opens", await page.getByRole("dialog", { name: "上传前安全检查" }).isVisible());
  check("modal states no remote call", await page.getByText(/不会连接 Factory 后端/).isVisible());
  await page.waitForTimeout(320);
  await page.screenshot({ path: path.join(outputDir, "desktop-upload-check.png"), fullPage: true });

  await page.getByRole("button", { name: "返回检查" }).click();
  check("modal closes with return", !(await page.getByRole("dialog", { name: "上传前安全检查" }).isVisible().catch(() => false)));

  await page.locator(".focus-actions .primary-cta").click();
  await page.keyboard.press("Escape");
  check("modal closes with Escape", !(await page.getByRole("dialog", { name: "上传前安全检查" }).isVisible().catch(() => false)));

  await page.locator(".focus-actions .primary-cta").click();
  await page.getByRole("button", { name: "模拟检查完成" }).click();
  const successToast = page.getByText("演示检查已完成：未发送任何 Ozon 请求", { exact: true });
  await successToast.waitFor({ state: "visible", timeout: 2500 });
  check("simulation success feedback appears", await successToast.isVisible());
  await page.screenshot({ path: path.join(outputDir, "desktop-success-feedback.png"), fullPage: true });

  await page.getByRole("button", { name: "选品与关键词" }).click();
  const navigationToast = page.getByText("独立原型当前只演示“我的采集箱”核心页面", { exact: true });
  await navigationToast.waitFor({ state: "visible", timeout: 1200 });
  check("out-of-scope navigation gives feedback", await navigationToast.isVisible());
  await navigationToast.waitFor({ state: "hidden", timeout: 4000 });

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(450);
  check("narrow desktop has no horizontal overflow", await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
  check("narrow desktop keeps primary action", await page.locator(".focus-actions .primary-cta").isVisible());
  await page.screenshot({ path: path.join(outputDir, "desktop-narrow-1024.png"), fullPage: true });
} finally {
  await writeFile(
    path.join(outputDir, "validation-results.json"),
    JSON.stringify({
      url: baseUrl,
      sourceViewport: { width: 1487, height: 1058 },
      checks,
      consoleErrors,
      pageErrors,
      passed: checks.every((item) => item.passed) && consoleErrors.length === 0 && pageErrors.length === 0,
      capturedAt: new Date().toISOString(),
    }, null, 2),
  );
  await browser.close();
}

if (consoleErrors.length || pageErrors.length) {
  throw new Error(`Browser errors: ${JSON.stringify({ consoleErrors, pageErrors })}`);
}

console.log(`Validated ${checks.length} checks. Artifacts: ${outputDir}`);
