import { useState } from "react";
import { ArrowDown, ArrowUp, Download, Image, PackageCheck, RefreshCcw, Search, ShieldCheck, Tag, Trash2, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { assetUrl } from "@/services/workbenchApi";
import type { ProductDetail } from "@/types/workbench";

function imageLabel(value?: string) {
  const normalized = String(value || "unknown").toUpperCase();
  return ({
    GENERATING: "生成中",
    RETRYING: "重试中",
    WAITING: "等待",
    PASS: "已通过",
    FAIL: "未通过",
    QC: "检查中",
  } as Record<string, string>)[normalized] || value || "未知";
}

function assetGroups(detail: ProductDetail | null) {
  const originals = [
    ...(detail?.image_assets?.original || []),
    ...(detail?.image_assets?.accepted || []),
  ].slice(0, 8);
  const generated = detail?.images || [];
  return { originals, generated };
}

type GalleryItem = {
  kind: "original" | "generated";
  key: string;
  label: string;
  url?: string;
  download_url?: string;
  slot?: string;
  status?: string;
  purpose?: string;
  score?: number;
  russian_text?: string[];
  retry_count?: number;
};

function galleryItems(detail: ProductDetail | null): GalleryItem[] {
  const groups = assetGroups(detail);
  return [
    ...groups.originals.map((item, index) => ({
      kind: "original" as const,
      key: `${item.url || item.path}-${index}`,
      label: `原图 ${index + 1}`,
      url: item.url,
      status: item.state || item.type || "source",
    })),
    ...groups.generated.slice(0, 12).map((item) => ({
      kind: "generated" as const,
      key: item.slot,
      label: item.slot,
      url: item.url,
      download_url: item.download_url,
      slot: item.slot,
      status: item.state || item.status || item.type || item.slot,
      purpose: item.purpose,
      score: item.score,
      russian_text: item.russian_text,
      retry_count: item.retry_count,
    })),
  ];
}

export function ProductImageGallery({
  detail,
  actionBusy,
  onRegenerateSlot,
  onImageAction,
  onReplaceImage,
}: {
  detail: ProductDetail | null;
  actionBusy?: boolean;
  onRegenerateSlot: (slot: string, prompt?: string) => Promise<void> | void;
  onReplaceImage: (slot: string, dataUrl: string) => Promise<void> | void;
  onImageAction: (
    slot: string,
    payload:
      | { action: "keep" | "accept" }
      | { action: "move"; direction: "up" | "down" }
      | { action: "set_role"; role: "main" | "detail" | "disclaimer" | "color_sample" }
      | { action: "delete" },
  ) => Promise<void> | void;
}) {
  const groups = assetGroups(detail);
  const items = galleryItems(detail);
  const [preview, setPreview] = useState<GalleryItem | null>(null);
  const [slotPrompt, setSlotPrompt] = useState("");
  const [replaceBusy, setReplaceBusy] = useState(false);

  function openPreview(item: GalleryItem) {
    setPreview(item);
    setSlotPrompt((item.slot && detail?.visual_preference?.slot_hints?.[item.slot]) || "");
  }

  function readFileAsDataUrl(file: File) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("图片文件读取失败"));
      reader.onload = () => resolve(String(reader.result || ""));
      reader.readAsDataURL(file);
    });
  }

  async function replaceImage(slot: string, file?: File) {
    if (!file) return;
    setReplaceBusy(true);
    try {
      const dataUrl = await readFileAsDataUrl(file);
      await onReplaceImage(slot, dataUrl);
    } finally {
      setReplaceBusy(false);
    }
  }

  return (
    <>
      <div className="image-gallery-summary">
        <Badge variant="muted">原图 {groups.originals.length}</Badge>
        <Badge variant="default">生成图 {groups.generated.length}</Badge>
        <span>点击任意图片可放大查看；生成图可单张重生成。</span>
      </div>
      <div className="drawer-image-grid">
        {items.map((item) => (
          <article key={item.key} className={item.kind}>
            <button type="button" className="image-preview-button" onClick={() => openPreview(item)}>
              {item.url ? <img src={assetUrl(item.url)} alt="" /> : item.kind === "original" ? <PackageCheck className="h-6 w-6" /> : <Image className="h-6 w-6" />}
              <span className="image-hover">
                <Search className="h-4 w-4" />
              </span>
            </button>
            <div>
              <strong>{item.label}</strong>
              <span>{imageLabel(item.status)}</span>
              {item.retry_count ? <span>已重试 {item.retry_count} 次</span> : null}
            </div>
            {item.kind === "generated" && item.slot && (
              <Button size="sm" variant="secondary" onClick={() => onRegenerateSlot(item.slot!)} disabled={actionBusy}>
                <RefreshCcw className="h-3.5 w-3.5" />
                重生成
              </Button>
            )}
          </article>
        ))}
        {!items.length && <p className="drawer-empty">暂无图片资产</p>}
      </div>
      <Dialog open={Boolean(preview)} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent className="image-preview-dialog">
          <DialogHeader>
            <DialogTitle>{preview?.label || "图片预览"}</DialogTitle>
            <DialogDescription>{preview?.kind === "generated" ? "生成图片槽位预览" : "采集原图预览"}</DialogDescription>
          </DialogHeader>
          <div className="image-preview-stage">
            {preview?.url ? <img src={assetUrl(preview.url)} alt="" /> : <Image className="h-10 w-10" />}
          </div>
          <div className="image-preview-meta">
            <Badge variant={preview?.kind === "generated" ? "default" : "muted"}>{preview?.status || "unknown"}</Badge>
            {typeof preview?.score === "number" && <Badge variant="muted">QC {preview.score}</Badge>}
            {preview?.purpose && <p>{preview.purpose}</p>}
            {preview?.russian_text?.length ? <p>{preview.russian_text.join(" · ")}</p> : null}
          </div>
          <div className="image-preview-actions">
            {preview?.download_url && (
              <Button variant="secondary" asChild>
                <a href={assetUrl(preview.download_url)} download>
                  <Download className="h-4 w-4" />
                  下载
                </a>
              </Button>
            )}
            {preview?.kind === "generated" && preview.slot && (
              <Button onClick={() => onRegenerateSlot(preview.slot!)} disabled={actionBusy}>
                <RefreshCcw className="h-4 w-4" />
                重新生成此图
              </Button>
            )}
          </div>
          {preview?.kind === "generated" && preview.slot && (
            <div className="image-management-panel">
              <div className="image-management-heading">
                <strong>本地图片操作</strong>
                <span>只修改当前商品本地图片计划，不提交 Ozon。</span>
              </div>
              <div className="image-prompt-editor">
                <label>
                  <span>本张图修改意见</span>
                  <textarea
                    value={slotPrompt}
                    onChange={(event) => setSlotPrompt(event.target.value)}
                    maxLength={200}
                    placeholder="例如：用近景特写展示材质，产品角度换成侧面，减少文字"
                  />
                </label>
                <Button size="sm" onClick={() => onRegenerateSlot(preview.slot!, slotPrompt.trim())} disabled={actionBusy}>
                  <RefreshCcw className="h-3.5 w-3.5" />
                  应用意见并重生成
                </Button>
              </div>
              <div className="image-management-actions">
                <Button size="sm" variant="secondary" onClick={() => onImageAction(preview.slot!, { action: "accept" })} disabled={actionBusy}>
                  <ShieldCheck className="h-3.5 w-3.5" />
                  保留
                </Button>
                <Button size="sm" variant="ghost" onClick={() => onImageAction(preview.slot!, { action: "move", direction: "up" })} disabled={actionBusy}>
                  <ArrowUp className="h-3.5 w-3.5" />
                  前移
                </Button>
                <Button size="sm" variant="ghost" onClick={() => onImageAction(preview.slot!, { action: "move", direction: "down" })} disabled={actionBusy}>
                  <ArrowDown className="h-3.5 w-3.5" />
                  后移
                </Button>
                <Button size="sm" variant="ghost" onClick={() => onImageAction(preview.slot!, { action: "set_role", role: "main" })} disabled={actionBusy}>
                  <Tag className="h-3.5 w-3.5" />
                  主图
                </Button>
                <Button size="sm" variant="ghost" onClick={() => onImageAction(preview.slot!, { action: "set_role", role: "detail" })} disabled={actionBusy}>
                  <Tag className="h-3.5 w-3.5" />
                  详情
                </Button>
                <label className="image-replace-button">
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    disabled={actionBusy || replaceBusy}
                    onChange={(event) => {
                      const file = event.currentTarget.files?.[0];
                      event.currentTarget.value = "";
                      void replaceImage(preview.slot!, file);
                    }}
                  />
                  <Upload className="h-3.5 w-3.5" />
                  替换图片
                </label>
                <Button size="sm" variant="danger" onClick={() => onImageAction(preview.slot!, { action: "delete" })} disabled={actionBusy}>
                  <Trash2 className="h-3.5 w-3.5" />
                  拒绝
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
