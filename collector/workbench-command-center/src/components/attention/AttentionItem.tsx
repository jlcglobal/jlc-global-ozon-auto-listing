import { useState } from "react";
import { motion } from "framer-motion";
import { FileWarning, ImageOff, UploadCloud, Workflow } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, formatTime, truncate } from "@/lib/utils";
import {
  classifyRisk,
  inferAttentionStatus,
  isSubmittedReadOnly,
  kindLabel,
  riskSlot,
  severityLabel,
  workflowBadgeTone,
  workflowStatusLabel,
} from "@/lib/workbenchFormat";
import type { AttentionKind } from "@/lib/workbenchFormat";
import type { ProductCard, ProductDetail, RiskItem } from "@/types/workbench";

function kindIcon(kind: AttentionKind) {
  return {
    image: ImageOff,
    product: FileWarning,
    upload: UploadCloud,
    pipeline: Workflow,
  }[kind];
}

export function AttentionItem({
  item,
  index,
  product,
  currentDetail,
  onOpenProduct,
  onRegenerateImage,
}: {
  item: RiskItem;
  index: number;
  product?: ProductCard;
  currentDetail?: ProductDetail | null;
  onOpenProduct: (productId: string) => void;
  onRegenerateImage: (productId: string, slot: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const kind = classifyRisk(item);
  const Icon = kindIcon(kind);
  const slot = kind === "image" ? riskSlot(item) : "";
  const itemDetail = currentDetail?.product_id === item.product_id ? currentDetail : null;
  const workflowStatus = inferAttentionStatus(item, product, itemDetail);
  const submittedReadOnly = isSubmittedReadOnly(itemDetail, product);
  const canRegenerateImage = kind === "image" && slot && workflowStatus !== "Resolved" && !submittedReadOnly;

  return (
    <motion.article
      className={cn("attention-item", kind, workflowStatus.toLowerCase())}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.035 }}
    >
      <div className="attention-icon"><Icon className="h-4 w-4" /></div>
      <div className="attention-copy">
        <div>
          <strong>{truncate(item.title || product?.title_cn || item.product_id, 56)}</strong>
          <Badge variant={workflowBadgeTone(workflowStatus)}>{workflowStatusLabel(workflowStatus)}</Badge>
          <Badge variant={String(item.level || "").toLowerCase().includes("high") ? "danger" : "warning"}>
            {severityLabel(item.level)}
          </Badge>
        </div>
        <p className={expanded ? "expanded" : ""}>{item.message || "当前商品存在需要处理的异常"}</p>
        <small>{kindLabel(kind)} · {item.product_id} · {product?.sku_count ?? item.sku_count ?? "--"} SKU · {formatTime(item.occurred_at || item.at)}</small>
        {expanded && (
          <div className="attention-detail">
            <span>异常类型：{item.type || item.category || kindLabel(kind)}</span>
            <span>当前步骤：{item.step || product?.current_step || "未知"}</span>
            <span>处理建议：{kind === "image" && slot ? "重新生成对应图片" : "打开商品详情查看可编辑字段和生产时间线"}</span>
          </div>
        )}
        <div className="attention-actions">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "收起" : "展开原因"}
          </Button>
          {kind === "image" && slot ? (
            <Button size="sm" variant="secondary" onClick={() => onRegenerateImage(item.product_id, slot)} disabled={!canRegenerateImage}>
              {canRegenerateImage ? "重新生成" : "已解决"}
            </Button>
          ) : kind === "product" || kind === "upload" || kind === "pipeline" ? (
            <Button size="sm" variant="secondary" onClick={() => onOpenProduct(item.product_id)}>
              查看详情
            </Button>
          ) : (
            <span>暂无可用自动处理，打开详情查看原因</span>
          )}
        </div>
      </div>
    </motion.article>
  );
}
