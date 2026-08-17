import { useEffect, useState } from "react";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import {
  createOzonReferenceTasks,
  createWorkbenchBatch,
  getErrorMessage,
  loadBatches,
  loadOzonReferenceTasks,
  loadShops,
  processOzonReferenceTasks,
  updateOzonReferenceTaskManualInputs,
} from "@/services/workbenchApi";
import type {
  BatchesResponse,
  CreateBatchResponse,
  CreateOzonReferenceTasksResponse,
  OzonReferenceManualInputs,
  OzonReferenceTasksResponse,
  OzonReferenceTaskInput,
  ShopsResponse,
  UpdateOzonReferenceTaskResponse,
} from "@/types/workbench";

export function useBatches() {
  const [batches, setBatches] = useState<BatchesResponse | null>(null);
  const [shops, setShops] = useState<ShopsResponse | null>(null);
  const [ozonReferenceTasks, setOzonReferenceTasks] = useState<OzonReferenceTasksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refreshBatches() {
    setError("");
    try {
      const next = await loadBatches();
      setBatches(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    }
  }

  async function refreshShops() {
    setError("");
    try {
      const next = await loadShops();
      setShops(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    }
  }

  async function refreshOzonReferenceTasks() {
    setError("");
    try {
      const next = await loadOzonReferenceTasks();
      setOzonReferenceTasks(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    }
  }

  async function refreshBatchInputs() {
    setLoading(true);
    setError("");
    try {
      const [nextBatches, nextShops, nextOzonReferenceTasks] = await Promise.all([
        loadBatches(),
        loadShops(),
        loadOzonReferenceTasks(),
      ]);
      setBatches(nextBatches);
      setShops(nextShops);
      setOzonReferenceTasks(nextOzonReferenceTasks);
      return { batches: nextBatches, shops: nextShops, ozonReferenceTasks: nextOzonReferenceTasks };
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setLoading(false);
    }
  }

  async function createBatch(productIds: string[], storeIds: string[]): Promise<CreateBatchResponse> {
    setError("");
    const result = await createWorkbenchBatch(productIds, storeIds);
    await refreshBatches().catch(() => null);
    return result;
  }

  async function createOzonReferenceBatch(items: OzonReferenceTaskInput[], storeIds: string[]): Promise<CreateOzonReferenceTasksResponse> {
    setError("");
    const result = await createOzonReferenceTasks(items, storeIds);
    if (result.created_count > 0) {
      await processOzonReferenceTasks().catch(() => null);
    }
    const nextTasks = await loadOzonReferenceTasks().catch(() => null);
    if (nextTasks) setOzonReferenceTasks(nextTasks);
    return result;
  }

  async function updateOzonReferenceTask(taskId: string, manualInputs: OzonReferenceManualInputs, storeIds: string[]): Promise<UpdateOzonReferenceTaskResponse> {
    setError("");
    const result = await updateOzonReferenceTaskManualInputs(taskId, manualInputs, storeIds);
    const nextTasks = await loadOzonReferenceTasks().catch(() => null);
    if (nextTasks) setOzonReferenceTasks(nextTasks);
    return result;
  }

  async function continueOzonReferenceQueue() {
    setError("");
    const result = await processOzonReferenceTasks();
    await refreshOzonReferenceTasks().catch(() => null);
    return result;
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    let pending = 3;
    const finish = () => {
      pending -= 1;
      if (!cancelled && pending <= 0) setLoading(false);
    };
    loadBatches()
      .then((nextBatches) => {
        if (!cancelled) setBatches(nextBatches);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(finish);
    loadShops()
      .then((nextShops) => {
        if (!cancelled) setShops(nextShops);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(finish);
    loadOzonReferenceTasks()
      .then((nextOzonReferenceTasks) => {
        if (!cancelled) setOzonReferenceTasks(nextOzonReferenceTasks);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(finish);
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
        const [nextBatches, nextShops, nextOzonReferenceTasks] = await Promise.all([
          loadBatches(),
          loadShops(),
          loadOzonReferenceTasks(),
        ]);
        if (!cancelled) {
          setBatches(nextBatches);
          setShops(nextShops);
          setOzonReferenceTasks(nextOzonReferenceTasks);
        }
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

  return {
    batches,
    shops,
    ozonReferenceTasks,
    loading,
    error,
    refreshBatches,
    refreshShops,
    refreshOzonReferenceTasks,
    refreshBatchInputs,
    createBatch,
    createOzonReferenceBatch,
    updateOzonReferenceTask,
    continueOzonReferenceQueue,
  };
}
