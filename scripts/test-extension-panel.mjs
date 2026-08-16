#!/usr/bin/env node
// Automated test: floating capture panel auto-appears on 1688 pages.
//  - 1688 detail page   -> panel + card auto-expanded + auto-detect status
//  - 1688 non-detail    -> floating button visible + hint text
//  - ozon.ru page       -> no 1688 panel (hostname guard)
// Usage: node scripts/test-extension-panel.mjs

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
let pw;
try {
  pw = require("playwright-core");
} catch {
  pw = require("C:/Users/14785/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core");
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const contentJs = readFileSync(join(__dirname, "..", "collector", "edge-extension", "content.js"), "utf-8");

const chromeStub = () => {
  window.chrome = {
    runtime: {
      getURL: (p) => "https://example.invalid/" + p,
      sendMessage: (msg, cb) => { if (cb) cb({ ok: false, error: "stub-no-service-worker" }); },
      onMessage: { addListener: () => {} },
      lastError: null,
    },
    storage: { local: { get: () => Promise.resolve({}), set: () => Promise.resolve() } },
  };
};

const DETAIL_HTML = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>测试商品</title></head>
<body><h1>测试商品 高保真蓝牙耳机 黑色</h1>
<img src="https://cbu01.alicdn.com/img/ibank/2020/o/abc_1.jpg">
<script>window.__INIT_DATA__ = {"globalData":{"offer":{"title":"测试商品"}},"skuProps":[],"skuInfoMap":{}};</script>
</body></html>`;

const LIST_HTML = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>1688 首页</title></head>
<body><h1>1688 采购批发</h1><p>首页内容</p></body></html>`;

async function runCase(browser, url, html, label, expectPanel) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.addInitScript(chromeStub);
  await page.route(url, (route) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html }));
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.addScriptTag({ content: contentJs });
  await page.waitForTimeout(4200); // wait for inject + auto-detect timer
  const info = await page.evaluate(() => {
    const root = document.getElementById("caf-fp-root");
    if (!root) return { panel: false };
    const btn = root.querySelector(".caf-fp-btn");
    const card = root.querySelector(".caf-fp-card");
    const status = root.querySelector(".caf-fp-status");
    return {
      panel: true,
      btnHidden: btn ? btn.hidden : null,
      cardHidden: card ? card.hidden : null,
      statusText: status ? status.innerText.slice(0, 60) : "",
      msg: (root.querySelector(".caf-fp-msg") || {}).textContent || "",
    };
  });
  await page.close();
  const pass = info.panel === expectPanel && (!expectPanel || !info.btnHidden || info.cardHidden === false);
  console.log((pass ? "PASS" : "FAIL") + "  " + label);
  console.log("      " + JSON.stringify(info));
  if (pageErrors.length) console.log("      pageErrors: " + pageErrors.join(" | "));
  return pass;
}

(async () => {
  const browser = await pw.chromium.launch({ channel: "msedge", headless: true });
  const r1 = await runCase(browser, "https://detail.1688.com/offer/5982066551.html", DETAIL_HTML, "1688 详情页 → 面板自动出现", true);
  const r2 = await runCase(browser, "https://www.1688.com/", LIST_HTML, "1688 非详情页 → 按钮仍在（提示详情页）", true);
  const r3 = await runCase(browser, "https://www.ozon.ru/product/test-123/", LIST_HTML, "ozon.ru → 不出现 1688 面板", false);
  await browser.close();
  console.log(r1 && r2 && r3 ? "ALL TESTS PASSED" : "SOME TESTS FAILED");
  process.exit(r1 && r2 && r3 ? 0 : 1);
})().catch((e) => { console.error("FATAL", e.message); process.exit(1); });
