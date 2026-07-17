import { chromium } from "playwright-core";
import { readFile } from "node:fs/promises";
import path from "node:path";

const qaDir = path.resolve(import.meta.dirname, "../qa-artifacts");
const reference = (await readFile(path.join(qaDir, "desktop-default.png"))).toString("base64");
const implementation = (await readFile(path.join(qaDir, "real-after/08-product-review.png"))).toString("base64");
const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1456, height: 610 }, deviceScaleFactor: 1 });
await page.setContent(`<!doctype html><html><style>
  *{box-sizing:border-box}body{margin:0;padding:16px;background:#e6e7ed;font:700 13px system-ui;color:#292a33}
  main{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0;padding:10px;background:white;border:1px solid #d7d8df;border-radius:16px}
  figcaption{height:28px;display:flex;align-items:center;padding:0 4px;color:#6e707a;font-size:11px;letter-spacing:.08em;text-transform:uppercase}
  img{width:100%;display:block;border-radius:10px}
</style><main>
  <figure><figcaption>Approved direction</figcaption><img src="data:image/png;base64,${reference}"></figure>
  <figure><figcaption>Real AI Factory workbench</figcaption><img src="data:image/png;base64,${implementation}"></figure>
</main></html>`);
await page.screenshot({ path: path.join(qaDir, "real-vs-approved.png"), fullPage: true });
await browser.close();
