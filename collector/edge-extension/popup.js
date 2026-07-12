const DEFAULT_FACTORY_URL = "http://127.0.0.1:8765";
let factoryConfig = { baseUrl: DEFAULT_FACTORY_URL, accessCode: "" };

function normalizeFactoryUrl(value) {
  const url = new URL(String(value || DEFAULT_FACTORY_URL).trim());
  if (url.protocol !== "http:") throw new Error("工作台地址必须以 http:// 开头");
  const host = url.hostname.toLowerCase();
  const privateIpv4 = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
  if (host !== "127.0.0.1" && host !== "localhost" && !host.endsWith(".local") && !privateIpv4) {
    throw new Error("只允许填写主电脑的局域网地址");
  }
  return `${url.protocol}//${url.host}`;
}

async function loadFactoryConfig() {
  const stored = await chrome.storage.local.get(["factoryBaseUrl", "factoryAccessCode"]);
  factoryConfig = {
    baseUrl: normalizeFactoryUrl(stored.factoryBaseUrl || DEFAULT_FACTORY_URL),
    accessCode: String(stored.factoryAccessCode || "").trim()
  };
  return factoryConfig;
}

async function factoryFetch(path, options = {}) {
  await loadFactoryConfig();
  const headers = { ...(options.headers || {}) };
  if (factoryConfig.accessCode) headers["X-Factory-Access-Code"] = factoryConfig.accessCode;
  return fetch(`${factoryConfig.baseUrl}${path}`, { ...options, headers });
}

const els = {
  status: document.getElementById("page-status"),
  title: document.getElementById("title"),
  mainCount: document.getElementById("main-count"),
  skuCount: document.getElementById("sku-count"),
  detailCount: document.getElementById("detail-count"),
  capture: document.getElementById("capture"),
  previewToggle: document.getElementById("preview-toggle"),
  debugExport: document.getElementById("debug-export"),
  openInbox: document.getElementById("open-inbox"),
  preview: document.getElementById("preview"),
  skuList: document.getElementById("sku-list"),
  mainThumbs: document.getElementById("main-thumbs"),
  detailThumbs: document.getElementById("detail-thumbs"),
  duplicate: document.getElementById("duplicate"),
  duplicateMessage: document.getElementById("duplicate-message"),
  openExisting: document.getElementById("open-existing"),
  createVersion: document.getElementById("create-version"),
  progress: document.getElementById("progress"),
  result: document.getElementById("result")
  ,factoryUrl: document.getElementById("factory-url")
  ,factoryAccessCode: document.getElementById("factory-access-code")
  ,saveConnection: document.getElementById("save-connection")
  ,testConnection: document.getElementById("test-connection")
  ,connectionResult: document.getElementById("connection-result")
};

let latestCapture = null;
let duplicateProductId = null;

function setResult(value) {
  els.result.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

function sendToTab(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(response);
    });
  });
}

async function waitForSkuSelection(tabId, capture, previousSelectedSkuIds = []) {
  await sendToTab(tabId, {
    type: "OPEN_SKU_SELECTOR",
    capture,
    previous_selected_sku_ids: previousSelectedSkuIds
  });
  window.close();
  return null;
}

async function loadPreview() {
  try {
    const tab = await getActiveTab();
    if (!tab || !tab.url || !/https:\/\/[^/]*1688\.com\//.test(tab.url)) {
      els.status.textContent = "当前页面不是可采集的1688商品页";
      return;
    }
    latestCapture = await sendToTab(tab.id, { type: "COLLECTOR_PREVIEW" });
    if (!latestCapture || !latestCapture.is_collectable) {
      els.status.textContent = latestCapture?.reason || "当前页面不可采集";
      setResult(latestCapture || {});
      return;
    }
    els.status.textContent = "可采集";
    els.title.textContent = latestCapture.title_cn || "unknown";
    els.mainCount.textContent = latestCapture.main_images.length;
    els.skuCount.textContent = latestCapture.skus.length;
    els.detailCount.textContent = latestCapture.detail_images.length;
    els.capture.disabled = false;
    els.previewToggle.disabled = false;
    els.debugExport.disabled = false;
    renderPreview(latestCapture);
    setResult({
      warnings: latestCapture.capture_warnings,
      diagnostics: latestCapture.field_diagnostics,
      sku_debug: latestCapture.raw_snapshot?.sku_debug || null
    });
  } catch (error) {
    els.status.textContent = "无法读取页面";
    setResult(error.message);
  }
}

