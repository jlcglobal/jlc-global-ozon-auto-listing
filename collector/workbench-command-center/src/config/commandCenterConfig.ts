export const commandCenterConfig = {
  buildVersion: "2026-08-01-ui-state-v1",
  apiBase: (import.meta.env.VITE_WORKBENCH_API_BASE || "").replace(/\/$/, ""),
  productsPageSize: Number(import.meta.env.VITE_WORKBENCH_PRODUCTS_PAGE_SIZE || 100),
  requestTimeoutMs: Number(import.meta.env.VITE_WORKBENCH_REQUEST_TIMEOUT_MS || 60000),
  productRefreshIntervalMs: Number(import.meta.env.VITE_WORKBENCH_PRODUCT_REFRESH_INTERVAL_MS || 15000),
  productDetailRefreshIntervalMs: Number(import.meta.env.VITE_WORKBENCH_PRODUCT_DETAIL_REFRESH_INTERVAL_MS || 15000),
  logsRefreshIntervalMs: Number(import.meta.env.VITE_WORKBENCH_LOGS_REFRESH_INTERVAL_MS || 15000),
  systemRefreshIntervalMs: Number(import.meta.env.VITE_WORKBENCH_SYSTEM_REFRESH_INTERVAL_MS || 15000),
  batchRefreshIntervalMs: Number(import.meta.env.VITE_WORKBENCH_BATCH_REFRESH_INTERVAL_MS || 15000),
};
