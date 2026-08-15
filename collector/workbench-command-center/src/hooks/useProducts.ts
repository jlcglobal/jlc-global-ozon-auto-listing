import { useEffect, useMemo, useRef, useState } from "react";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import { getErrorMessage, loadProductDetail, loadProducts, loadRisks } from "@/services/workbenchApi";
import type { ProductDetail, ProductsResponse, RisksResponse } from "@/types/workbench";

const PRODUCT_CACHE_KEY = "jlc-global-command-center:products-cache:v1";
const SUBMITTED_READ_ONLY_STATUSES = new Set(["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED"]);
const ACTIVE_PRODUCTION_STATUSES = new Set(["PROCESSING", "RUNNING", "UPLOADING", "WAITING_FOR_AI_SERVICE"]);
const QUEUED_PRODUCTION_STATUSES = new Set(["QUEUED", "READY"]);

type ProductCache = {
  products: ProductsResponse | null;
  risks: RisksResponse | null;
  detail: ProductDetail | null;
  selectedProductId: string;
  savedAt: number;
};

function readProductCache(): ProductCache | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = window.localStorage.getItem(PRODUCT_CACHE_KEY);
    return raw ? JSON.parse(raw) as ProductCache : null;
  } catch {
    return null;
  }
}

function writeProductCache(cache: ProductCache) {
  try {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(PRODUCT_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // Cache is best-effort; local data loading remains the source of truth.
  }
}

function productStatus(value?: ProductDetail | null) {
  return String(value?.status?.status || value?.raw_status || "").toUpperCase();
}

function isSubmittedReadOnlyDetail(value?: ProductDetail | null) {
  return SUBMITTED_READ_ONLY_STATUSES.has(productStatus(value));
}

function shouldPollProductDetail(value?: ProductDetail | null) {
  const status = productStatus(value);
  return ACTIVE_PRODUCTION_STATUSES.has(status) || QUEUED_PRODUCTION_STATUSES.has(status);
}

function shouldPollProductCard(value?: ProductsResponse["items"][number]) {
  if (!value) return false;
  const status = String(value.raw_status || "").toUpperCase();
  return ACTIVE_PRODUCTION_STATUSES.has(status)
    || QUEUED_PRODUCTION_STATUSES.has(status)
    || isActiveProductionProduct(value);
}

function isInboxProduct(product: { raw_status?: string; workflow_bucket?: string; current_step?: string }) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "");
  return status === "COLLECTED" || bucket.includes("采集箱") || step === "collect_source";
}

function isActiveProductionProduct(product: { raw_status?: string; workflow_bucket?: string; current_step?: string; progress?: number }) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "").toLowerCase();
  if (QUEUED_PRODUCTION_STATUSES.has(status) || step === "queue") return false;
  return ACTIVE_PRODUCTION_STATUSES.has(status)
    || bucket.includes("生成中")
    || bucket.includes("生产中")
    || (step !== "queue" && Number(product.progress || 0) > 0 && Number(product.progress || 0) < 100);
}

function isQueuedProductionProduct(product: { raw_status?: string; current_step?: string }) {
  const status = String(product.raw_status || "").toUpperCase();
  const step = String(product.current_step || "").toLowerCase();
  return QUEUED_PRODUCTION_STATUSES.has(status) || step === "queue";
}

function isAttentionProduct(product: { raw_status?: string; workflow_bucket?: string }) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  return status === "NEEDS_ATTENTION" || bucket.includes("需要处理");
}

function preferredInitialProductId(products?: ProductsResponse | null, cachedProductId = "") {
  const items = products?.items || [];
  const liveProductId = items.find(isActiveProductionProduct)?.product_id || "";
  const queuedProductId = items.find(isQueuedProductionProduct)?.product_id || "";
  const inboxProductId = items.find(isInboxProduct)?.product_id || "";
  const attentionProductId = items.find(isAttentionProduct)?.product_id || "";
  const cachedProductStillExists = items.some((product) => product.product_id === cachedProductId);
  return liveProductId || queuedProductId || inboxProductId || (cachedProductStillExists ? cachedProductId : "") || attentionProductId || items[0]?.product_id || "";
}

