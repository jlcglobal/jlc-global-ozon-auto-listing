const DEFAULT_FACTORY_URL = "http://192.168.3.13:8765";
const COMMAND_CENTER_QUERY_VERSION = "2026-08-01-ui-state-v1";
const LEGACY_LOCAL_FACTORY_URLS = new Set([
    "http://127.0.0.1:8765",
    "http://localhost:8765"
]);
const OZON_IMAGE_HOST_SUFFIXES = ["ozone.ru", "ozon.ru", "ozonusercontent.com"];
async function ensureFactoryDeviceId() {
    const stored = await chrome.storage.local.get(['factoryDeviceId']);
    let deviceId = String(stored.factoryDeviceId || '').trim();
    if (!deviceId) {
        deviceId = globalThis.crypto?.randomUUID?.() || `device-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
        await chrome.storage.local.set({ factoryDeviceId: deviceId });
    }
    return deviceId;
}
function normalizeFactoryUrl(value) {
    const url = new URL(String(value || DEFAULT_FACTORY_URL).trim());
    if (!['http:', 'https:'].includes(url.protocol))
        throw new Error('工作台地址协议不支持');
    const host = url.hostname.toLowerCase();
    const privateIpv4 = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
    if (host !== '127.0.0.1' && host !== 'localhost' && !host.endsWith('.local') && !privateIpv4) {
        throw new Error('只允许访问主电脑的局域网地址');
    }
    return `${url.protocol}//${url.host}`;
}
function cleanFactoryUrlText(value) {
    return String(value || "").trim().replace(/\/+$/, "");
}
function isLegacyLocalFactoryUrl(value) {
    return LEGACY_LOCAL_FACTORY_URLS.has(cleanFactoryUrlText(value));
}
function factoryUrlOrDefault(value) {
    const text = cleanFactoryUrlText(value);
    if (!text || isLegacyLocalFactoryUrl(text))
        return DEFAULT_FACTORY_URL;
    return text;
}
async function loadFactoryBaseUrl() {
    const stored = await chrome.storage.local.get(['factoryBaseUrl']);
    const baseUrl = normalizeFactoryUrl(factoryUrlOrDefault(stored.factoryBaseUrl));
    if (!cleanFactoryUrlText(stored.factoryBaseUrl) || isLegacyLocalFactoryUrl(stored.factoryBaseUrl)) {
        await chrome.storage.local.set({ factoryBaseUrl: baseUrl });
    }
    return baseUrl;
}
function commandCenterUrl(baseUrl, taskCenter, extra = {}) {
    const params = new URLSearchParams({ v: COMMAND_CENTER_QUERY_VERSION });
    if (extra.product_id)
        params.set("product_id", String(extra.product_id));
    if (extra.task_id)
        params.set("task_id", String(extra.task_id));
    const path = taskCenter === "reference"
        ? "/ozon-reference"
        : taskCenter === "inbox"
            ? "/1688-collection"
            : "/command-center";
    if (taskCenter && path === "/command-center")
        params.set("task_center", taskCenter);
    return `${baseUrl}${path}?${params.toString()}`;
}
function isAllowedOzonImageUrl(value) {
    try {
        const url = new URL(String(value || ""));
        const host = url.hostname.toLowerCase();
        return url.protocol === "https:" && OZON_IMAGE_HOST_SUFFIXES.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
    }
    catch {
        return false;
    }
}
function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
    }
    return btoa(binary);
}
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === 'FACTORY_FETCH_IMAGE_DATA_URL') {
        (async () => {
            try {
                const url = String(message.url || "");
                if (!isAllowedOzonImageUrl(url))
                    throw new Error("图片地址不属于 Ozon 图片域名");
                const response = await fetch(url, {
                    headers: {
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                    },
                    credentials: "omit",
                });
                if (!response.ok)
                    throw new Error(`图片读取失败 HTTP ${response.status}`);
                const contentType = String(response.headers.get("Content-Type") || "image/jpeg").split(";")[0].trim();
                if (!contentType.startsWith("image/"))
                    throw new Error("返回内容不是图片");
                const buffer = await response.arrayBuffer();
                if (buffer.byteLength > 8 * 1024 * 1024)
                    throw new Error("图片超过8MB");
                sendResponse({
                    ok: true,
                    url,
                    content_type: contentType,
                    byte_size: buffer.byteLength,
                    data_url: `data:${contentType};base64,${arrayBufferToBase64(buffer)}`,
                });
            }
            catch (error) {
                sendResponse({ ok: false, status: 0, error: error?.message || '图片读取失败' });
            }
        })();
        return true;
    }
    if (message?.type === 'FACTORY_OPEN_COMMAND_CENTER') {
        (async () => {
            try {
                const baseUrl = await loadFactoryBaseUrl();
                await chrome.tabs.create({
                    url: commandCenterUrl(baseUrl, message.task_center || "all", {
                        product_id: message.product_id,
                        task_id: message.task_id,
                    }),
                    active: true,
                });
                sendResponse({ ok: true });
            }
            catch (error) {
                sendResponse({ ok: false, status: 0, error: error?.message || '工作台打开失败' });
            }
        })();
        return true;
    }
    if (message?.type === 'FACTORY_FETCH_DETAIL_PAGE') {
        (async () => {
            try {
                const url = String(message.url || "");
                const parsed = new URL(url);
                if (parsed.protocol !== "https:" || !parsed.hostname.endsWith(".tmall.com")) {
                    throw new Error("详情页地址不在允许域名");
                }
                const response = await fetch(url, { credentials: "include" });
                if (!response.ok)
                    throw new Error(`详情页读取失败 HTTP ${response.status}`);
                sendResponse({ ok: true, status: response.status, body: await response.text() });
            }
            catch (error) {
                sendResponse({ ok: false, status: 0, error: error?.message || '详情页读取失败' });
            }
        })();
        return true;
    }
    if (message?.type === 'FACTORY_FETCH_1688_PAGE_SOURCE') {
        // The content script runs in an isolated world and recent 1688 pages do
        // not consistently expose tradeModel there.  Read the current offer's
        // HTML through the extension worker instead, under a deliberately narrow
        // allow-list.  This is a read-only recovery path, never a purchase action.
        (async () => {
            try {
                const url = String(message.url || "");
                const parsed = new URL(url);
                if (parsed.protocol !== "https:"
                    || parsed.hostname !== "detail.1688.com"
                    || !/^\/offer\/\d+\.html$/i.test(parsed.pathname)) {
                    throw new Error("1688商品页地址不在允许范围");
                }
                const response = await fetch(url, {
                    credentials: "include",
                    headers: { Accept: "text/html,application/xhtml+xml" }
                });
                if (!response.ok)
                    throw new Error(`1688商品页读取失败 HTTP ${response.status}`);
                const body = await response.text();
                if (!body || body.length > 2000000)
                    throw new Error("1688商品页源码为空或超出安全大小");
                sendResponse({ ok: true, status: response.status, body });
            }
            catch (error) {
                sendResponse({ ok: false, status: 0, error: error?.message || "1688商品页读取失败" });
            }
        })();
        return true;
    }
    if (message?.type !== 'FACTORY_FETCH')
        return undefined;
    (async () => {
        try {
            const path = String(message.path || '');
            const allowedWorkbenchPath = path.startsWith('/api/workbench/market-intelligence/search-visibility/seerfar/');
            if (!path.startsWith('/api/collector/') && !allowedWorkbenchPath)
                throw new Error('无效的工作台接口');
            const baseUrl = await loadFactoryBaseUrl();
            const headers = { ...(message.options?.headers || {}) };
            headers['X-Factory-Device-Id'] = await ensureFactoryDeviceId();
            const response = await fetch(`${baseUrl}${path}`, {
                method: message.options?.method || 'GET',
                headers,
                body: message.options?.body
            });
            sendResponse({ ok: true, status: response.status, body: await response.text() });
        }
        catch (error) {
            sendResponse({ ok: false, status: 0, error: error?.message || '工作台连接失败' });
        }
    })();
    return true;
});
