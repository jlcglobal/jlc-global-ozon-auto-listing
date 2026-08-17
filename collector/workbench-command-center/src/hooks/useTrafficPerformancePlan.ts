import { useEffect, useState } from "react";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import { getErrorMessage, loadTrafficPerformancePlan } from "@/services/workbenchApi";
import type { TrafficPerformancePlan } from "@/types/workbench";

export function useTrafficPerformancePlan() {
  const [plan, setPlan] = useState<TrafficPerformancePlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refreshTrafficPerformancePlan() {
    setError("");
    try {
      const next = await loadTrafficPerformancePlan();
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
    loadTrafficPerformancePlan()
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
        const next = await loadTrafficPerformancePlan();
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

  return { plan, loading, error, refreshTrafficPerformancePlan };
}
