import { AlertTriangle, Loader2, PackageCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { assetUrl } from "@/services/workbenchApi";
import type { BatchConfirmationProduct, BatchConfirmationResponse } from "@/types/workbench";

function dims(value?: { length?: number; width?: number; height?: number }, unit = "cm") {
  if (!value) return "--";
  const length = value.length ?? "--";
  const width = value.width ?? "--";
  const height = value.height ?? "--";
  return `${length} x ${width} x ${height} ${unit}`;
}

function sourceLabel(source?: string, confidence?: number) {
  const level = typeof confidence === "number" ? ` · ${confidence}%` : "";
  return `${source || "来源未知"}${level}`;
}

function priceText(value?: number | string | null) {
  if (value === null || value === undefined || value === "") return "未填";
  return `¥${value}`;
}

function productFieldRows(product: BatchConfirmationProduct) {
  const fields = product.fields || {};
  return [
    {
      label: "商品尺寸",
      value: dims(fields.product_dimensions?.value, fields.product_dimensions?.unit || "cm"),
      source: sourceLabel(fields.product_dimensions?.source, fields.product_dimensions?.confidence),
      estimated: fields.product_dimensions?.estimated,
    },
    {
      label: "商品净重",
      value: fields.product_weight_g?.value ? `${fields.product_weight_g.value} g` : "--",
      source: sourceLabel(fields.product_weight_g?.source, fields.product_weight_g?.confidence),
      estimated: fields.product_weight_g?.estimated,
    },
    {
      label: "包装尺寸",
      value: dims(fields.package_dimensions?.value, fields.package_dimensions?.unit || "cm"),
      source: sourceLabel(fields.package_dimensions?.source, fields.package_dimensions?.confidence),
      estimated: fields.package_dimensions?.estimated,
    },
    {
      label: "包装重量",
      value: fields.package_weight_g?.value ? `${fields.package_weight_g.value} g` : "--",
      source: sourceLabel(fields.package_weight_g?.source, fields.package_weight_g?.confidence),
      estimated: fields.package_weight_g?.estimated,
    },
    {
      label: "材质",
      value: fields.material?.value || "unknown",
      source: sourceLabel(fields.material?.source, fields.material?.confidence),
      estimated: fields.material?.estimated || fields.material?.needs_input,
    },
  ];
}

export function BatchConfirmationDialog({
  open,
  onOpenChange,
  data,
  loading,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data?: BatchConfirmationResponse | null;
  loading?: boolean;
  error?: string;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="batch-confirmation-dialog">
        <DialogHeader>
          <div className="panel-kicker">批次确认资料</div>
          <DialogTitle>批次确认资料</DialogTitle>
          <DialogDescription>
            这里只读展示历史人工确认批次的商品事实，不会确认批次，也不会提交 Ozon。
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="confirmation-loading">
            <Loader2 className="h-5 w-5 animate-spin" />
            正在读取批次资料
          </div>
        ) : error ? (
          <div className="confirmation-error">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </div>
        ) : data ? (
          <>
            <div className="confirmation-summary">
              <div>
                <span>批次</span>
                <strong>{data.batch_id}</strong>
              </div>
              <div>
                <span>商品</span>
                <strong>{data.product_count || 0}</strong>
              </div>
              <div>
                <span>SKU</span>
                <strong>{data.sku_count || 0}</strong>
              </div>
              <div>
                <span>待核对字段</span>
                <strong>{data.uncertain_count || 0}</strong>
              </div>
            </div>

            <ScrollArea className="confirmation-products">
              {(data.products || []).map((product) => (
                <article key={product.product_id} className="confirmation-product">
                  <div className="confirmation-product-head">
                    <div className="confirmation-thumb">
                      {product.thumbnail_url ? <img src={assetUrl(product.thumbnail_url)} alt="" /> : <PackageCheck className="h-5 w-5" />}
                    </div>
                    <div>
                      <strong>{product.title_cn || product.product_id}</strong>
                      <span>{product.product_id} · SKU {product.sku_count || 0}</span>
                      <small>{(product.category_path_zh || []).join(" / ") || "类目未显示"}</small>
                    </div>
                    <Badge variant={(product.uncertain_count || 0) ? "warning" : "default"}>
                      {product.uncertain_count || 0} 项需核对
                    </Badge>
                  </div>

                  <div className="confirmation-field-grid">
                    {productFieldRows(product).map((row) => (
                      <div key={row.label} className={row.estimated ? "estimated" : ""}>
                        <span>{row.label}</span>
                        <strong>{row.value}</strong>
                        <small>{row.source}</small>
                      </div>
                    ))}
                  </div>

                  <div className="confirmation-skus">
                    {(product.skus || []).slice(0, 8).map((sku) => (
                      <div key={sku.sku_id}>
                        <span>{sku.name || sku.option_text || sku.sku_id}</span>
                        <strong>{priceText(sku.purchase_price_cny)}</strong>
                      </div>
                    ))}
                    {(product.skus || []).length > 8 && <small>还有 {(product.skus || []).length - 8} 个 SKU</small>}
                  </div>
                </article>
              ))}
            </ScrollArea>
          </>
        ) : (
          <div className="confirmation-empty">没有可展示的批次确认资料</div>
        )}
      </DialogContent>
    </Dialog>
  );
}