function renderPreview(capture) {
  els.skuList.innerHTML = "";
  els.mainThumbs.innerHTML = "";
  els.detailThumbs.innerHTML = "";
  (capture.skus || []).slice(0, 50).forEach((sku) => {
    const li = document.createElement("li");
    const dimensions = (sku.option_values || []).map((item) => `${item.name_cn || "规格"}:${item.value_cn || "unknown"}`).join("；");
    const imageState = sku.sku_image_missing ? "无SKU图" : "有SKU图";
    li.textContent = [sku.sku_name, dimensions, sku.purchase_price ? `¥${sku.purchase_price}` : "¥unknown", sku.price_source || "unknown", imageState, sku.sku_id || "unknown"].filter(Boolean).join(" / ") || "unknown";
    els.skuList.appendChild(li);
  });
  (capture.main_images || []).slice(0, 20).forEach((item) => {
    const img = document.createElement("img");
    img.src = item.url;
    img.title = item.url;
    els.mainThumbs.appendChild(img);
  });
  (capture.detail_images || []).slice(0, 30).forEach((item) => {
    const img = document.createElement("img");
    img.src = item.url;
    img.title = item.url;
    els.detailThumbs.appendChild(img);
  });
}

async function exportSkuDebug() {
  els.debugExport.disabled = true;
  els.progress.textContent = "正在导出SKU诊断...";
  try {
    const tab = await getActiveTab();
    const debug = await sendToTab(tab.id, { type: "EXPORT_SKU_DEBUG" });
    els.progress.textContent = "已导出 sku-debug.json";
    setResult({
      total_skus: debug.total_skus,
      real_sku_ids: debug.real_sku_ids,
      sku_with_images: debug.sku_with_images,
      sku_with_prices: debug.sku_with_prices,
      data_sources: debug.data_sources
    });
  } catch (error) {
    els.progress.textContent = "导出失败";
    setResult(error.message);
  } finally {
    els.debugExport.disabled = false;
  }
}

async function checkDuplicate(sourceUrl) {
  const response = await factoryFetch(`/api/collector/duplicates?source_url=${encodeURIComponent(sourceUrl)}`);
  if (!response.ok) return { exists: false };
  return response.json();
}

async function postCapture(capture, allowNewVersion = false) {
  const body = { ...capture };
  if (allowNewVersion) body.allow_new_version = true;
  const response = await factoryFetch("/api/collector/products", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.detail ? JSON.stringify(result.detail) : `HTTP ${response.status}`);
    error.status = response.status;
    error.body = result;
    throw error;
  }
  return result;
}

async function captureCurrentProduct(allowNewVersion = false) {
  els.capture.disabled = true;
  els.duplicate.hidden = true;
  els.progress.textContent = "读取页面数据...";
  try {
    const tab = await getActiveTab();
    const capture = await sendToTab(tab.id, { type: "COLLECTOR_CAPTURE" });
    if (!allowNewVersion) {
      const duplicate = await checkDuplicate(capture.source_url);
      if (duplicate.exists) {
        duplicateProductId = duplicate.product_id;
        els.progress.textContent = "该产品已经采集过。";
        els.duplicateMessage.textContent = `已有商品：${duplicate.product_id}`;
        els.duplicate.hidden = false;
        setResult(duplicate);
        return;
      }
    }
    els.progress.textContent = "请在页面右侧选择需要采集的SKU...";
    await waitForSkuSelection(tab.id, { ...capture, allow_new_version: allowNewVersion });
  } catch (error) {
    els.progress.textContent = "采集失败";
    setResult(error.message);
  } finally {
    els.capture.disabled = false;
  }
}

els.capture.addEventListener("click", () => captureCurrentProduct(false));
els.previewToggle.addEventListener("click", () => {
  els.preview.hidden = !els.preview.hidden;
});
els.debugExport.addEventListener("click", exportSkuDebug);
els.openInbox.addEventListener("click", async () => {
  await loadFactoryConfig();
  chrome.tabs.create({ url: `${factoryConfig.baseUrl}/workbench` });
});
els.openExisting.addEventListener("click", () => {
  if (duplicateProductId) {
    els.progress.textContent = `已有商品目录：products/${duplicateProductId}`;
    setResult({ existing_product_id: duplicateProductId, path: `products/${duplicateProductId}` });
  }
});
els.createVersion.addEventListener("click", () => captureCurrentProduct(true));

async function saveConnection() {
  try {
    const baseUrl = normalizeFactoryUrl(els.factoryUrl.value);
    const accessCode = els.factoryAccessCode.value.trim();
    await chrome.storage.local.set({ factoryBaseUrl: baseUrl, factoryAccessCode: accessCode });
    factoryConfig = { baseUrl, accessCode };
    els.connectionResult.textContent = "主电脑连接设置已保存";
    await testConnection();
  } catch (error) {
    els.connectionResult.textContent = error.message;
  }
}

async function testConnection() {
  els.connectionResult.textContent = "正在连接主电脑...";
  try {
    const response = await factoryFetch("/api/workbench/summary");
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail?.message || result.detail || `HTTP ${response.status}`);
    els.connectionResult.textContent = "已连接主电脑";
  } catch (error) {
    els.connectionResult.textContent = `连接失败：${error.message}`;
  }
}

els.saveConnection.addEventListener("click", saveConnection);
els.testConnection.addEventListener("click", testConnection);

async function initialize() {
  await loadFactoryConfig();
  els.factoryUrl.value = factoryConfig.baseUrl;
  els.factoryAccessCode.value = factoryConfig.accessCode;
  await loadPreview();
}

initialize();
