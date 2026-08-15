import { AlertTriangle, CheckCircle2, Clock3, PackageSearch } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatTime } from "@/lib/utils";
import type { BatchCard } from "@/types/workbench";

function batchStatusLabel(batch?: BatchCard | null) {
  if (!batch) return "未知";
  if (batch.display_status) return batch.display_status;
  if (batch.status === "RUNNING") return "运行中";
  if (batch.status === "QUEUED") return "排队中";
  if (batch.status === "COMPLETED" || batch.status === "COMPLETE") return "已完成";
  if (batch.status === "COMPLETED_WITH_ERRORS") return "完成但有错误";
  if (batch.status === "FAILED") return "失败";
  if (batch.status === "INCOMPLETE") return "未完成";
  if (batch.status === "AWAITING_CONFIRMATION") return "等待确认";
  if (batch.status === "AWAITING_MANUAL_UPLOAD") return "待确认上传";
  return batch.status || "未知";
}

function productTone(status?: string): "default" | "warning" | "danger" | "muted" {
  const value = String(status || "").toUpperCase();
  if (value.includes("FAILED") || value.includes("NEEDS_ATTENTION")) return "danger";
  if (value.includes("OZON") || value.includes("COMPLETE") || value.includes("HANDED")) return "default";
  if (value.includes("RUN") || value.includes("QUEUE")) return "warning";
  return "muted";
}

function firstIssue(product: NonNullable<BatchCard["products"]>[number]) {
  return product.errors?.[0] || product.warnings?.[0] || "";
}

export function BatchDetailDialog({
  open,
  onOpenChange,
  batch,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  batch?: BatchCard | null;
}) {
  const products = batch?.products || [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="batch-detail-dialog">
        <DialogHeader>
          <div className="panel-kicker">批次详情</div>
          <DialogTitle>生产批次详情</DialogTitle>
          <DialogDescription>只读查看当前批次的商品进度、店铺范围和失败原因。</DialogDescription>
        </DialogHeader>

        {batch ? (
          <>
            <div className="batch-detail-summary">
              <div>
                <span>批次</span>
                <strong>{batch.batch_id}</strong>
              </div>
              <div>
                <span>状态</span>
                <strong>{batchStatusLabel(batch)}</strong>
              </div>
              <div>
                <span>商品 / SKU</span>
                <strong>{batch.product_count || products.length || 0} / {batch.sku_count || 0}</strong>
              </div>
              <div>
                <span>进度</span>
                <strong>{batch.progress || 0}%</strong>
              </div>
            </div>

            <div className="batch-detail-meta">
              <Badge variant={batch.auto_upload ? "default" : "warning"}>{batch.auto_upload ? "自动上传" : "手动批次"}</Badge>
              <Badge variant={batch.inventory_submission_enabled ? "danger" : "muted"}>库存接口 {batch.inventory_submission_enabled ? "已启用" : "未启用"}</Badge>
              <span>创建：{formatTime(batch.created_at)}</span>
              <span>开始：{formatTime(batch.started_at)}</span>
              <span>完成：{formatTime(batch.completed_at)}</span>
            </div>

            <div className="batch-store-strip">
              {(batch.target_store_ids || []).map((storeId) => (
                <Badge key={storeId} variant="muted">{storeId}</Badge>
              ))}
              {!(batch.target_store_ids || []).length && <span>未显示目标店铺</span>}
            </div>

            <ScrollArea className="batch-detail-products">
              {products.length ? products.map((product) => {
                const issue = firstIssue(product);
                return (
                  <article key={product.product_id} className="batch-detail-product">
                    <div className="batch-product-icon">
                      {productTone(product.status) === "default" ? <CheckCircle2 className="h-4 w-4" /> : issue ? <AlertTriangle className="h-4 w-4" /> : <PackageSearch className="h-4 w-4" />}
                    </div>
                    <div>
                      <div className="batch-product-head">
                        <strong>{product.product_id}</strong>
                        <Badge variant={productTone(product.status)}>{batchStatusLabel({ status: product.status } as BatchCard)}</Badge>
                      </div>
                      <small>SKU {product.selected_sku_count || 0} · 当前步骤 {product.current_step || "未知"} · {formatTime(product.started_at)} - {formatTime(product.completed_at)}</small>
                      {issue && <p>{issue}</p>}
                    </div>
                  </article>
                );
              }) : (
                <div className="batch-detail-empty">
                  <Clock3 className="h-5 w-5" />
                  这个批次没有商品明细
                </div>
              )}
            </ScrollArea>
          </>
        ) : (
          <div className="batch-detail-empty">
            <PackageSearch className="h-5 w-5" />
            未选择批次
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
