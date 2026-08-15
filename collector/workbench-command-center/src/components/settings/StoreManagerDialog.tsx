import { useEffect, useMemo, useState } from "react";
import { Loader2, Plus, ShieldCheck, Store, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { deleteShop, saveShop, setShopEnabled, validateShop } from "@/services/workbenchApi";
import { cn, formatTime } from "@/lib/utils";
import type { ShopCard, ShopPayload } from "@/types/workbench";

type Draft = {
  display_name: string;
  client_id: string;
  api_key: string;
  currency: string;
  notes: string;
};

const emptyDraft: Draft = {
  display_name: "",
  client_id: "",
  api_key: "",
  currency: "CNY",
  notes: "",
};

function shopStatusTone(shop: ShopCard) {
  if (!shop.enabled) return "muted";
  if (shop.connection_status === "connected") return "default";
  if (shop.connection_status === "failed") return "danger";
  return "warning";
}

function shopStatusLabel(shop: ShopCard) {
  if (!shop.enabled) return "已禁用";
  if (shop.connection_status === "connected") return "已连接";
  if (shop.connection_status === "failed") return "连接失败";
  return shop.connection_status || "未验证";
}

function draftFromShop(shop: ShopCard): Draft {
  return {
    display_name: shop.display_name || shop.id,
    client_id: "",
    api_key: "",
    currency: shop.currency || "CNY",
    notes: shop.notes || "",
  };
}

export function StoreManagerDialog({
  open,
  onOpenChange,
  shops,
  loading,
  onRefresh,
  onResult,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  shops?: ShopCard[];
  loading?: boolean;
  onRefresh: () => Promise<unknown>;
  onResult: (message: string, tone?: "ok" | "danger" | "idle") => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [busyKey, setBusyKey] = useState("");
  const [message, setMessage] = useState("");
  const [testMessages, setTestMessages] = useState<Record<string, { tone: "ok" | "danger"; text: string }>>({});
  const visibleShops = useMemo(() => shops || [], [shops]);
  const editingShop = visibleShops.find((shop) => shop.id === editingId);

  function editShop(shop: ShopCard) {
    setEditingId(shop.id);
    setDraft(draftFromShop(shop));
    setMessage("编辑时留空 Client-Id / Api-Key 表示保留现有本地密钥。");
  }

  function newShop() {
    setEditingId(null);
    setDraft(emptyDraft);
    setMessage("新增店铺必须填写 Client-Id 和 Api-Key。");
  }

  async function saveCurrentShop() {
    const payload: ShopPayload = {
      display_name: draft.display_name.trim(),
      client_id: draft.client_id.trim(),
      api_key: draft.api_key.trim(),
      currency: draft.currency.trim() || "CNY",
      notes: draft.notes.trim(),
      enabled: editingShop?.enabled ?? true,
    };
    setBusyKey("save");
    setMessage("");
    try {
      await saveShop(payload, editingId || undefined);
      await onRefresh();
      onResult(editingId ? "店铺配置已更新；修改凭证后需要重新只读测试。" : "店铺已添加；请执行只读连接测试。", "ok");
      if (!editingId) setDraft(emptyDraft);
    } catch (err) {
      const text = err instanceof Error ? err.message : "店铺保存失败";
      setMessage(text);
      onResult(text, "danger");
    } finally {
      setBusyKey("");
    }
  }

  async function toggle(shop: ShopCard) {
    setBusyKey(`toggle:${shop.id}`);
    try {
      await setShopEnabled(shop.id, !shop.enabled);
      await onRefresh();
      onResult(`${shop.display_name || shop.id} 已${shop.enabled ? "禁用" : "启用"}`, "ok");
    } catch (err) {
      onResult(err instanceof Error ? err.message : "店铺启用状态修改失败", "danger");
    } finally {
      setBusyKey("");
    }
  }

  async function validate(shop: ShopCard) {
    setBusyKey(`validate:${shop.id}`);
    try {
      const result = await validateShop(shop.id);
      await onRefresh();
      const ok = result.connection_status === "connected";
      const text = ok ? "只读连接测试通过" : `连接失败：${result.last_validation_error || "未返回原因"}`;
      setTestMessages((current) => ({ ...current, [shop.id]: { tone: ok ? "ok" : "danger", text } }));
      onResult(text, ok ? "ok" : "danger");
    } catch (err) {
      const text = err instanceof Error ? err.message : "只读连接测试失败";
      setTestMessages((current) => ({ ...current, [shop.id]: { tone: "danger", text } }));
      onResult(text, "danger");
    } finally {
      setBusyKey("");
    }
  }

  async function remove(shop: ShopCard) {
    if (!window.confirm(`只删除本地店铺配置，不会删除 Ozon 后台商品。确认删除 ${shop.display_name || shop.id}？`)) return;
    setBusyKey(`delete:${shop.id}`);
    try {
      await deleteShop(shop.id);
      await onRefresh();
      onResult("本地店铺配置已删除，Ozon 后台不受影响。", "ok");
      if (editingId === shop.id) newShop();
    } catch (err) {
      onResult(err instanceof Error ? err.message : "店铺删除失败", "danger");
    } finally {
      setBusyKey("");
    }
  }

  useEffect(() => {
    if (!open) return;
    onRefresh().catch(() => null);
    if (!editingId && !draft.display_name) newShop();
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="store-manager-dialog">
        <DialogHeader>
          <DialogTitle>店铺管理</DialogTitle>
          <DialogDescription>管理本地 Ozon 店铺配置。只读测试按钮会调用 Seller API 只读连接检查；其他操作只改本地配置。</DialogDescription>
        </DialogHeader>
        <div className="store-manager-grid">
          <section className="store-list-panel">
            <div className="store-manager-head">
              <strong>店铺状态</strong>
              <Button size="sm" variant="secondary" onClick={newShop}>
                <Plus className="h-3.5 w-3.5" />
                新增
              </Button>
            </div>
            <div className="store-manager-list">
              {loading ? (
                <div className="store-empty"><Loader2 className="h-4 w-4 animate-spin" /> 正在读取店铺</div>
              ) : visibleShops.length ? visibleShops.map((shop) => (
                <article key={shop.id} className={cn("store-manager-row", editingId === shop.id && "active")}>
                  <button type="button" onClick={() => editShop(shop)}>
                    <Store className="h-4 w-4" />
                    <span>
                      <strong>{shop.display_name || shop.id}</strong>
                      <small>{shop.credentials_display || "凭证未配置"} · {formatTime(shop.last_validated_at || "")}</small>
                    </span>
                    <Badge variant={shopStatusTone(shop)}>{shopStatusLabel(shop)}</Badge>
                  </button>
                  {(testMessages[shop.id]?.text || shop.last_validation_error) && (
                    <p className={testMessages[shop.id]?.tone === "ok" ? "ok" : ""}>
                      {testMessages[shop.id]?.text || shop.last_validation_error}
                    </p>
                  )}
                  <div className="store-row-actions">
                    <Button size="sm" variant="secondary" onClick={() => validate(shop)} disabled={busyKey === `validate:${shop.id}`}>
                      {busyKey === `validate:${shop.id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                      只读测试
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => toggle(shop)} disabled={busyKey === `toggle:${shop.id}`}>
                      {shop.enabled ? "禁用" : "启用"}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => remove(shop)} disabled={busyKey === `delete:${shop.id}`}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </article>
              )) : (
                <div className="store-empty">没有店铺配置</div>
              )}
            </div>
          </section>
          <section className="store-edit-panel">
            <div className="store-manager-head">
              <strong>{editingId ? `编辑 ${editingShop?.display_name || editingId}` : "新增店铺"}</strong>
              {editingId && <Badge variant="muted">{editingId}</Badge>}
            </div>
            <label>
              <span>店铺名称</span>
              <input value={draft.display_name} onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} placeholder="例如 zhonglian1" />
            </label>
            <label>
              <span>Client-Id</span>
              <input value={draft.client_id} onChange={(event) => setDraft({ ...draft, client_id: event.target.value })} placeholder={editingId ? "留空表示不修改" : "新增店铺必填"} />
            </label>
            <label>
              <span>Api-Key</span>
              <input type="password" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} placeholder={editingId ? "留空表示不修改" : "新增店铺必填"} />
            </label>
            <label>
              <span>币种</span>
              <select value={draft.currency} onChange={(event) => setDraft({ ...draft, currency: event.target.value })}>
                <option value="CNY">CNY</option>
                <option value="RUB">RUB</option>
              </select>
            </label>
            <label>
              <span>备注</span>
              <textarea value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} placeholder="本地备注，不会提交到 Ozon" />
            </label>
            {message && <div className="store-manager-message">{message}</div>}
            <div className="store-edit-actions">
              <Button variant="secondary" onClick={() => onOpenChange(false)}>关闭</Button>
              <Button onClick={saveCurrentShop} disabled={busyKey === "save" || !draft.display_name.trim()}>
                {busyKey === "save" && <Loader2 className="h-4 w-4 animate-spin" />}
                保存本地配置
              </Button>
            </div>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
