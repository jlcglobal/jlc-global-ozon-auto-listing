const DEFAULT_FACTORY_URL = "http://127.0.0.1:8765";

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
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('工作台地址协议不支持');
  const host = url.hostname.toLowerCase();
  const privateIpv4 = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
  if (host !== '127.0.0.1' && host !== 'localhost' && !host.endsWith('.local') && !privateIpv4) {
    throw new Error('只允许访问主电脑的局域网地址');
  }
  return `${url.protocol}//${url.host}`;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'FACTORY_FETCH') return undefined;
  (async () => {
    try {
      const path = String(message.path || '');
      if (!path.startsWith('/api/collector/')) throw new Error('无效的工作台接口');
      const stored = await chrome.storage.local.get(['factoryBaseUrl']);
      const baseUrl = normalizeFactoryUrl(stored.factoryBaseUrl || DEFAULT_FACTORY_URL);
      const headers = { ...(message.options?.headers || {}) };
      headers['X-Factory-Device-Id'] = await ensureFactoryDeviceId();
      const response = await fetch(`${baseUrl}${path}`, {
        method: message.options?.method || 'GET',
        headers,
        body: message.options?.body
      });
      sendResponse({ ok: true, status: response.status, body: await response.text() });
    } catch (error) {
      sendResponse({ ok: false, status: 0, error: error?.message || '工作台连接失败' });
    }
  })();
  return true;
});
