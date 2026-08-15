#!/usr/bin/env node
/*
 * Local Seerfar browser worker.
 *
 * Uses its own persistent Chrome profile so the workbench can read visible
 * Seerfar results after one interactive login. No extension, API key, Ozon
 * write endpoint, inventory, warehouse, or activation endpoint is used.
 */

import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const FACTORY_URL = process.env.JLC_FACTORY_URL || "http://127.0.0.1:8765";
const PROFILE_DIR = path.join(ROOT, "runtime", "seerfar-browser-profile");
const POLL_MS = 5_000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function api(pathname, options = {}) {
  const response = await fetch(`${FACTORY_URL}${pathname}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Factory-Device-Id": "seerfar-local-browser-worker",
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail?.message || body.detail || `HTTP ${response.status}`);
  return body;
}

async function loginRequired(page) {
  const text = `${await page.title().catch(() => "")} ${await page.locator("body").innerText().catch(() => "")}`.toLowerCase();
  return page.url().includes("login") || /请先登录|登录后|账号登录|扫码登录|sign in|log in/.test(text);
}

async function resultRows(page, mode) {
  return page.locator("table").evaluateAll((tables, expectedMode) => {
    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const number = (value) => Number(String(value || "").replace(/[^0-9,.-]/g, "").replace(/,/g, ".")) || 0;
    for (const table of tables) {
      const tr = Array.from(table.querySelectorAll("tr"));
      const header = tr.find((row) => {
        const text = clean(row.textContent);
        return expectedMode === "keyword_reverse"
          ? /搜索查询|关键词/.test(text) && /一直在找|搜索量|搜索人数/.test(text)
          : /关键词/.test(text) && /月搜热度/.test(text);
      });
      if (!header) continue;
      const labels = Array.from(header.querySelectorAll("th,td")).map((cell) => clean(cell.textContent));
      const queryIndex = labels.findIndex((label) => /搜索查询|关键词/.test(label));
      const valueIndex = labels.findIndex((label) => expectedMode === "keyword_reverse" ? /一直在找|搜索量|搜索人数/.test(label) : /月搜热度/.test(label));
      if (queryIndex < 0 || valueIndex < 0) continue;
      const rows = tr.slice(tr.indexOf(header) + 1).map((row) => Array.from(row.querySelectorAll("td")).map((cell) => clean(cell.textContent)))
        .map((cells) => expectedMode === "keyword_reverse"
          ? { query: cells[queryIndex], search_count: number(cells[valueIndex]) }
          : { query: cells[queryIndex], monthly_search_heat: number(cells[valueIndex]) })
        .filter((row) => row.query && (row.search_count || row.monthly_search_heat) > 0);
      if (rows.length) return rows;
    }
    return [];
  }, mode);
}

async function rowsSignature(page, mode) {
  const rows = await resultRows(page, mode);
  return {
    rows,
    signature: rows.slice(0, 10).map((row) => `${row.query}:${row.search_count || row.monthly_search_heat}`).join("|"),
  };
}

async function waitForRows(page, mode, previousSignature) {
  let sawLoading = false;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const button = page.locator("#search, .quick-search").first();
    const loading = await button.evaluate((element) => element.classList.contains("btn-loading") || element.classList.contains("disabled")).catch(() => false);
    sawLoading ||= loading;
    const { rows, signature } = await rowsSignature(page, mode);
    // Never import the previous table. A query is accepted only after the
    // page loaded and produced a different visible result set.
    if (sawLoading && !loading && rows.length && signature && signature !== previousSignature) return rows;
    await sleep(1_000);
  }
  return [];
}

async function runJob(page, job) {
  const mode = job.mode === "keyword_reverse" ? "keyword_reverse" : "keyword_miner";
  const target = `https://seerfar.cn/admin/${mode === "keyword_reverse" ? "keyword-reverse" : "keyword-miner"}.html`;
  if (!page.url().includes(mode === "keyword_reverse" ? "keyword-reverse" : "keyword-miner")) {
    await page.goto(target, { waitUntil: "domcontentloaded", timeout: 60_000 });
  }
  if (await loginRequired(page)) throw new Error("SEERFAR_LOGIN_REQUIRED: 请在工作台 Seerfar 浏览器窗口登录");
  const { signature: previousSignature } = await rowsSignature(page, mode);
  if (mode === "keyword_reverse") {
    // Seerfar renders this field as Select2: the original <select> keeps the
    // placeholder, while the actual text input is transient. Add the SKU to
    // the Select2 value, then use the normal visible query button.
    await page.locator("#reverse-keyword").evaluate((select, sku) => {
      select.replaceChildren(new Option(sku, sku, true, true));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      window.jQuery?.(select).trigger("change");
    }, String(job.seed_keyword || ""));
  } else {
    const input = page.locator("input[placeholder*='关键词']:visible, input[placeholder*='关键字']:visible, textarea[placeholder*='关键词']:visible, textarea[placeholder*='关键字']:visible").first();
    await input.fill(String(job.seed_keyword || ""));
  }
  await page.locator("#search, .quick-search").first().click();
  const rows = await waitForRows(page, mode, previousSignature);
  if (!rows.length) throw new Error(mode === "keyword_reverse" ? "SEERFAR_REVERSE_EMPTY: Seerfar 未返回竞品反查词" : "Seerfar 页面没有返回关键词挖掘结果");
  await api("/api/workbench/market-intelligence/search-visibility/seerfar/import", {
    method: "POST",
    body: JSON.stringify({
      job_id: job.job_id,
      product_id: job.product_id,
      store_id: job.shop_id,
      seed_keyword: job.seed_keyword,
      mode,
      rows,
    }),
  });
}

async function main() {
  await mkdir(PROFILE_DIR, { recursive: true });
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: "chrome",
    headless: false,
    viewport: { width: 1280, height: 900 },
  });
  const page = context.pages()[0] || await context.newPage();
  await page.goto("https://seerfar.cn/admin/keyword-reverse.html", { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
  for (;;) {
    try {
      const sessionState = await loginRequired(page) ? "login_required" : "logged_in";
      const { job } = await api(`/api/workbench/market-intelligence/search-visibility/seerfar/next?session_state=${sessionState}`);
      if (!job) {
        await sleep(POLL_MS);
        continue;
      }
      try {
        await runJob(page, job);
      } catch (error) {
        await api("/api/workbench/market-intelligence/search-visibility/seerfar/fail", {
          method: "POST",
          body: JSON.stringify({ job_id: job.job_id, error: String(error?.message || error) }),
        });
      }
      // Keep the visible Seerfar session stable instead of issuing a burst of UI searches.
      await sleep(3_000);
    } catch {
      await sleep(POLL_MS);
    }
  }
}

void main();
