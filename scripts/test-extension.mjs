#!/usr/bin/env node
// Automated browser test for the 1688 collector content script.
// Usage: node scripts/test-extension.mjs
// Loads content.js into a realistic 1688 detail-page fixture (both legacy
// array-based and current object-based __INIT_DATA__ structures) and asserts
// the capture pipeline does not crash and returns product data.

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
const DETAIL_URL = "https://detail.1688.com/offer/5982066551.html";

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

const FIXTURE_OBJECT_DATA = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>测试商品 高保真蓝牙耳机</title></head>
<body>
<h1>测试商品 高保真蓝牙耳机 黑色</h1>
<img src="https://cbu01.alicdn.com/img/ibank/2020/o/abc_1.jpg">
<img src="https://cbu01.alicdn.com/img/ibank/2020/o/abc_2.jpg">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"测试商品 高保真蓝牙耳机","image":["https://cbu01.alicdn.com/img/ibank/2020/o/ld_1.jpg"],"offers":{"price":"99.00","priceCurrency":"CNY"}}
</script>
<script>
window.__INIT_DATA__ = {"globalData":{"offer":{"title":"测试商品 高保真蓝牙耳机"}},"skuProps":[{"prop":"颜色","value":[{"name":"黑色"},{"name":"白色"}]}],"skuInfoMap":{"1":{"skuId":"1","price":"99.00","specId":"1"}}};
</script>
</body></html>`;

const FIXTURE_ARRAY_DATA = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>测试商品 高保真蓝牙耳机</title></head>
<body>
<h1>测试商品 高保真蓝牙耳机 黑色</h1>
<img src="https://cbu01.alicdn.com/img/ibank/2020/o/abc_1.jpg">
<script>
window.__INIT_DATA__ = [{"data":[{"offerImgList":["https://cbu01.alicdn.com/img/ibank/2020/o/ld_1.jpg"],"title":"测试商品 高保真蓝牙耳机"}]}];
</script>
</body></html>`;

async function runCase(browser, html, label) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.addInitScript(chromeStub);
  await page.route(DETAIL_URL, (route) =>
    route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html }),
  );
  await page.goto(DETAIL_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.addScriptTag({ content: contentJs });
  await page.waitForTimeout(1200);
  let result;
  try {
    result = await page.evaluate(async () => {
      const c = await buildReadyCapture();
      return {
        ok: true,
        collectable: c.is_collectable,
        title: c.title_cn,
        skus: (c.skus || []).length,
        main: (c.main_images || []).length,
        detail: (c.detail_images || []).length,
        warnings: (c.capture_warnings || []).slice(0, 8),
      };
    });
  } catch (e) {
    result = { ok: false, error: String(e && e.message || e) };
  }
  await page.close();
  const pass = result.ok && result.collectable && !/forEach/.test((result.warnings || []).join(" "));
  console.log((pass ? "PASS" : "FAIL") + "  " + label);
  console.log("      " + JSON.stringify(result).slice(0, 300));
  if (pageErrors.length) console.log("      pageErrors: " + pageErrors.join(" | "));
  return pass;
}

(async () => {
  const browser = await pw.chromium.launch({ channel: "msedge", headless: true });
  const r1 = await runCase(browser, FIXTURE_OBJECT_DATA, "object __INIT_DATA__ (current 1688)");
  const r2 = await runCase(browser, FIXTURE_ARRAY_DATA, "array __INIT_DATA__ (legacy)");
  await browser.close();
  console.log(r1 && r2 ? "ALL TESTS PASSED" : "SOME TESTS FAILED");
  process.exit(r1 && r2 ? 0 : 1);
})().catch((e) => { console.error("FATAL", e.message); process.exit(1); });
