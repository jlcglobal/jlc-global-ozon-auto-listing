const DEFAULT_FACTORY_URL = "http://127.0.0.1:8765";
const STALE_DEFAULT_FACTORY_URL = "http://192.168.3.13:8765"; // old hardcoded LAN default, reset to localhost
const COMMAND_CENTER_QUERY_VERSION = "2026-08-16-ui-v2";
const LEGACY_LOCAL_FACTORY_URLS = new Set([
    "http://127.0.0.1:8765",
    "http://localhost:8765"
]);
let factoryConfig = { baseUrl: DEFAULT_FACTORY_URL, deviceId: "" };
async function ensureFactoryDeviceId() {
    const stored = await chrome.storage.local.get(["factoryDeviceId"]);
    let deviceId = String(stored.factoryDeviceId || "").trim();
    if (!deviceId) {
        deviceId = globalThis.crypto?.randomUUID?.() || `device-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
        await chrome.storage.local.set({ factoryDeviceId: deviceId });
    }
    return deviceId;
}
function normalizeFactoryUrl(value) {
    const url = new URL(String(value || DEFAULT_FACTORY_URL).trim());
    if (url.protocol !== "http:")
        throw new Error("工作台地址必须以 http:// 开头");
    const host = url.hostname.toLowerCase();
    const privateIpv4 = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
    if (host !== "127.0.0.1" && host !== "localhost" && !host.endsWith(".local") && !privateIpv4) {
        throw new Error("只允许填写主电脑的局域网地址");
    }
    return `${url.protocol}//${url.host}`;
}
function workbenchEntryUrl(kind, extra = {}) {
    const path = kind === "ozon" ? "/ozon-reference" : "/1688-collection";
    const params = new URLSearchParams({ v: COMMAND_CENTER_QUERY_VERSION });
    if (extra.product_id)
        params.set("product_id", String(extra.product_id));
    if (extra.task_id)
        params.set("task_id", String(extra.task_id));
    return `${factoryConfig.baseUrl}${path}?${params.toString()}`;
}
function cleanFactoryUrlText(value) {
    return String(value || "").trim().replace(/\/+$/, "");
}
function isLegacyLocalFactoryUrl(value) {
    return LEGACY_LOCAL_FACTORY_URLS.has(cleanFactoryUrlText(value));
}
function factoryUrlOrDefault(value) {
    const text = cleanFactoryUrlText(value);
    if (!text || text === STALE_DEFAULT_FACTORY_URL)
        return DEFAULT_FACTORY_URL;
    return text;
}
async function loadFactoryConfig() {
    const stored = await chrome.storage.local.get(["factoryBaseUrl"]);
    const baseUrl = normalizeFactoryUrl(factoryUrlOrDefault(stored.factoryBaseUrl));
    if (!cleanFactoryUrlText(stored.factoryBaseUrl) || cleanFactoryUrlText(stored.factoryBaseUrl) === STALE_DEFAULT_FACTORY_URL) {
        await chrome.storage.local.set({ factoryBaseUrl: baseUrl });
    }
    factoryConfig = {
        baseUrl,
        deviceId: await ensureFactoryDeviceId()
    };
    return factoryConfig;
}
async function factoryFetch(path, options = {}) {
    await loadFactoryConfig();
    const headers = { ...(options.headers || {}) };
    headers["X-Factory-Device-Id"] = factoryConfig.deviceId;
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
    result: document.getElementById("result"),
    factoryUrl: document.getElementById("factory-url"),
    saveConnection: document.getElementById("save-connection"),
    testConnection: document.getElementById("test-connection"),
    connectionResult: document.getElementById("connection-result")
};
let latestCapture = null;
let duplicateProductId = null;
let activePageKind = "unsupported";
function setResult(value) {
    els.result.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}
function skuImagePreflight(capture) {
    const debug = capture?.raw_snapshot?.sku_debug || {};
    const total = Number(capture?.sku_image_preflight?.total_skus || debug.total_skus || capture?.skus?.length || 0);
    const withImages = Number(capture?.sku_image_preflight?.sku_with_images || debug.sku_with_images || 0);
    const missing = capture?.sku_image_preflight?.missing_sku_ids || debug.missing_image_skus || [];
    return {
        total,
        withImages,
        missing,
        complete: total > 0 && missing.length === 0 && withImages === total
    };
}
function showSkuImageWarning(capture) {
    const check = skuImagePreflight(capture);
    if (check.complete)
        return false;
    const sample = check.missing.slice(0, 5).join("、") || "未识别SKU";
    els.status.textContent = `可采集：SKU图片${check.withImages}/${check.total}，缺图将保留标记`;
    els.progress.textContent = "缺图SKU可继续选择；生图前需要人工确认参考图";
    setResult({
        code: "SKU_IMAGES_INCOMPLETE_WARNING",
        message: `1688页面识别到${check.withImages}/${check.total}个SKU图片，缺少${check.missing.length}个。仍可采集；系统会保留真实缺图状态，生图前再由你确认共用哪张同外观SKU实拍图。`,
        missing_sku_ids: check.missing,
        examples: sample
    });
    return false;
}
async function getActiveTab() {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    return tabs[0];
}
function sendToTab(tabId, message) {
    return new Promise((resolve, reject) => {
        chrome.tabs.sendMessage(tabId, message, (response) => {
            const err = chrome.runtime.lastError;
            if (err)
                reject(new Error(err.message));
            else
                resolve(response);
        });
    });
}

function ensureContentScriptInjected(tabId) {
    return chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] })
        .then(() => true)
        .catch(() => false);
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readPreviewFromTab(tabId, type) {
    try {
        return await sendToTab(tabId, { type });
    }
    catch (error) {
        const injected = await ensureContentScriptInjected(tabId);
        if (!injected)
            throw error;
        await sleep(250);
        return await sendToTab(tabId, { type });
    }
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
        const is1688Page = Boolean(tab?.url && /https:\/\/[^/]*1688\.com\//.test(tab.url));
        const isOzonPage = Boolean(tab?.url && /https:\/\/[^/]*ozon\.ru\/product\//.test(tab.url));
        if (!tab || !tab.url || (!is1688Page && !isOzonPage)) {
            activePageKind = "unsupported";
            els.status.textContent = "当前页面不是可采集的1688商品页或Ozon商品页";
            return;
        }
        activePageKind = isOzonPage ? "ozon" : "1688";
        latestCapture = await readPreviewFromTab(tab.id, isOzonPage ? "COLLECTOR_OZON_PREVIEW" : "COLLECTOR_PREVIEW");
        if (!latestCapture || !latestCapture.is_collectable) {
            els.status.textContent = latestCapture?.reason || "当前页面不可采集";
            setResult(latestCapture || {});
            return;
        }
        if (isOzonPage) {
            els.status.textContent = "可采集Ozon参考页（浏览器已打开，绕开307重定向）";
            els.title.textContent = latestCapture.title || latestCapture.title_ru || "unknown";
            els.mainCount.textContent = latestCapture.image_urls?.length || latestCapture.main_images?.length || 0;
            els.skuCount.textContent = 1;
            els.detailCount.textContent = latestCapture.detail_images?.length || 0;
            els.capture.textContent = "采集当前Ozon参考页";
            els.capture.disabled = false;
            els.previewToggle.disabled = false;
            els.debugExport.disabled = true;
            renderPreview(latestCapture);
            setResult({
                source_url: latestCapture.source_url,
                title: latestCapture.title,
                image_count: latestCapture.image_urls?.length || 0,
                warnings: latestCapture.capture_warnings,
            });
            return;
        }
        const skuCheck = skuImagePreflight(latestCapture);
        els.status.textContent = skuCheck.complete
            ? "可采集（SKU图片已完整加载）"
            : `可采集（SKU图片${skuCheck.withImages}/${skuCheck.total}，缺图已标记）`;
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
    }
    catch (error) {
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
    }
    catch (error) {
        els.progress.textContent = "导出失败";
        setResult(error.message);
    }
    finally {
        els.debugExport.disabled = false;
    }
}
async function checkDuplicate(sourceUrl) {
    const response = await factoryFetch(`/api/collector/duplicates?source_url=${encodeURIComponent(sourceUrl)}`);
    if (!response.ok)
        return { exists: false };
    return response.json();
}
async function postCapture(capture, allowNewVersion = false) {
    const body = { ...capture };
    if (allowNewVersion)
        body.allow_new_version = true;
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
async function postOzonReferencePage(capture) {
    const response = await factoryFetch("/api/collector/ozon-reference-page", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(capture)
    });
    const result = await response.json();
    if (!response.ok) {
        const error = new Error(result.detail?.message || result.detail || `HTTP ${response.status}`);
        error.status = response.status;
        error.body = result;
        throw error;
    }
    return result;
}
async function captureCurrentProduct(allowNewVersion = false) {
    if (activePageKind === "ozon") {
        await captureCurrentOzonReference();
        return;
    }
    els.capture.disabled = true;
    els.duplicate.hidden = true;
    els.progress.textContent = "读取页面数据...";
    try {
        const tab = await getActiveTab();
        const capture = await sendToTab(tab.id, { type: "COLLECTOR_CAPTURE" });
        showSkuImageWarning(capture);
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
    }
    catch (error) {
        els.progress.textContent = "采集失败";
        setResult(error.message);
    }
    finally {
        els.capture.disabled = false;
    }
}
async function captureCurrentOzonReference() {
    els.capture.disabled = true;
    els.duplicate.hidden = true;
    els.progress.textContent = "正在读取当前Ozon页面...";
    try {
        const tab = await getActiveTab();
        const capture = await sendToTab(tab.id, { type: "COLLECTOR_OZON_CAPTURE" });
        if (!capture?.is_collectable)
            throw new Error(capture?.reason || "当前页面不是Ozon商品页");
        els.progress.textContent = "正在提交到共享工作台...";
        const result = await postOzonReferencePage(capture);
        els.progress.textContent = result.status === "waiting_ai_design"
            ? "Ozon参考页已采集，已进入AI商品卡生成"
            : "Ozon参考页已采集，正在打开工作台";
        setResult(result);
        await loadFactoryConfig();
        chrome.tabs.create({ url: workbenchEntryUrl("ozon", { task_id: result.task?.task_id }), active: true });
    }
    catch (error) {
        els.progress.textContent = "Ozon参考页采集失败";
        setResult(error.message);
    }
    finally {
        els.capture.disabled = false;
    }
}
els.capture.addEventListener("click", () => captureCurrentProduct(false));
els.previewToggle.addEventListener("click", () => {
    els.preview.hidden = !els.preview.hidden;
});
els.debugExport.addEventListener("click", exportSkuDebug);
els.openInbox.addEventListener("click", async () => {
    try {
        await loadFactoryConfig();
        const target = activePageKind === "ozon"
            ? workbenchEntryUrl("ozon")
            : workbenchEntryUrl("1688");
        chrome.tabs.create({ url: target });
    }
    catch (error) {
        els.connectionResult.textContent = `工作台地址无效：${error.message}`;
    }
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
        await chrome.storage.local.set({ factoryBaseUrl: baseUrl });
        factoryConfig = { baseUrl, deviceId: await ensureFactoryDeviceId() };
        els.connectionResult.textContent = "主电脑地址已保存，本电脑会自动识别";
        await testConnection();
    }
    catch (error) {
        els.connectionResult.textContent = error.message;
    }
}
async function testConnection() {
    els.connectionResult.textContent = "正在自动识别并连接主电脑...";
    try {
        const response = await factoryFetch("/api/workbench/summary");
        const result = await response.json().catch(() => ({}));
        if (!response.ok)
            throw new Error(result.detail?.message || result.detail || `HTTP ${response.status}`);
        els.connectionResult.textContent = "已连接共享工作台（无需访问码）";
    }
    catch (error) {
        els.connectionResult.textContent = `连接失败：${error.message}`;
    }
}
els.saveConnection.addEventListener("click", saveConnection);
els.testConnection.addEventListener("click", testConnection);
async function initialize() {
    await loadFactoryConfig();
    els.factoryUrl.value = factoryConfig.baseUrl;
    await loadPreview();
}
initialize().catch((error) => {
    els.status.textContent = "插件初始化失败";
    setResult(error.message);
});
