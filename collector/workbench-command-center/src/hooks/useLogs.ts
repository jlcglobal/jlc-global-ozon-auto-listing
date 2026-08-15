import { useEffect, useMemo, useState } from "react";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import { getErrorMessage, loadLogs } from "@/services/workbenchApi";
import type { LogsResponse } from "@/types/workbench";

export function useLogs(productId: string) {
  const [logs, setLogs] = useState<LogsResponse>({ items: [] });
  const [productLogs, setProductLogs] = useState<LogsResponse>({ items: [] });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function refreshGlobalLogs() {
    setLoading(true);
    setError("");
    try {
      const next = await loadLogs();
      setLogs(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    } finally {
      setLoading(false);
    }
  }

  async function refreshProductLogs(nextProductId = productId) {
    if (!nextProductId) {
      setProductLogs({ items: [] });
      return { items: [] };
    }
    setError("");
    try {
      const next = await loadLogs(nextProductId);
      setProductLogs(next);
      return next;
    } catch (err) {
      setError(getErrorMessage(err));
      throw err;
    }
  }

  useEffect(() => {
    let cancelled = false;
    refreshGlobalLogs().catch((err: Error) => {
      if (!cancelled) setError(getErrorMessage(err));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    refreshProductLogs(productId).catch((err: Error) => {
      if (!cancelled) setError(getErrorMessage(err));
    });
    return () => {
      cancelled = true;
    };
  }, [productId]);

  useEffect(() => {
    let cancelled = false;
    let busy = false;

    async function refreshSilently() {
      if (busy || (typeof document !== "undefined" && document.hidden)) return;
      busy = true;
      try {
        const [nextGlobal, nextProduct] = await Promise.all([
          loadLogs(),
          productId ? loadLogs(productId) : Promise.resolve({ items: [] }),
        ]);
        if (!cancelled) {
          setLogs(nextGlobal);
          setProductLogs(nextProduct);
        }
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        busy = false;
      }
    }

    const timer = window.setInterval(refreshSilently, commandCenterConfig.logsRefreshIntervalMs);
    const focusRefresh = () => refreshSilently();
    window.addEventListener("focus", focusRefresh);
    document.addEventListener("visibilitychange", focusRefresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", focusRefresh);
      document.removeEventListener("visibilitychange", focusRefresh);
    };
  }, [productId]);

  const activity = useMemo(() => {
    return productLogs.items.length ? productLogs.items : logs.items;
  }, [logs.items, productLogs.items]);

  return {
    logs,
    productLogs,
    activity,
    error,
    loading,
    refreshGlobalLogs,
    refreshProductLogs,
  };
}
