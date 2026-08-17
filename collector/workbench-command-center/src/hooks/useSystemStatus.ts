import { useEffect, useState } from "react";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import { getErrorMessage, loadSystemStatus } from "@/services/workbenchApi";
import type { SystemStatus } from "@/types/workbench";

export function useSystemStatus() {
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function refreshSystemStatus() {
    setLoading(true);
    setError("");
    try {
      const next = await loadSystemStatus();
      setSystem(next);
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
    refreshSystemStatus().catch((err: Error) => {
      if (!cancelled) setError(getErrorMessage(err));
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
        const next = await loadSystemStatus();
        if (!cancelled) setSystem(next);
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        busy = false;
      }
    }

    const timer = window.setInterval(refreshSilently, commandCenterConfig.systemRefreshIntervalMs);
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

  return { system, error, loading, refreshSystemStatus };
}
