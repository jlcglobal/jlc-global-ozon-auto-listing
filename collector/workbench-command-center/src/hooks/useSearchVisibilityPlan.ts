import { useEffect, useRef, useState } from "react";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import {
  applySearchVisibilityBatch as applySearchVisibilityBatchRequest,
  applySearchVisibilityAction as applySearchVisibilityActionRequest,
  checkSearchVisibilityUploadStatus as checkSearchVisibilityUploadStatusRequest,
  getErrorMessage,
  importOzonProductQuery as importOzonProductQueryRequest,
  importYandexWordstat as importYandexWordstatRequest,
  queueSeerfarKeywordMining as queueSeerfarKeywordMiningRequest,
  loadSearchVisibilityPlan,
  syncSearchVisibilityPlan as syncSearchVisibilityPlanRequest,
} from "@/services/workbenchApi";
import type { SearchVisibilityPlan } from "@/types/workbench";

export function useSearchVisibilityPlan() {
  const [plan, setPlan] = useState<SearchVisibilityPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [importingYandex, setImportingYandex] = useState(false);
  const [queueingSeerfar, setQueueingSeerfar] = useState(false);
  const [importingOzonProductQuery, setImportingOzonProductQuery] = useState(false);
  const [error, setError] = useState("");
  const activeStoreIdRef = useRef("");

  async function refreshSearchVisibilityPlan(storeId?: string) {
    if (storeId !== undefined) activeStoreIdRef.current = storeId;
    setError("");
    try {
      const next = await loadSearchVisibilityPlan(storeId ?? (activeStoreIdRef.current || undefined));
      setPlan(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadSearchVisibilityPlan(activeStoreIdRef.current || undefined)
      .then((next) => {
        if (!cancelled) setPlan(next);
      })
      .catch((err) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let busy = false;

    async function refreshSilently() {
      if (busy || (typeof document !== "undefined" && document.hidden)) return;
      busy = true;
      try {
        const next = await loadSearchVisibilityPlan(activeStoreIdRef.current || undefined);
        if (!cancelled) setPlan(next);
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        busy = false;
      }
    }

    const timer = window.setInterval(refreshSilently, commandCenterConfig.batchRefreshIntervalMs);
    const focusRefresh = () => refreshSilently();
    window.addEventListener("focus", focusRefresh);
    document.addEventListener("visibilitychange", focusRefresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", focusRefresh);
      document.removeEventListener("visibilitychange", focusRefresh);
    };
  }, []);

  async function syncSearchVisibilityPlan(payload: { store_id?: string; product_limit?: number; period_days?: number } = {}) {
    setError("");
    setSyncing(true);
    try {
      const next = await syncSearchVisibilityPlanRequest(payload);
      activeStoreIdRef.current = String(payload.store_id || next.shop_id || "");
      setPlan(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setSyncing(false);
      setLoading(false);
    }
  }

  async function applySearchVisibilityAction(payload: { store_id?: string; product_id: string }) {
    setError("");
    setApplying(true);
    try {
      const result = await applySearchVisibilityActionRequest(payload);
      await refreshSearchVisibilityPlan().catch(() => null);
      return result;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setApplying(false);
      setLoading(false);
    }
  }

  async function applySearchVisibilityBatch(payload: { store_id?: string; product_ids?: string[]; max_products?: number; confirm_upload?: boolean } = {}) {
    setError("");
    setApplying(true);
    try {
      const result = await applySearchVisibilityBatchRequest(payload);
      await refreshSearchVisibilityPlan().catch(() => null);
      return result;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setApplying(false);
      setLoading(false);
    }
  }

  async function checkSearchVisibilityUploadStatus(payload: { store_id?: string; product_id: string }) {
    setError("");
    setApplying(true);
    try {
      const result = await checkSearchVisibilityUploadStatusRequest(payload);
      await refreshSearchVisibilityPlan().catch(() => null);
      return result;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setApplying(false);
      setLoading(false);
    }
  }

  async function importYandexWordstat(payload: { store_id?: string; product_id: string; text: string; period_days?: number }) {
    setError("");
    setImportingYandex(true);
    try {
      const next = await importYandexWordstatRequest(payload);
      setPlan(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setImportingYandex(false);
      setLoading(false);
    }
  }

  async function queueSeerfarKeywordMining(payload: { store_id?: string; product_id: string; seed_keyword?: string }) {
    setError("");
    setQueueingSeerfar(true);
    try {
      return await queueSeerfarKeywordMiningRequest(payload);
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setQueueingSeerfar(false);
    }
  }

  async function importOzonProductQuery(payload: { store_id?: string; product_id: string; text: string; period_days?: number }) {
    setError("");
    setImportingOzonProductQuery(true);
    try {
      const next = await importOzonProductQueryRequest(payload);
      setPlan(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setImportingOzonProductQuery(false);
      setLoading(false);
    }
  }

  return {
    plan,
    loading,
    syncing,
    applying,
    importingYandex,
    queueingSeerfar,
    importingOzonProductQuery,
    error,
    refreshSearchVisibilityPlan,
    syncSearchVisibilityPlan,
    applySearchVisibilityAction,
    applySearchVisibilityBatch,
    checkSearchVisibilityUploadStatus,
    importYandexWordstat,
    queueSeerfarKeywordMining,
    importOzonProductQuery,
  };
}
