import { chromium } from "@playwright/test";
import { existsSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";

const baseUrl = process.env.COMMAND_CENTER_URL || "http://127.0.0.1:5174/";
const artifactDir = resolve("artifacts");
mkdirSync(artifactDir, { recursive: true });

const localChromium = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ].find((path) => existsSync(path));

async function launchBrowser() {
  try {
    return await chromium.launch({ headless: true });
  } catch (error) {
    if (!localChromium) throw error;
    return chromium.launch({ headless: true, executablePath: localChromium });
  }
}

const browser = await launchBrowser();
const errors = [];

async function verifyViewport(name, viewport) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  page.on("pageerror", (error) => errors.push(`${name}: ${error.message}`));
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && !text.includes("Failed to load resource: the server responded with a status of 404")) {
      errors.push(`${name}: ${text}`);
    }
  });

  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector(".command-center", { state: "visible", timeout: 10000 });
  await page.waitForFunction(() => (
    Boolean(document.querySelector(".production-stage"))
    || Boolean(document.querySelector(".ozon-optimization-stage"))
  ), null, { timeout: 60000 });
  await page.waitForFunction(() => (
    Boolean(document.querySelector(".hud-image-zone"))
    || Boolean(document.querySelector(".ozon-product-list"))
    || Boolean(document.querySelector(".ozon-product-overview"))
    || Boolean(document.querySelector(".ozon-opt-empty"))
  ), null, { timeout: 10000 });
  await page.screenshot({
    path: resolve(artifactDir, `command-center-${name}.png`),
    fullPage: true,
  });
  await page.close();
}

try {
  await verifyViewport("desktop", { width: 1440, height: 980 });
  await verifyViewport("wide", { width: 1720, height: 1040 });
} finally {
  await browser.close();
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}

console.log(`Command Center UI verified: ${baseUrl}`);
console.log(`Screenshots saved to ${artifactDir}`);
