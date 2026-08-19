import { commandCenterConfig } from "@/config/commandCenterConfig";
import type {
  FinanceImportPreview,
  FinanceImportResult,
  FinanceOrder,
  FinanceOverview,
  FinanceProduct,
  FinanceReconciliationItem,
  FinanceSyncStatus,
} from "@/types/finance";
import type {
  BatchesResponse,
  BatchConfirmationResponse,
  BatchControlResponse,
  CategoryCandidate,
  CategoryRulesResponse,
  CategorySearchResponse,
  CategoryUpdateResponse,
  CreateBatchResponse,
  DeletePreviewResponse,
  DeleteProductResponse,
  DraftSaveResponse,
  LogsResponse,
  CreateOzonReferenceTasksResponse,
  ImportOzonReferenceImagesResponse,
  OzonReferenceImportedImage,
  OzonReferenceManualInputs,
  OzonReferenceTasksResponse,
  OzonReferenceTaskInput,
  ProcessOzonReferenceTasksResponse,
  ProductDraftPayload,
  ProductDetail,
  ProductsResponse,
  QuestionAnswerResponse,
  RisksResponse,
  RunProductResponse,
  SearchVisibilityApplyResponse,
  SearchVisibilityBatchApplyResponse,
  SearchVisibilitySeerfarQueueResponse,
  SearchVisibilityUploadStatusResponse,
  SearchVisibilityYandexImportResponse,
  ShopMutationResponse,
  ShopPayload,
  ShopsResponse,
  SearchVisibilityPlan,
  StoreRetryResponse,
  StoreSelectionResponse,
  SuggestionActionResponse,
  SystemStatus,
  TrafficPerformancePlan,
  UpdateOzonReferenceTaskResponse,
  VisualPreferenceResponse,
  WorkbenchSettings,
} from "@/types/workbench";

const API_BASE = commandCenterConfig.apiBase;

export class WorkbenchApiError extends Error {
  status?: number;
  path: string;
  userMessage: string;

  constructor(path: string, message: string, status?: number) {
    super(message);
    this.name = "WorkbenchApiError";
    this.path = path;
    this.status = status;
    this.userMessage = status
      ? `${message || "工作台接口请求失败"}（${status}）`
      : `${message || "无法连接本地工作台"}：${path}`;
  }
}

export function getErrorMessage(error: unknown) {
  if (error instanceof WorkbenchApiError) return error.userMessage;
  if (error instanceof Error) return translateTechnicalError(error.message);
  return "未知前端错误";
}

function translateTechnicalError(message: string) {
  const text = String(message || "").trim();
  if (!text) return "未知前端错误";
  if (/timeout/i.test(text)) return "本地工作台读取超时，请确认后台服务仍在运行。";
  if (/network error|failed to fetch/i.test(text)) return "无法连接本地工作台，请确认主机服务和局域网连接正常。";
  if (/invalid json/i.test(text)) return "本地工作台返回的数据格式异常。";
  return text;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), commandCenterConfig.requestTimeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { Accept: "application/json", ...(init?.headers || {}) },
    });
  } catch (error) {
    const message = error instanceof DOMException && error.name === "AbortError"
      ? "本地工作台读取超时"
      : "本地工作台网络连接失败";
    throw new WorkbenchApiError(path, message);
  } finally {
    window.clearTimeout(timeout);
  }
  if (!response.ok) {
    let message = `工作台接口请求失败`;
    try {
      const data = await response.clone().json();
      message = String(data?.detail || data?.message || message);
    } catch {
      try {
        const text = await response.text();
        if (text.trim()) message = text.trim().slice(0, 240);
      } catch {
        // Keep the generic Chinese message.
      }
    }
    throw new WorkbenchApiError(path, translateTechnicalError(message), response.status);
  }
  try {
    return await response.json() as T;
  } catch {
    throw new WorkbenchApiError(path, "本地工作台返回的数据格式异常", response.status);
  }
}

function readJson<T>(path: string): Promise<T> {
  return requestJson<T>(path);
}

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

function patchJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

function deleteJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, { method: "DELETE" });
}

