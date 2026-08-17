import { Boxes, ClipboardCheck, RadioTower } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import type { BatchCard } from "@/types/workbench";

const statusTone: Record<string, "default" | "warning" | "danger" | "muted"> = {
  RUNNING: "warning",
  QUEUED: "muted",
  COMPLETE: "default",
  FAILED: "danger",
  INCOMPLETE: "warning",
  AWAITING_CONFIRMATION: "warning",
};

function batchLabel(batch: BatchCard) {
  if (batch.display_status) return batch.display_status;
  if (batch.status === "RUNNING") return "运行中";
  if (batch.status === "QUEUED") return "排队中";
  if (batch.status === "COMPLETE") return "已完成";
  if (batch.status === "FAILED") return "失败";
  if (batch.status === "INCOMPLETE") return "未完成";
  return batch.status || "未知";
}

export function BatchStatusPanel({
  batches,
  runningPid,
  queuedCount,
  onOpenConfirmation,
  onOpenBatch,
}: {
  batches?: BatchCard[];
  runningPid?: number | null;
  queuedCount?: number;
  onOpenConfirmation?: (batchId: string) => void;
  onOpenBatch?: (batch: BatchCard) => void;
}) {
  const visible = (batches || []).slice(0, 4);
  return (
    <section className="batch-status-panel">
      <div className="batch-status-head">
        <div>
          <span className="panel-kicker">批次控制</span>
          <strong>生产任务</strong>
        </div>
        <Badge variant={runningPid ? "warning" : "muted"}>
          <RadioTower className="h-3 w-3" />
          {runningPid ? "运行中" : "空闲"}
        </Badge>
      </div>
      <div className="batch-status-meta">
        <span>队列 {queuedCount || 0}</span>
        <span>最近 {visible.length}</span>
      </div>
      <div className="batch-list">
        {visible.length ? visible.map((batch) => (
          <article key={batch.batch_id} className="batch-row">
            <div className="batch-row-main">
              <Boxes className="h-4 w-4 text-emerald-200/58" />
              <div>
                <strong>{batch.batch_id}</strong>
                <span>{batch.product_count || 0} 个商品 · {(batch.target_store_ids || []).length || 0} 家店铺</span>
              </div>
            </div>
            <Badge variant={statusTone[String(batch.status || "").toUpperCase()] || "muted"}>
              {batchLabel(batch)}
            </Badge>
            {onOpenBatch && (
              <button type="button" className="batch-detail-link" onClick={() => onOpenBatch(batch)}>
                查看批次
              </button>
            )}
            {batch.status === "AWAITING_CONFIRMATION" && onOpenConfirmation && (
              <button type="button" className="batch-confirmation-link" onClick={() => onOpenConfirmation(batch.batch_id)}>
                <ClipboardCheck className="h-3.5 w-3.5" />
                查看确认资料
              </button>
            )}
            <Progress value={batch.progress || 0} className={cn("h-1", (batch.progress || 0) > 0 && "opacity-90")} />
          </article>
        )) : (
          <div className="batch-empty">暂无批次记录</div>
        )}
      </div>
    </section>
  );
}