export function useProducts(selectedProductId: string, onAutoSelect?: (productId: string) => void) {
  const [cacheSeed] = useState(() => readProductCache());
  const [products, setProducts] = useState<ProductsResponse | null>(cacheSeed?.products || null);
  const [risks, setRisks] = useState<RisksResponse | null>(cacheSeed?.risks || null);
  const [detail, setDetail] = useState<ProductDetail | null>(cacheSeed?.detail || null);
  const [error, setError] = useState("");
  const [loadingProducts, setLoadingProducts] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const selectedProductIdRef = useRef(selectedProductId);
  const latestDetailRequestRef = useRef("");
  const initialAutoSelectDoneRef = useRef(false);
  const liveProductsLoadedRef = useRef(false);

  useEffect(() => {
    selectedProductIdRef.current = selectedProductId;
  }, [selectedProductId]);

  function saveCache(
    nextProducts = products,
    nextRisks = risks,
    nextDetail = detail,
    nextSelectedProductId = selectedProductId,
  ) {
    writeProductCache({
      products: nextProducts,
      risks: nextRisks,
      detail: nextDetail,
      selectedProductId: nextSelectedProductId,
      savedAt: Date.now(),
    });
  }

  async function refreshProducts() {
    setLoadingProducts(true);
    setError("");
    try {
      const nextProducts = await loadProducts();
      liveProductsLoadedRef.current = true;
      setProducts(nextProducts);
      setLoadingProducts(false);
      const nextSelectedProductId = preferredInitialProductId(nextProducts, cacheSeed?.selectedProductId || "");
      if (!initialAutoSelectDoneRef.current && !selectedProductIdRef.current && nextSelectedProductId) {
        initialAutoSelectDoneRef.current = true;
        selectedProductIdRef.current = nextSelectedProductId;
        onAutoSelect?.(nextSelectedProductId);
      }
      saveCache(nextProducts, risks, detail, selectedProductIdRef.current || selectedProductId || cacheSeed?.selectedProductId || "");
      loadRisks()
        .then((nextRisks) => {
          setRisks(nextRisks);
          saveCache(nextProducts, nextRisks, detail, selectedProductIdRef.current || selectedProductId || cacheSeed?.selectedProductId || "");
        })
        .catch((err) => setError(getErrorMessage(err)));
      return { products: nextProducts, risks };
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setLoadingProducts(false);
    }
  }

  async function refreshProductDetail(productId = selectedProductId) {
    if (!productId) return null;
    latestDetailRequestRef.current = productId;
    setLoadingDetail(true);
    setError("");
    try {
      const next = await loadProductDetail(productId);
      if (latestDetailRequestRef.current === productId || selectedProductIdRef.current === productId) {
        setDetail(next);
      }
      saveCache(products, risks, next, productId);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setLoadingDetail(false);
    }
  }

  useEffect(() => {
    if (!liveProductsLoadedRef.current || initialAutoSelectDoneRef.current || selectedProductIdRef.current) return;
    const nextProductId = preferredInitialProductId(products, cacheSeed?.selectedProductId || "");
    if (!nextProductId) return;
    initialAutoSelectDoneRef.current = true;
    selectedProductIdRef.current = nextProductId;
    onAutoSelect?.(nextProductId);
  }, [cacheSeed?.selectedProductId, onAutoSelect, products?.items]);

  useEffect(() => {
    let cancelled = false;
    refreshProducts().catch((err: Error) => {
      if (!cancelled) setError(getErrorMessage(err));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!selectedProductId) return undefined;
    latestDetailRequestRef.current = selectedProductId;
    setDetail((current) => current?.product_id === selectedProductId ? current : null);
    setLoadingDetail(true);
    setError("");
    loadProductDetail(selectedProductId)
      .then((next) => {
        if (!cancelled && (latestDetailRequestRef.current === selectedProductId || selectedProductIdRef.current === selectedProductId)) {
          setDetail(next);
          saveCache(products, risks, next, selectedProductId);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProductId]);

  useEffect(() => {
    let cancelled = false;
    let productListBusy = false;
    let detailBusy = false;

    async function refreshProductsSilently() {
      if (productListBusy || (typeof document !== "undefined" && document.hidden)) return;
      productListBusy = true;
      try {
        const nextProducts = await loadProducts();
        liveProductsLoadedRef.current = true;
        if (cancelled) return;
        setProducts(nextProducts);
        if (!cancelled) saveCache(nextProducts, risks, detail, selectedProductIdRef.current || selectedProductId || "");
        loadRisks()
          .then((nextRisks) => {
            if (cancelled) return;
            setRisks(nextRisks);
            saveCache(nextProducts, nextRisks, detail, selectedProductIdRef.current || selectedProductId || "");
          })
          .catch((err) => {
            if (!cancelled) setError(getErrorMessage(err));
          });
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        productListBusy = false;
      }
    }

    async function refreshDetailSilently() {
      const nextSelectedProductId = selectedProductIdRef.current || selectedProductId;
      if (!nextSelectedProductId || detailBusy || (typeof document !== "undefined" && document.hidden)) return;
      if (detail?.product_id === nextSelectedProductId && isSubmittedReadOnlyDetail(detail)) return;
      const selectedCard = products?.items.find((product) => product.product_id === nextSelectedProductId);
      if (
        detail?.product_id === nextSelectedProductId
        && !shouldPollProductDetail(detail)
        && !shouldPollProductCard(selectedCard)
      ) return;
      latestDetailRequestRef.current = nextSelectedProductId;
      detailBusy = true;
      try {
        const nextDetail = await loadProductDetail(nextSelectedProductId);
        if (!cancelled && latestDetailRequestRef.current === nextSelectedProductId) {
          setDetail(nextDetail);
          saveCache(products, risks, nextDetail, nextSelectedProductId);
        }
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        detailBusy = false;
      }
    }

    const productListTimer = window.setInterval(refreshProductsSilently, commandCenterConfig.productRefreshIntervalMs);
    const detailTimer = window.setInterval(refreshDetailSilently, commandCenterConfig.productDetailRefreshIntervalMs);
    const focusRefresh = () => {
      refreshProductsSilently();
      refreshDetailSilently();
    };
    window.addEventListener("focus", focusRefresh);
    document.addEventListener("visibilitychange", focusRefresh);
    return () => {
      cancelled = true;
      window.clearInterval(productListTimer);
      window.clearInterval(detailTimer);
      window.removeEventListener("focus", focusRefresh);
      document.removeEventListener("visibilitychange", focusRefresh);
    };
  }, [selectedProductId, cacheSeed?.selectedProductId, products?.items, risks, detail?.product_id, detail?.raw_status, detail?.status?.status]);

  const currentProduct = useMemo(
    () => products?.items.find((product) => product.product_id === selectedProductId) || null,
    [products?.items, selectedProductId],
  );

  return {
    products,
    risks,
    detail,
    currentProduct,
    error,
    loadingProducts,
    loadingDetail,
    setDetail,
    refreshProducts,
    refreshProductDetail,
  };
}
