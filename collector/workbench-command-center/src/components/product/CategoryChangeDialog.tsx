import { useEffect, useMemo, useState } from "react";
import { Loader2, Search, Tag } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { loadCategoryRules, searchCategories, updateProductCategory } from "@/services/workbenchApi";
import type { CategoryCandidate, CategoryRulesResponse, ProductDetail } from "@/types/workbench";

function categoryName(item?: CategoryCandidate) {
  return item?.name_zh || item?.name || "未翻译类目";
}

function categoryPath(item?: CategoryCandidate) {
  const path = item?.path_zh || item?.path || [];
  return Array.isArray(path) ? path.join(" / ") : path;
}

function fallbackCategoryQueries(detail: ProductDetail | null, query: string) {
  const title = detail?.source?.title_cn || "";
  const candidates = [
    query,
    detail?.category?.category_name_zh || "",
    ...Array.from(title.matchAll(/[\u4e00-\u9fa5A-Za-z0-9]{2,8}/g)).map((match) => match[0]).filter((word) => /灯|架|盒|包|杯|瓶|线|器|饰|柜|篮|车|鞋|表|笔|夹/.test(word)),
  ];
  return candidates.filter((item, index) => item.trim() && candidates.indexOf(item) === index);
}

export function CategoryChangeDialog({
  open,
  onOpenChange,
  detail,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  detail: ProductDetail | null;
  onChanged: (message: string) => Promise<void> | void;
}) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CategoryCandidate[]>([]);
  const [selected, setSelected] = useState<CategoryCandidate | null>(null);
  const [rules, setRules] = useState<CategoryRulesResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const defaultQuery = useMemo(() => detail?.source?.title_cn || "", [detail?.source?.title_cn]);

  async function runSearch(nextQuery = query || defaultQuery) {
    setBusy(true);
    setMessage("正在搜索本地 Ozon 类目树");
    setSelected(null);
    setRules(null);
    try {
      let result = await searchCategories(nextQuery, 30);
      let usedQuery = nextQuery;
      if (!result.count) {
        for (const fallbackQuery of fallbackCategoryQueries(detail, nextQuery).slice(1)) {
          result = await searchCategories(fallbackQuery, 30);
          usedQuery = fallbackQuery;
          if (result.count) break;
        }
      }
      setItems(result.items || []);
      setMessage(`找到 ${result.count || 0} 个匹配类目${usedQuery !== nextQuery ? ` · 已用关键词「${usedQuery}」重搜` : ""}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "类目搜索失败");
    } finally {
      setBusy(false);
    }
  }

  async function choose(item: CategoryCandidate) {
    setSelected(item);
    setRules(null);
    setBusy(true);
    setMessage(`正在读取 ${categoryName(item)} 的官方属性规则`);
    try {
      const nextRules = await loadCategoryRules(item.category_id, item.type_id);
      setRules(nextRules);
      setMessage(`已读取规则：必填 ${(nextRules.required_attribute_ids || []).length} · SKU维度 ${(nextRules.aspect_attribute_ids || []).length}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "类目规则读取失败");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!detail?.product_id || !selected || !rules) return;
    setSaving(true);
    try {
      const result = await updateProductCategory(detail.product_id, selected, rules);
      const invalidated = result.invalidated?.length ?? 0;
      await onChanged(result.message || `最终类目已修改，${invalidated} 项旧结果已失效。`);
      onOpenChange(false);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "类目修改失败");
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    setQuery(defaultQuery);
    setItems([]);
    setSelected(null);
    setRules(null);
    setMessage("");
    runSearch(defaultQuery).catch(() => null);
  }, [defaultQuery, open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="category-dialog-shell">
        <DialogHeader>
          <DialogTitle>修改最终 Ozon 类目</DialogTitle>
          <DialogDescription>类目变化会让属性、图片策略和上传草稿失效；原始 1688 资料不会删除。</DialogDescription>
        </DialogHeader>
        <div className="category-search-bar">
          <Search className="h-4 w-4" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索中文类目或商品关键词" />
          <Button size="sm" variant="secondary" onClick={() => runSearch()} disabled={busy}>
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "搜索"}
          </Button>
        </div>
        <div className="category-message">{message || "选择一个类目后会读取官方属性规则。"}</div>
        <div className="category-result-list">
          {items.map((item) => (
            <button
              key={`${item.category_id}-${item.type_id}`}
              type="button"
              className={selected?.category_id === item.category_id && selected?.type_id === item.type_id ? "selected" : ""}
              onClick={() => choose(item)}
            >
              <Tag className="h-4 w-4" />
              <span>
                <strong>{categoryName(item)}</strong>
                <small>{categoryPath(item)}</small>
              </span>
              <Badge variant="muted">type {item.type_id}</Badge>
            </button>
          ))}
          {!items.length && !busy && <div className="category-empty">没有类目结果</div>}
        </div>
        <div className="category-actions">
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={saving}>关闭</Button>
          <Button onClick={save} disabled={!selected || !rules || saving}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "确认修改类目"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
