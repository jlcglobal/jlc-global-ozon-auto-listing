import { AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AttentionItem } from "@/components/attention/AttentionItem";
import type { ProductCard, ProductDetail, RiskItem } from "@/types/workbench";

export function AttentionPanel({
  risks,
  products,
  currentDetail,
  onOpenProduct,
  onRegenerateImage,
}: {
  risks: RiskItem[];
  products?: ProductCard[];
  currentDetail?: ProductDetail | null;
  onOpenProduct: (productId: string) => void;
  onRegenerateImage: (productId: string, slot: string) => void;
}) {
  const productById = new Map((products || []).map((product) => [product.product_id, product]));
  const fallback: RiskItem[] = currentDetail?.attention_required
    ? [{
        product_id: currentDetail.product_id,
        title: currentDetail.source?.title_cn,
        level: currentDetail.risk?.level || "review",
        message: currentDetail.error?.message || currentDetail.error?.title || "需要处理当前商品状态",
        step: currentDetail.current_step || currentDetail.status?.current_step,
      }]
    : [];
  const items = risks.length ? risks : fallback;

  return (
    <Card className="attention-panel glass-panel">
      <CardHeader>
        <div className="panel-kicker">异常处理</div>
        <CardTitle>需要处理</CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[198px]">
          <div className="attention-list">
            {items.length ? items.slice(0, 8).map((item, index) => (
              <AttentionItem
                key={`${item.product_id}-${item.title}-${index}`}
                item={item}
                index={index}
                product={productById.get(item.product_id)}
                currentDetail={currentDetail}
                onOpenProduct={onOpenProduct}
                onRegenerateImage={onRegenerateImage}
              />
            )) : (
              <div className="attention-empty">
                <AlertTriangle className="h-5 w-5" />
                <span>暂无需要处理的异常</span>
              </div>
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
