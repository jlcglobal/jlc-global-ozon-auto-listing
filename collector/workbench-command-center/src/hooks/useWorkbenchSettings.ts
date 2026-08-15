import { useEffect, useState } from "react";
import { getErrorMessage, loadWorkbenchSettings } from "@/services/workbenchApi";
import type { WorkbenchSettings } from "@/types/workbench";

export function useWorkbenchSettings() {
  const [settings, setSettings] = useState<WorkbenchSettings | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function refreshWorkbenchSettings() {
    setLoading(true);
    setError("");
    try {
      const next = await loadWorkbenchSettings();
      setSettings(next);
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
    refreshWorkbenchSettings().catch((err: Error) => {
      if (!cancelled) setError(getErrorMessage(err));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return { settings, error, loading, refreshWorkbenchSettings };
}
