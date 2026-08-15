import { Loader2, PackageCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { assetUrl } from "@/services/workbenchApi";
import { cn, truncate } from "@/lib/utils";
import type { ProductDetail } from "@/types/workbench";

function skuBindingLabel(sku: NonNullable<ProductDetail["skus"]>[number]) {
  if (sku.binding_required) return "需要绑定图片";
  if (sku.image_missing) return "缺少 SKU 图";
  if (sku.image_binding) return "已绑定参考图";
  if (sku.binding_status === "sku_owned_image") return "SKU 图已匹配";
  return sku.binding_status || "图片状态未知";
}

export function SkuImageBindingPanel({
  detail,
  busyKey,
  onBind,
}: {
  detail: ProductDetail | null;
  busyKey?: string;
  onBind: (skuId: string, selectedImagePath: string) => Promise<void>;
}) {
  const skus = detail?.skus || [];
  const candidates = detail?.sku_image_binding_candidates || [];
  return (
    <div className="sku-binding-panel">
      {skus.slice(0, 10).map((sku, index) => {
        const skuId = sku.sku_id || sku.offer_id || String(index);
        const selectedPath = sku.image_binding?.selected_image_path || "";
        const needsAction = Boolean(sku.binding_required || sku.image_missing || sku.image_binding);
        return (
          <article key={skuId} className={cn("sku-binding-row", needsAction && "needs-action")}>
            <div className="sku-binding-head">
              {sku.image_url ? <img src={assetUrl(sku.image_url)} alt="" /> : <PackageCheck className="h-5 w-5" />}
              <div>
                <strong>{truncate(sku.name || sku.title || skuId, 42)}</strong>
                <span>{skuId} · {skuBindingLabel(sku)}</span>
              </div>
            </div>
            {needsAction ? (
              <div className="sku-binding-candidates">
                {candidates.map((candidate) => {
                  const key = `${skuId}:${candidate.path || candidate.id || ""}`;
                  const selected = selectedPath && candidate.path === selectedPath;
                  return (
                    <Button
                      key={key}
                      type="button"
                      variant={selected ? "default" : "secondary"}
                      className={cn("sku-binding-choice", selected && "selected")}
                      disabled={!candidate.path || busyKey === key}
                      onClick={() => candidate.path && onBind(skuId, candidate.path)}
                    >
                      {candidate.url ? <img src={assetUrl(candidate.url)} alt="" /> : <PackageCheck className="h-5 w-5" />}
                      <span>{candidate.label || candidate.display_source || candidate.id || "候选图"}</span>
                      {busyKey === key && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                    </Button>
                  );
                })}
                {!candidates.length && <small>当前商品没有可绑定图片</small>}
              </div>
            ) : (
              <small className="sku-binding-ok">当前 SKU 图片可用，不需要手动绑定。</small>
            )}
          </article>
        );
      })}
      {!skus.length && <p className="drawer-empty">暂无 SKU 图绑定状态</p>}
    </div>
  );
}
