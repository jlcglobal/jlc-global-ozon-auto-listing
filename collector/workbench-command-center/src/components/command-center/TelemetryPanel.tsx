import { Activity, Bot, CheckCircle2, Clock3, PackageCheck, ShieldCheck, UploadCloud } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { truncate } from "@/lib/utils";
import { readableStageName, statusLabel, statusTone } from "@/lib/workbenchFormat";
import type { ProductCard, ProductDetail, ProductsResponse, SearchVisibilityAction, SystemStatus, WorkbenchSettings } from "@/types/workbench";

function StatusLine({ icon: Icon, label, value, ok = true }: { icon: typeof ShieldCheck; label: string; value: string; ok?: boolean }) {
  return (
    <div className="simple-status-line">
      <Icon className="h-4 w-4" />
      <span>{label}</span>
      <strong>{value}</strong>
      <i className={ok ? "ok" : "warn"} />
    </div>
  );
}

function currentBadge(status?: string): "default" | "warning" | "danger" | "muted" {
  const tone = statusTone(status);
  if (tone === "danger") return "danger";
  if (tone === "running") return "warning";
  if (tone === "ok") return "default";
  return "muted";
}

export function TelemetryPanel({
  system,
  products,
  settings,
  currentDetail,
  currentProduct,
  mode = "local",
  searchAction,
}: {
  system?: SystemStatus | null;
  products?: ProductsResponse | null;
  settings?: WorkbenchSettings | null;
  currentDetail?: ProductDetail | null;
  currentProduct?: ProductCard;
  mode?: "local" | "ozon";
  searchAction?: SearchVisibilityAction | null;
}) {
  const ozonMode = mode === "ozon";
  const activeTitle = ozonMode
    ? searchAction?.current_title || searchAction?.offer_ids?.[0] || searchAction?.product_id || "未选择 Ozon 商品"
    : currentDetail?.source?.title_cn || currentProduct?.title_cn || currentProduct?.title_ru || "未选择商品";
  const activeStatus = ozonMode
    ? searchAction?.last_upload?.status === "submitted" ? "UPLOADED" : searchAction ? "PROCESSING" : ""
    : currentDetail?.status?.status || currentProduct?.raw_status || "";
  const activeStep = ozonMode ? "Ozon 商品信息" : currentDetail?.status?.current_step || currentProduct?.current_step || "";
  const pendingCount = products?.items?.filter((item) => item.attention_required || item.risk?.level === "high").length || 0;
  const ozonImages = searchAction?.images?.length || (searchAction?.image_url ? 1 : 0);
  const ozonSkuCount = searchAction?.sku || searchAction?.offer_ids?.length ? 1 : 0;
  const ozonTagCount = (searchAction?.existing_subject_tags?.length || 0) + (searchAction?.subject_tags?.length || 0);

  return (
    <Card className="telemetry-panel simple-monitor-panel">
      <CardHeader>
        <div className="panel-kicker">状态</div>
        <CardTitle>系统状态</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="simple-status-list">
          <StatusLine icon={ShieldCheck} label="主机" value={system?.label || "读取中"} ok={system?.state === "normal"} />
          <StatusLine icon={Bot} label="AI" value={system?.codex_ready ? "在线" : "待检查"} ok={Boolean(system?.codex_ready)} />
          <StatusLine icon={UploadCloud} label="上传" value={system?.batch_running ? "运行中" : "待命"} ok />
          <StatusLine icon={Activity} label="自动模式" value={settings?.auto_mode_enabled === false ? "关闭" : "开启"} ok />
        </div>

        <section className="current-task-card">
          <div className="current-task-head">
            <PackageCheck className="h-4 w-4" />
            <strong>当前任务</strong>
            <Badge variant={currentBadge(activeStatus)}>
              {ozonMode ? searchAction?.last_upload?.status === "submitted" ? "已上传" : searchAction ? "已读取" : "待选择" : activeStatus ? statusLabel(activeStatus) : "待命"}
            </Badge>
          </div>
          <h3>{truncate(activeTitle, 46)}</h3>
          <p>{activeStep ? readableStageName(activeStep) : "左侧选择商品后显示"}</p>
          <div className="current-task-metrics">
            <span><b>{ozonMode ? ozonSkuCount : currentProduct?.sku_count || currentDetail?.skus?.length || 0}</b> SKU</span>
            <span><b>{ozonMode ? ozonImages : currentProduct?.image_count || currentDetail?.images?.length || 0}</b> 图片</span>
            <span><b>{ozonMode ? ozonTagCount : pendingCount}</b> {ozonMode ? "标签" : "待处理"}</span>
          </div>
        </section>

        <section className="current-task-card compact">
          <div className="current-task-head">
            <Clock3 className="h-4 w-4" />
            <strong>最近失败原因</strong>
          </div>
          <p>{currentDetail?.error?.message || currentDetail?.production_readiness?.errors?.[0] || "当前没有失败原因"}</p>
        </section>
      </CardContent>
    </Card>
  );
}