function deleteJsonWithBody<T>(path: string, body: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function assetUrl(path?: string) {
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE}${path}`;
}

export function loadSystemStatus() {
  return readJson<SystemStatus>("/api/workbench/system-status");
}

export function loadWorkbenchSettings() {
  return readJson<WorkbenchSettings>("/api/workbench/settings");
}

function financeQuery(filters: { storeId: string; dateFrom: string; dateTo: string; limit?: number; q?: string }) {
  const params = new URLSearchParams({
    store_id: filters.storeId || "all",
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
  });
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.q) params.set("q", filters.q);
  return params.toString();
}

export function loadFinanceOverview(filters: { storeId: string; dateFrom: string; dateTo: string }) {
  return readJson<FinanceOverview>(`/api/workbench/finance/overview?${financeQuery(filters)}`);
}

export function loadFinanceOrders(filters: { storeId: string; dateFrom: string; dateTo: string; limit?: number; q?: string }) {
  return readJson<{ items: FinanceOrder[]; total: number }>(`/api/workbench/finance/orders?${financeQuery(filters)}`);
}

export function loadFinanceProducts(filters: { storeId: string; dateFrom: string; dateTo: string; limit?: number; q?: string }) {
  return readJson<{ items: FinanceProduct[]; total: number }>(`/api/workbench/finance/products?${financeQuery(filters)}`);
}

export function loadFinanceReconciliation(storeId: string, limit = 200) {
  const params = new URLSearchParams({ store_id: storeId || "all", limit: String(limit) });
  return readJson<{ items: FinanceReconciliationItem[]; counts: Record<string, number>; notice: string }>(
    `/api/workbench/finance/reconciliation?${params}`,
  );
}

export function loadFinanceSyncStatus() {
  return readJson<FinanceSyncStatus>("/api/workbench/finance/sync-status");
}

export function startFinanceSync() {
  return postJson<{ status: string; message: string; ozon_write_api_calls?: number }>("/api/workbench/finance/sync/start", {});
}

export function previewFinanceImport(payload: { file_name: string; content_base64: string; file_kind: string }) {
  return postJson<FinanceImportPreview>("/api/workbench/finance/imports/preview", payload);
}

export function commitFinanceImport(payload: {
  file_name: string;
  content_base64: string;
  file_kind: string;
  mapping: Record<string, string>;
}) {
  return postJson<FinanceImportResult>("/api/workbench/finance/imports/commit", payload);
}

export function saveFinanceSkuPurchaseCost(payload: { sku: string; purchase_cost_cny: number }) {
  return postJson<{
    status: string;
    sku: string;
    purchase_cost_cny: string;
    affected_order_count: number;
    affected_store_count: number;
    ozon_write_api_calls: number;
  }>("/api/workbench/finance/purchase-costs/sku", payload);
}

export function loadProducts(pageSize = commandCenterConfig.productsPageSize) {
  return readJson<ProductsResponse>(`/api/workbench/products?page_size=${pageSize}`);
}

export function loadShops() {
  return readJson<ShopsResponse>("/api/workbench/shops");
}

export function saveShop(payload: ShopPayload, storeId?: string) {
  return storeId
    ? patchJson<ShopMutationResponse>(`/api/workbench/shops/${encodeURIComponent(storeId)}`, payload)
    : postJson<ShopMutationResponse>("/api/workbench/shops", payload);
}

export function validateShop(storeId: string) {
  return postJson<ShopMutationResponse>(`/api/workbench/shops/${encodeURIComponent(storeId)}/validate`);
}

export function setShopEnabled(storeId: string, enabled: boolean) {
  return postJson<ShopMutationResponse>(`/api/workbench/shops/${encodeURIComponent(storeId)}/enabled`, { enabled });
}

export function deleteShop(storeId: string) {
  return deleteJson<ShopMutationResponse>(`/api/workbench/shops/${encodeURIComponent(storeId)}`);
}

export function loadBatches() {
  return readJson<BatchesResponse>("/api/workbench/batches");
}

export function loadBatchConfirmation(batchId: string) {
  return readJson<BatchConfirmationResponse>(`/api/workbench/batches/${encodeURIComponent(batchId)}/confirmation`);
}

export function loadRisks() {
  return readJson<RisksResponse>("/api/workbench/risks");
}

export function loadLogs(productId?: string) {
  const query = productId ? `?product_id=${encodeURIComponent(productId)}` : "";
  return readJson<LogsResponse>(`/api/workbench/logs${query}`);
}

export function loadProductDetail(productId: string) {
  return readJson<ProductDetail>(`/api/workbench/products/${encodeURIComponent(productId)}`);
}

export function loadProductLogs(productId: string) {
  return loadLogs(productId);
}

export async function loadCockpitData(productId?: string) {
  const [system, products, logs, risks] = await Promise.all([
    loadSystemStatus(),
    loadProducts(),
    loadLogs(),
    loadRisks(),
  ]);
  const selected = productId || products.items[0]?.product_id;
  const [detail, productLogs] = selected
    ? await Promise.all([loadProductDetail(selected), loadProductLogs(selected)])
    : [null, { items: [] }];
  return { system, products, logs, risks, productLogs, detail };
}

export function runProduct(productId: string) {
  return postJson<RunProductResponse>(`/api/workbench/products/${encodeURIComponent(productId)}/run`);
}

export function refreshProductOzonStatus(productId: string) {
  return postJson<{ product_id: string; detail: ProductDetail; status?: string; import_status?: string; notice?: string }>(
    `/api/workbench/products/${encodeURIComponent(productId)}/refresh-ozon-status`,
  );
}

export function createWorkbenchBatch(productIds: string[], storeIds: string[]) {
  return postJson<CreateBatchResponse>("/api/workbench/batches/create", {
    product_ids: productIds,
    store_ids: storeIds,
  });
}

export function loadOzonReferenceTasks() {
  return readJson<OzonReferenceTasksResponse>("/api/workbench/ozon-reference-tasks");
}

export function loadSearchVisibilityPlan(storeId?: string) {
  const query = storeId ? `?store_id=${encodeURIComponent(storeId)}` : "";
  return readJson<SearchVisibilityPlan>(`/api/workbench/market-intelligence/search-visibility/latest${query}`);
}

export function syncSearchVisibilityPlan(payload: { store_id?: string; product_limit?: number; period_days?: number } = {}) {
  return postJson<SearchVisibilityPlan>("/api/workbench/market-intelligence/search-visibility/sync", payload);
}

export function applySearchVisibilityAction(payload: { store_id?: string; product_id: string }) {
  return postJson<SearchVisibilityApplyResponse>("/api/workbench/market-intelligence/search-visibility/apply", payload);
}

export function applySearchVisibilityBatch(payload: { store_id?: string; product_ids?: string[]; max_products?: number; confirm_upload?: boolean } = {}) {
  return postJson<SearchVisibilityBatchApplyResponse>("/api/workbench/market-intelligence/search-visibility/apply-batch", payload);
}

export function checkSearchVisibilityUploadStatus(payload: { store_id?: string; product_id: string }) {
  return postJson<SearchVisibilityUploadStatusResponse>(
    "/api/workbench/market-intelligence/search-visibility/upload-status",
    payload,
  );
}

export function importYandexWordstat(payload: { store_id?: string; product_id: string; text: string; period_days?: number }) {
  return postJson<SearchVisibilityYandexImportResponse>(
    "/api/workbench/market-intelligence/search-visibility/yandex-wordstat/import",
    payload,
  );
}

export function queueSeerfarKeywordMining(payload: { store_id?: string; product_id: string; seed_keyword?: string }) {
  return postJson<SearchVisibilitySeerfarQueueResponse>(
    "/api/workbench/market-intelligence/search-visibility/seerfar/queue",
    payload,
  );
}

export function importOzonProductQuery(payload: { store_id?: string; product_id: string; text: string; period_days?: number }) {
  return postJson<SearchVisibilityYandexImportResponse>(
    "/api/workbench/market-intelligence/search-visibility/ozon-product-query/import",
    payload,
  );
}

export function loadTrafficPerformancePlan() {
  return readJson<TrafficPerformancePlan>("/api/workbench/market-intelligence/traffic-performance/latest");
}

export interface KeywordGrowthReportResponse {
  available: boolean;
  excel_path: string;
  keyword_count: number;
  generated_at: string;
  notice: string;
}

export function generateKeywordGrowthReport() {
  return postJson<KeywordGrowthReportResponse>(
    "/api/workbench/market-intelligence/keyword-growth-report",
  );
}

export function keywordGrowthReportDownloadUrl() {
  return assetUrl("/api/workbench/market-intelligence/keyword-growth-report/latest");
}

export function createOzonReferenceTasks(items: OzonReferenceTaskInput[], storeIds: string[]) {
  return postJson<CreateOzonReferenceTasksResponse>("/api/workbench/ozon-reference-tasks", {
    items,
    store_ids: storeIds,
  });
}

export function updateOzonReferenceTaskManualInputs(taskId: string, manualInputs: OzonReferenceManualInputs, storeIds: string[]) {
  return requestJson<UpdateOzonReferenceTaskResponse>(
    `/api/workbench/ozon-reference-tasks/${encodeURIComponent(taskId)}/manual-inputs`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        ...manualInputs,
        store_ids: storeIds,
      }),
    },
  );
}

export function processOzonReferenceTasks() {
  return postJson<ProcessOzonReferenceTasksResponse>("/api/workbench/ozon-reference-tasks/process", {});
}

export function importOzonReferenceFitkunImages(taskId: string, images: OzonReferenceImportedImage[]) {
  return postJson<ImportOzonReferenceImagesResponse>(
    `/api/workbench/ozon-reference-tasks/${encodeURIComponent(taskId)}/fitkun-images`,
    { fitkun_images: images },
  );
}

export function stopRunningBatch() {
  return postJson<BatchControlResponse>("/api/workbench/batches/control", {
    action: "stop",
    source: "manual_toolbar_v2",
  });
}

export function regenerateProductImage(productId: string, slot: string, prompt?: string) {
  return postJson<{ queued: boolean; slot: string; message?: string }>(
    `/api/workbench/products/${encodeURIComponent(productId)}/images/${encodeURIComponent(slot)}/regenerate`,
    prompt ? { prompt } : {},
  );
}

export function saveVisualPreference(productId: string, payload: { set_hint?: string; slot_hints?: Record<string, string> }) {
  return requestJson<VisualPreferenceResponse>(`/api/workbench/products/${encodeURIComponent(productId)}/visual-preference`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updateProductImage(
  productId: string,
  slot: string,
  payload:
    | { action: "keep" | "accept" }
    | { action: "move"; direction: "up" | "down" }
    | { action: "set_role"; role: "main" | "detail" | "disclaimer" | "color_sample" },
) {
  return patchJson<{ saved: boolean; slot: string; action: string; message?: string }>(
    `/api/workbench/products/${encodeURIComponent(productId)}/images/${encodeURIComponent(slot)}`,
    payload,
  );
}

export function deleteProductImage(productId: string, slot: string) {
  return deleteJson<{ deleted: boolean; slot: string; message?: string }>(
    `/api/workbench/products/${encodeURIComponent(productId)}/images/${encodeURIComponent(slot)}`,
  );
}

export function replaceProductImage(productId: string, slot: string, dataUrl: string) {
  return requestJson<{ saved: boolean; slot: string; bytes: number; message?: string }>(
    `/api/workbench/products/${encodeURIComponent(productId)}/images/${encodeURIComponent(slot)}/content`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ data_url: dataUrl }),
    },
  );
}

export function bindSkuImage(productId: string, skuId: string, selectedImagePath: string) {
  return postJson<{ saved: boolean; message?: string }>(
    `/api/workbench/products/${encodeURIComponent(productId)}/sku-image-bindings`,
    { sku_id: skuId, selected_image_path: selectedImagePath },
  );
}

export function saveProductStores(productId: string, storeIds: string[]) {
  return requestJson<StoreSelectionResponse>(`/api/workbench/products/${encodeURIComponent(productId)}/stores`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ store_ids: storeIds, overrides: {} }),
  });
}

export function retryProductStore(productId: string, storeId: string) {
  return postJson<StoreRetryResponse>(
    `/api/workbench/products/${encodeURIComponent(productId)}/stores/${encodeURIComponent(storeId)}/retry`,
  );
}

export function retryFailedProductStores(productId: string, storeIds: string[]) {
  return postJson<StoreRetryResponse>(
    `/api/workbench/products/${encodeURIComponent(productId)}/stores/retry-failed`,
    { store_ids: storeIds },
  );
}

export function applySuggestion(productId: string, suggestionId: string, action: "accept" | "ignore" | "mute_similar") {
  return postJson<SuggestionActionResponse>(
    `/api/workbench/products/${encodeURIComponent(productId)}/suggestions/${encodeURIComponent(suggestionId)}`,
    { action },
  );
}

export function answerProductQuestion(productId: string, answer: string) {
  return postJson<QuestionAnswerResponse>(
    `/api/workbench/products/${encodeURIComponent(productId)}/question/answer`,
    { answer },
  );
}

export function saveProductDraft(productId: string, payload: ProductDraftPayload) {
  return patchJson<DraftSaveResponse>(`/api/workbench/products/${encodeURIComponent(productId)}/draft`, payload);
}

export function loadProductDeletePreview(productId: string) {
  return readJson<DeletePreviewResponse>(`/api/workbench/products/${encodeURIComponent(productId)}/delete-preview`);
}

export function deleteLocalProduct(productId: string) {
  return deleteJsonWithBody<DeleteProductResponse>(`/api/workbench/products/${encodeURIComponent(productId)}`, {
    permanent: true,
    confirm_product_id: productId,
  });
}

export function exportImagesUrl(productId: string) {
  return assetUrl(`/api/workbench/products/${encodeURIComponent(productId)}/export-images`);
}

export function searchCategories(query: string, limit = 30) {
  return readJson<CategorySearchResponse>(`/api/collector/categories?q=${encodeURIComponent(query)}&limit=${limit}`);
}

export function loadCategoryRules(categoryId: string | number, typeId: string | number, shopId?: string) {
  return postJson<CategoryRulesResponse>("/api/collector/categories/rules", {
    category_id: categoryId,
    type_id: typeId,
    shop_id: shopId,
    allow_readonly_fetch: true,
  });
}

export function updateProductCategory(productId: string, candidate: CategoryCandidate, rules: CategoryRulesResponse) {
  return requestJson<CategoryUpdateResponse>(`/api/collector/products/${encodeURIComponent(productId)}/category`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      category_id: candidate.category_id,
      type_id: candidate.type_id,
      category_path: candidate.path,
      category_name_zh: candidate.name_zh,
      category_path_zh: candidate.path_zh,
      selected_at: new Date().toISOString(),
      rules_snapshot: rules,
    }),
  });
}
