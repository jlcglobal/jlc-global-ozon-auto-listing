import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { ChartPie, FileSpreadsheet, Link2, ListChecks, PackageSearch, Plus, Power, Settings, Workflow } from "lucide-react";
import jlcLogo from "@/assets/jlc-global-logo.png";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { topServices } from "@/lib/workbenchFormat";
import type { SystemStatus } from "@/types/workbench";

function useClock() {
  const [clock, setClock] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return clock;
}

export function CommandBar({
  system,
  onOpenBatchLauncher,
  onOpenTaskCenter,
  onOpenOzonReferenceLauncher,
  onOpenFinanceCenter,
  onOpenStoreManager,
  workspaceMode,
  onSelectWorkspaceMode,
  canStopBatch,
  stoppingBatch,
  onStopBatch,
  onGenerateKeywordGrowthReport,
  generatingKeywordGrowthReport,
}: {
  system?: SystemStatus | null;
  onOpenBatchLauncher: () => void;
  onOpenTaskCenter: () => void;
  onOpenOzonReferenceLauncher: () => void;
  onOpenFinanceCenter: () => void;
  onOpenStoreManager: () => void;
  workspaceMode: "local" | "ozon";
  onSelectWorkspaceMode: (mode: "local" | "ozon") => void;
  canStopBatch?: boolean;
  stoppingBatch?: boolean;
  onStopBatch: () => void;
  onGenerateKeywordGrowthReport?: () => void;
  generatingKeywordGrowthReport?: boolean;
}) {
  const clock = useClock();
  return (
    <motion.header
      initial={{ opacity: 0, y: -12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}
      className="command-bar"
    >
      <div className="brand-lockup">
        <div className="brand-core">
          <img src={jlcLogo} alt="JLC GLOBAL 嘉联创" />
        </div>
        <div>
          <strong>JLC GLOBAL 嘉联创</strong>
          <span>AI 商品优化工作台</span>
        </div>
        <div className="online-pulse" />
        <small>按店铺读取商品</small>
      </div>
      <div className="service-grid">
        {topServices(system).slice(0, 3).map((service) => {
          const Icon = service.icon;
          return (
            <Tooltip key={service.label}>
              <TooltipTrigger asChild>
                <div className="service-chip">
                  <span className={cn("status-dot", service.ok ? "ok" : "warn")} />
                  <Icon className="h-3.5 w-3.5 text-emerald-200/70" />
                  <div>
                    <small>{service.label}</small>
                    <strong>{service.value}</strong>
                  </div>
                </div>
              </TooltipTrigger>
              <TooltipContent>{service.label} 只读取本地工作台状态。</TooltipContent>
            </Tooltip>
          );
        })}
      </div>
      <div className="operator-zone">
        <div className="clock">{clock.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</div>
        <div className="workspace-mode-tabs" role="tablist" aria-label="工作台视图">
          <Button
            type="button"
            variant={workspaceMode === "local" ? "default" : "secondary"}
            size="sm"
            role="tab"
            aria-selected={workspaceMode === "local"}
            onClick={() => onSelectWorkspaceMode("local")}
            title="生产"
          >
            <Workflow className="h-4 w-4" />
            <span>生产</span>
          </Button>
          <Button
            type="button"
            variant={workspaceMode === "ozon" ? "default" : "secondary"}
            size="sm"
            role="tab"
            aria-selected={workspaceMode === "ozon"}
            onClick={() => onSelectWorkspaceMode("ozon")}
            title="商品/关键词"
          >
            <PackageSearch className="h-4 w-4" />
            <span>商品/关键词</span>
          </Button>
        </div>
        <Button className="command-secondary-action" variant="secondary" size="sm" onClick={onOpenTaskCenter} title="任务中心" aria-label="任务中心">
          <ListChecks className="h-4 w-4" />
          <span className="command-action-label">任务中心</span>
        </Button>
        <Button className="finance-entry-button command-secondary-action" variant="secondary" size="sm" onClick={onOpenFinanceCenter} title="财务利润" aria-label="财务利润">
          <span className="finance-circle-icon"><ChartPie className="h-3.5 w-3.5" /></span>
          <span className="command-action-label">财务利润</span>
        </Button>
        <Button variant="default" size="sm" onClick={onOpenBatchLauncher}>
          <Plus className="h-4 w-4" />
          <span>启动采集箱商品</span>
        </Button>
        <Button className="command-secondary-action" variant="secondary" size="sm" onClick={onOpenOzonReferenceLauncher} title="Ozon参考上架" aria-label="Ozon参考上架">
          <Link2 className="h-4 w-4" />
          <span className="command-action-label">Ozon参考上架</span>
        </Button>
        <Button
          className="command-secondary-action"
          variant="secondary"
          size="sm"
          onClick={onGenerateKeywordGrowthReport}
          disabled={!onGenerateKeywordGrowthReport || generatingKeywordGrowthReport}
          title="生成 Ozon 关键词增长机会周报"
          aria-label="关键词周报"
        >
          <FileSpreadsheet className="h-4 w-4" />
          <span className="command-action-label">{generatingKeywordGrowthReport ? "生成中" : "关键词周报"}</span>
        </Button>
        <Button variant="secondary" size="icon" aria-label="店铺设置" onClick={onOpenStoreManager}>
          <Settings className="h-4 w-4" />
        </Button>
        <Dialog>
          <DialogTrigger asChild>
            <Button variant="danger" size="sm" disabled={!canStopBatch || stoppingBatch}>
              <Power className="h-4 w-4" />
              <span>安全停止</span>
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>手动停止当前生产任务</DialogTitle>
              <DialogDescription>
                系统会在最近安全断点停止并保留进度。不会调用库存接口，也不会重复提交 Ozon。
              </DialogDescription>
            </DialogHeader>
            <div className="emergency-stop-copy">
              <p>这不是强制退出。当前商品会在安全位置写入断点，之后可以继续生产。</p>
            </div>
            <div className="emergency-stop-actions">
              <Button variant="danger" onClick={onStopBatch} disabled={!canStopBatch || stoppingBatch}>
                <Power className="h-4 w-4" />
                {stoppingBatch ? "正在请求停止" : "确认安全停止"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </motion.header>
  );
}
