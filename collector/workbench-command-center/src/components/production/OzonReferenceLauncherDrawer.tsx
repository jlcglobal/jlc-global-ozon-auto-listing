import { useEffect, useMemo, useState } from "react";
import { Check, FileText, ImagePlus, Loader2, Link2, Search, Store, Tag, Trash2, Upload, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { loadCategoryRules, searchCategories } from "@/services/workbenchApi";
import type {
  CategoryCandidate,
  CreateOzonReferenceTasksResponse,
  OzonReferenceImportedImage,
  OzonReferenceManualInputs,
  OzonReferenceTask,
  OzonReferenceTaskInput,
  OzonReferenceTasksResponse,
  ShopCard,
  UpdateOzonReferenceTaskResponse,
} from "@/types/workbench";

function isConnectedShop(shop: ShopCard) {
  return Boolean(shop.enabled) && shop.connection_status === "connected";
}

function parseLinkPreview(text: string) {
  return Array.from(new Set(text.split(/[\s,，；;]+/).map((item) => item.trim()).filter(Boolean)));
}

function taskStatusLabel(task: OzonReferenceTask) {
  if (task.display_status) return task.display_status;
  if (task.status === "queued") return "待处理";
  if (task.status === "processing") return "处理中";
  if (task.status === "completed") return "已完成";
  if (task.status === "failed") return "失败";
  return task.status || "未知";
}

function resultMessage(result: CreateOzonReferenceTasksResponse) {
  if (result.message) return result.message;
  if (result.status === "queued") return `已加入 ${result.created_count} 个 Ozon 参考上架任务`;
  if (result.status === "already_queued") return "这些链接已在队列中";
  return `工作台返回：${result.status}`;
}

function storedManualInputsToForm(task: OzonReferenceTask): OzonReferenceManualInputs {
  const raw = task.manual_inputs || {};
  const dimensions = (raw.package_dimensions_mm && typeof raw.package_dimensions_mm === "object")
    ? raw.package_dimensions_mm as Record<string, unknown>
    : {};
  return {
    length_mm: dimensions.length_mm as number | string | undefined,
    width_mm: dimensions.width_mm as number | string | undefined,
    height_mm: dimensions.height_mm as number | string | undefined,
    weight_g: raw.package_weight_g as number | string | undefined,
    selling_price_cny: raw.selling_price_cny as number | string | undefined,
    ozon_category_selection: raw.ozon_category_selection as OzonReferenceManualInputs["ozon_category_selection"],
  };
}

function cleanManualInputs(value: OzonReferenceManualInputs): OzonReferenceManualInputs {
  const result: OzonReferenceManualInputs = {};
  (["length_mm", "width_mm", "height_mm", "weight_g", "selling_price_cny"] as const).forEach((key) => {
    const raw = value[key];
    if (raw === "" || raw === undefined || raw === null) return;
    const number = Number(String(raw).replace(",", "."));
    if (Number.isFinite(number) && number > 0) {
      result[key] = number;
    }
  });
  if (value.ozon_category_selection?.category_id && value.ozon_category_selection?.type_id && value.ozon_category_selection.rules_snapshot) {
    result.ozon_category_selection = value.ozon_category_selection;
  }
  return result;
}

function categoryName(item?: CategoryCandidate | OzonReferenceManualInputs["ozon_category_selection"]) {
  if (!item) return "未翻译类目";
  if ("rules_snapshot" in item) return item.category_name_zh || "未翻译类目";
  const candidate = item as CategoryCandidate;
  return candidate.name_zh || candidate.name || "未翻译类目";
}

function categoryPath(item?: CategoryCandidate | OzonReferenceManualInputs["ozon_category_selection"]) {
  if (!item) return "";
  const path = "rules_snapshot" in item ? (item.category_path_zh || item.category_path) : ((item as CategoryCandidate).path_zh || (item as CategoryCandidate).path);
  return Array.isArray(path) ? path.join(" / ") : String(path || "");
}

function hasRequiredInputs(value: OzonReferenceManualInputs) {
  const clean = cleanManualInputs(value);
  return Boolean(
    clean.length_mm &&
    clean.width_mm &&
    clean.height_mm &&
    clean.weight_g &&
    clean.selling_price_cny &&
    clean.ozon_category_selection?.category_id &&
    clean.ozon_category_selection?.type_id,
  );
}

function missingRequiredInputLabels(value: OzonReferenceManualInputs) {
  const clean = cleanManualInputs(value);
  const missing: string[] = [];
  if (!clean.length_mm) missing.push("长MM");
  if (!clean.width_mm) missing.push("宽MM");
  if (!clean.height_mm) missing.push("高MM");
  if (!clean.weight_g) missing.push("重量G");
  if (!clean.selling_price_cny) missing.push("售价CNY");
  if (!clean.ozon_category_selection?.category_id || !clean.ozon_category_selection?.type_id) missing.push("最终Ozon类目");
  return missing;
}

function readImageFile(file: File): Promise<OzonReferenceImportedImage> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith("image/")) {
      reject(new Error(`${file.name} 不是图片文件`));
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      reject(new Error(`${file.name} 超过 8MB`));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      resolve({
        url: `fitkun-file://${encodeURIComponent(file.name)}`,
        data_url: String(reader.result || ""),
        content_type: file.type || "image/jpeg",
        byte_size: file.size,
        name: file.name,
      });
    };
    reader.onerror = () => reject(new Error(`${file.name} 读取失败`));
    reader.readAsDataURL(file);
  });
}

function parseImageUrls(text: string): OzonReferenceImportedImage[] {
  return Array.from(new Set(text.split(/[\s,，；;]+/).map((item) => item.trim()).filter(Boolean)))
    .filter((url) => /^https?:\/\//i.test(url))
    .slice(0, 24)
    .map((url, index) => ({
      url,
      content_type: "image/jpeg",
      byte_size: 0,
      name: `fitkun-url-${index + 1}`,
    }));
}

export function OzonReferenceLauncherDrawer({
  open,
  onOpenChange,
  shops,
  tasks,
  loading,
  error,
  onRefresh,
  onCreateTasks,
  onUpdateTask,
  onContinueQueue,
  onCreated,
  onOpenProduct,
  focusTaskId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  focusTaskId?: string;
  shops?: ShopCard[];
  tasks?: OzonReferenceTasksResponse | null;
  loading?: boolean;
  error?: string;
  onRefresh: () => Promise<unknown>;
  onCreateTasks: (items: OzonReferenceTaskInput[], storeIds: string[]) => Promise<CreateOzonReferenceTasksResponse>;
  onUpdateTask: (taskId: string, manualInputs: OzonReferenceManualInputs, storeIds: string[]) => Promise<UpdateOzonReferenceTaskResponse>;
  onContinueQueue: () => Promise<unknown>;
  onCreated: (result: CreateOzonReferenceTasksResponse) => void;
  onOpenProduct: (productId: string) => void;
}) {
  const [text, setText] = useState("");
  const [manualInputsByUrl, setManualInputsByUrl] = useState<Record<string, OzonReferenceManualInputs>>({});
  const [fitkunImagesByUrl, setFitkunImagesByUrl] = useState<Record<string, OzonReferenceImportedImage[]>>({});
  const [fitkunUrlTextByUrl, setFitkunUrlTextByUrl] = useState<Record<string, string>>({});
  const [categoryQueryByUrl, setCategoryQueryByUrl] = useState<Record<string, string>>({});
  const [categoryResultsByUrl, setCategoryResultsByUrl] = useState<Record<string, CategoryCandidate[]>>({});
  const [categoryMessageByUrl, setCategoryMessageByUrl] = useState<Record<string, string>>({});
  const [selectedStores, setSelectedStores] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [categoryBusyUrl, setCategoryBusyUrl] = useState("");
  const [localError, setLocalError] = useState("");
  const [lastResult, setLastResult] = useState<CreateOzonReferenceTasksResponse | null>(null);
  const [lastResultText, setLastResultText] = useState("");
  const [editingTaskId, setEditingTaskId] = useState("");

  const connectedShops = useMemo(() => (shops || []).filter(isConnectedShop), [shops]);
  const links = useMemo(() => parseLinkPreview(text), [text]);
  const referenceItems = useMemo(
    () => links.map((url) => ({
      url,
      ...cleanManualInputs(manualInputsByUrl[url] || {}),
      fitkun_images: [
        ...(fitkunImagesByUrl[url] || []),
        ...parseImageUrls(fitkunUrlTextByUrl[url] || ""),
      ].slice(0, 24),
    })),
    [fitkunImagesByUrl, fitkunUrlTextByUrl, links, manualInputsByUrl],
  );
  const missingInputCount = useMemo(
    () => links.filter((url) => !hasRequiredInputs(manualInputsByUrl[url] || {})).length,
    [links, manualInputsByUrl],
  );
  const visibleTasks = useMemo(() => {
    const items = tasks?.items || [];
    if (!focusTaskId) return items.slice(0, 8);
    return [...items]
      .sort((left, right) => {
        if (left.task_id === focusTaskId) return -1;
        if (right.task_id === focusTaskId) return 1;
        return 0;
      })
      .slice(0, 8);
  }, [focusTaskId, tasks?.items]);

  useEffect(() => {
    if (!open) return;
    setLocalError("");
    setLastResult(null);
    setLastResultText("");
    onRefresh().catch(() => null);
  }, [open]);

  useEffect(() => {
    if (!open || selectedStores.length || !connectedShops.length) return;
    setSelectedStores(connectedShops.map((shop) => shop.id));
  }, [connectedShops, open, selectedStores.length]);

  useEffect(() => {
    if (!open || !focusTaskId || editingTaskId === focusTaskId) return;
    const task = (tasks?.items || []).find((item) => item.task_id === focusTaskId);
    if (!task || task.created_product_id || !task.missing_fields?.length) return;
    editExistingTask(task);
  }, [editingTaskId, focusTaskId, open, tasks?.items]);

  function toggleStore(storeId: string) {
    setLastResult(null);
    setSelectedStores((current) =>
      current.includes(storeId) ? current.filter((id) => id !== storeId) : [...current, storeId],
    );
  }

  function updateManualInput(url: string, key: keyof OzonReferenceManualInputs, value: string) {
    setLastResult(null);
    setManualInputsByUrl((current) => ({
      ...current,
      [url]: {
        ...(current[url] || {}),
        [key]: value,
      },
    }));
  }

  async function searchCategory(url: string) {
    const query = (categoryQueryByUrl[url] || "").trim();
    if (!query) {
      setCategoryMessageByUrl((current) => ({ ...current, [url]: "请输入类目关键词，比如 手办、杯子、收纳盒" }));
      return;
    }
    setCategoryBusyUrl(url);
    setCategoryMessageByUrl((current) => ({ ...current, [url]: "正在搜索本地 Ozon 类目树" }));
    try {
      const result = await searchCategories(query, 12);
      setCategoryResultsByUrl((current) => ({ ...current, [url]: result.items || [] }));
      setCategoryMessageByUrl((current) => ({ ...current, [url]: `找到 ${result.count || 0} 个匹配类目` }));
    } catch (err) {
      setCategoryMessageByUrl((current) => ({ ...current, [url]: err instanceof Error ? err.message : "类目搜索失败" }));
    } finally {
      setCategoryBusyUrl("");
    }
  }

  async function chooseCategory(url: string, item: CategoryCandidate) {
    setCategoryBusyUrl(url);
    setCategoryMessageByUrl((current) => ({ ...current, [url]: `正在读取 ${categoryName(item)} 的官方属性规则` }));
    try {
      const rules = await loadCategoryRules(item.category_id, item.type_id);
      setManualInputsByUrl((current) => ({
        ...current,
        [url]: {
          ...(current[url] || {}),
          ozon_category_selection: {
            category_id: item.category_id,
            type_id: item.type_id,
            category_path: item.path,
            category_name_zh: item.name_zh,
            category_path_zh: item.path_zh,
            selected_at: new Date().toISOString(),
            rules_snapshot: rules,
          },
        },
      }));
      setLastResult(null);
      setCategoryMessageByUrl((current) => ({
        ...current,
        [url]: `已选择：${categoryName(item)} · 必填 ${(rules.required_attribute_ids || []).length} · SKU维度 ${(rules.aspect_attribute_ids || []).length}`,
      }));
    } catch (err) {
      setCategoryMessageByUrl((current) => ({ ...current, [url]: err instanceof Error ? err.message : "类目规则读取失败" }));
    } finally {
      setCategoryBusyUrl("");
    }
  }

  async function addFitkunFiles(url: string, files: FileList | null) {
    if (!files?.length) return;
    setLastResult(null);
    setLocalError("");
    try {
      const next = await Promise.all(Array.from(files).slice(0, 12).map(readImageFile));
      setFitkunImagesByUrl((current) => ({
        ...current,
        [url]: [...(current[url] || []), ...next].slice(0, 24),
      }));
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "FITKUN 图片读取失败");
    }
  }

  function removeFitkunImage(url: string, index: number) {
    setLastResult(null);
    setFitkunImagesByUrl((current) => ({
      ...current,
      [url]: (current[url] || []).filter((_, itemIndex) => itemIndex !== index),
    }));
  }

  function editExistingTask(task: OzonReferenceTask) {
    if (!task.source_url) return;
    setEditingTaskId(task.task_id);
    setText(task.source_url);
    setManualInputsByUrl({ [task.source_url]: storedManualInputsToForm(task) });
    setFitkunImagesByUrl({});
    setFitkunUrlTextByUrl({});
    setCategoryQueryByUrl({});
    setCategoryResultsByUrl({});
    setCategoryMessageByUrl({});
    if (task.target_store_ids?.length) setSelectedStores(task.target_store_ids);
    setLocalError("");
    setLastResult(null);
    setLastResultText("");
  }

  async function submit() {
    if (!links.length || !selectedStores.length) return;
    if (missingInputCount > 0) {
      setLocalError(`还有 ${missingInputCount} 个链接缺少尺寸、重量、售价或最终Ozon类目。`);
      return;
    }
    setBusy(true);
    setLocalError("");
    setLastResult(null);
    setLastResultText("");
    try {
      if (editingTaskId) {
        const url = links[0];
        const result = await onUpdateTask(editingTaskId, cleanManualInputs(manualInputsByUrl[url] || {}), selectedStores);
        setLastResultText(result.message || "Ozon参考任务参数已保存，已继续生成。");
        setEditingTaskId("");
        setText("");
        setManualInputsByUrl({});
        setFitkunImagesByUrl({});
        setFitkunUrlTextByUrl({});
        setCategoryQueryByUrl({});
        setCategoryResultsByUrl({});
        setCategoryMessageByUrl({});
        onRefresh().catch(() => null);
      } else {
        const result = await onCreateTasks(referenceItems, selectedStores);
        setLastResult(result);
        onCreated(result);
        if (result.created_count > 0) {
          setText("");
          setManualInputsByUrl({});
          setFitkunImagesByUrl({});
          setFitkunUrlTextByUrl({});
          setCategoryQueryByUrl({});
          setCategoryResultsByUrl({});
          setCategoryMessageByUrl({});
        }
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Ozon参考任务创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="batch-launcher ozon-reference-launcher">
        <SheetHeader className="batch-launcher-head">
          <div className="panel-kicker">Ozon参考上架</div>
          <SheetTitle>批量粘贴 Ozon 商品卡链接</SheetTitle>
          <SheetDescription>
            系统会把链接加入参考上架队列；后续自动抓取公开商品卡图片和文字，生成我方商品卡，不提交库存。
          </SheetDescription>
        </SheetHeader>

        {(error || localError) && (
          <div className="launcher-alert">
            <XCircle className="h-4 w-4" />
            <span>{localError || error}</span>
          </div>
        )}
        {lastResult && (
          <div className="launcher-result">
            <Check className="h-4 w-4" />
            <span>{resultMessage(lastResult)}</span>
          </div>
        )}
        {lastResultText && (
          <div className="launcher-result">
            <Check className="h-4 w-4" />
            <span>{lastResultText}</span>
          </div>
        )}

        <ScrollArea className="launcher-body">
          {loading ? (
            <div className="launcher-loading">
              <Loader2 className="h-5 w-5 animate-spin" />
              正在读取 Ozon 参考队列
            </div>
          ) : (
            <div className="ozon-reference-form">
              <label className="ozon-reference-field">
                <span>Ozon 商品卡链接</span>
                <textarea
                  value={text}
                  onChange={(event) => {
                    setLastResult(null);
                    setText(event.target.value);
                  }}
                  placeholder={"每行一个链接，例如：\nhttps://www.ozon.ru/product/..."}
                />
              </label>

              <div className="ozon-reference-summary">
                <Badge variant={links.length ? "default" : "muted"}>{links.length} 个链接</Badge>
                {editingTaskId && <Badge variant="warning">补参数</Badge>}
                <span>每个链接单独填写尺寸、重量、售价并选择最终 Ozon 类目；单位固定为 MM / G / CNY。</span>
                {!!missingInputCount && <Badge variant="warning">缺 {missingInputCount} 个</Badge>}
              </div>

              {!!links.length && (
                <section className="ozon-reference-input-list">
                  <div className="panel-kicker">逐链接商品参数</div>
                  {links.map((url, index) => {
                    const values = manualInputsByUrl[url] || {};
                    const missingLabels = missingRequiredInputLabels(values);
                    return (
                      <div key={url} className="ozon-reference-input-row">
                        <div className="ozon-reference-link-line">
                          <Badge variant="muted">#{index + 1}</Badge>
                          <span>{url}</span>
                        </div>
                        <div className="ozon-reference-measure-grid">
                          <label>
                            <span>长 MM</span>
                            <input value={values.length_mm ?? ""} inputMode="decimal" onChange={(event) => updateManualInput(url, "length_mm", event.target.value)} />
                          </label>
                          <label>
                            <span>宽 MM</span>
                            <input value={values.width_mm ?? ""} inputMode="decimal" onChange={(event) => updateManualInput(url, "width_mm", event.target.value)} />
                          </label>
                          <label>
                            <span>高 MM</span>
                            <input value={values.height_mm ?? ""} inputMode="decimal" onChange={(event) => updateManualInput(url, "height_mm", event.target.value)} />
                          </label>
                          <label>
                            <span>重量 G</span>
                            <input value={values.weight_g ?? ""} inputMode="decimal" onChange={(event) => updateManualInput(url, "weight_g", event.target.value)} />
                          </label>
                          <label>
                            <span>售价 CNY</span>
                            <input value={values.selling_price_cny ?? ""} inputMode="decimal" onChange={(event) => updateManualInput(url, "selling_price_cny", event.target.value)} />
                          </label>
                        </div>
                        <div className="ozon-reference-category-box">
                          <div className="ozon-reference-category-head">
                            <Tag className="h-4 w-4" />
                            <span>
                              <strong>最终 Ozon 类目</strong>
                              <small>
                                {values.ozon_category_selection
                                  ? `${categoryName(values.ozon_category_selection)} · category ${values.ozon_category_selection.category_id} · type ${values.ozon_category_selection.type_id}`
                                  : "必须选择，否则无法生成可上传商品卡"}
                              </small>
                            </span>
                          </div>
                          <div className="ozon-reference-category-search">
                            <input
                              value={categoryQueryByUrl[url] || ""}
                              onChange={(event) => setCategoryQueryByUrl((current) => ({ ...current, [url]: event.target.value }))}
                              placeholder="搜索中文类目或商品词"
                            />
                            <Button size="sm" variant="secondary" onClick={() => searchCategory(url)} disabled={categoryBusyUrl === url}>
                              {categoryBusyUrl === url ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                              搜索类目
                            </Button>
                          </div>
                          {!!categoryMessageByUrl[url] && <div className="ozon-reference-category-message">{categoryMessageByUrl[url]}</div>}
                          {!!categoryResultsByUrl[url]?.length && (
                            <div className="ozon-reference-category-results">
                              {categoryResultsByUrl[url].slice(0, 6).map((item) => (
                                <button
                                  key={`${item.category_id}-${item.type_id}`}
                                  type="button"
                                  className={values.ozon_category_selection?.category_id === item.category_id && values.ozon_category_selection?.type_id === item.type_id ? "selected" : ""}
                                  onClick={() => chooseCategory(url, item)}
                                >
                                  <span>
                                    <strong>{categoryName(item)}</strong>
                                    <small>{categoryPath(item)}</small>
                                  </span>
                                  <Badge variant="muted">type {item.type_id}</Badge>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                        {!!missingLabels.length && (
                          <div className="ozon-reference-category-message warning">
                            开始前必须补齐：{missingLabels.join("、")}
                          </div>
                        )}
                        <div className="ozon-reference-fitkun-box">
                          <div className="ozon-reference-category-head">
                            <ImagePlus className="h-4 w-4" />
                            <span>
                              <strong>FITKUN 图片</strong>
                              <small>推荐先用 FITKUN 下载图片，再在这里选择文件；只粘贴 URL 时可能受 Ozon 防盗链影响。</small>
                            </span>
                            <Badge variant={(fitkunImagesByUrl[url]?.length || fitkunUrlTextByUrl[url]) ? "default" : "muted"}>
                              {(fitkunImagesByUrl[url]?.length || 0) + parseImageUrls(fitkunUrlTextByUrl[url] || "").length} 张
                            </Badge>
                          </div>
                          <div className="ozon-reference-fitkun-actions">
                            <label className="ozon-reference-file-button">
                              <Upload className="h-3.5 w-3.5" />
                              选择 FITKUN 下载图片
                              <input
                                type="file"
                                accept="image/*"
                                multiple
                                onChange={(event) => {
                                  addFitkunFiles(url, event.currentTarget.files);
                                  event.currentTarget.value = "";
                                }}
                              />
                            </label>
                          </div>
                          {!!fitkunImagesByUrl[url]?.length && (
                            <div className="ozon-reference-fitkun-list">
                              {(fitkunImagesByUrl[url] || []).map((image, imageIndex) => (
                                <div key={`${image.name || image.url}-${imageIndex}`} className="ozon-reference-fitkun-item">
                                  {image.data_url ? <img src={image.data_url} alt={image.name || "FITKUN 图片"} /> : <span />}
                                  <small>{image.name || `图片 ${imageIndex + 1}`}</small>
                                  <button type="button" onClick={() => removeFitkunImage(url, imageIndex)} aria-label="移除图片">
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                          <label className="ozon-reference-field compact">
                            <span>备用：粘贴 FITKUN 图片 URL</span>
                            <textarea
                              value={fitkunUrlTextByUrl[url] || ""}
                              onChange={(event) => {
                                setLastResult(null);
                                setFitkunUrlTextByUrl((current) => ({ ...current, [url]: event.target.value }));
                              }}
                              placeholder="每行一个图片 URL；文件导入更稳定"
                            />
                          </label>
                        </div>
                      </div>
                    );
                  })}
                </section>
              )}

              <div className="launcher-grid">
                {(shops || []).map((shop) => {
                  const available = isConnectedShop(shop);
                  return (
                    <button
                      key={shop.id}
                      type="button"
                      className={cn("launcher-shop", selectedStores.includes(shop.id) && "selected", !available && "disabled")}
                      disabled={!available}
                      onClick={() => toggleStore(shop.id)}
                    >
                      <Store className="h-5 w-5" />
                      <span>
                        <strong>{shop.display_name || shop.id}</strong>
                        <small>{shop.connection_status || "unknown"} · {shop.credentials_display || "credentials hidden"}</small>
                      </span>
                      <Badge variant={available ? "default" : "warning"}>{available ? "可用" : "不可用"}</Badge>
                    </button>
                  );
                })}
                {!(shops || []).length && <div className="launcher-empty">没有店铺配置</div>}
              </div>

              {!!visibleTasks.length && (
                <section className="ozon-reference-existing">
                  <div className="panel-kicker">最近参考任务</div>
                  {visibleTasks.map((task) => (
                    <div key={task.task_id} className="ozon-reference-task">
                      <Link2 className="h-4 w-4" />
                      <span>
                        <strong>{task.reference_title || task.source_url}</strong>
                        <small>
                          {task.task_id} · {taskStatusLabel(task)}
                          {task.fitkun_image_count ? ` · FITKUN ${task.fitkun_image_count} 张` : ""}
                          {task.captured_image_count ? ` · 图片 ${task.captured_image_count} 张` : ""} · 不提交库存
                        </small>
                        {!!task.missing_fields?.length && (
                          <small>还缺：{task.missing_fields.join("、")}</small>
                        )}
                      </span>
                      {task.created_product_id && (
                        <Button
                          variant="secondary"
                          size="sm"
                          className="ozon-reference-product-button"
                          onClick={() => {
                            onOpenChange(false);
                            onOpenProduct(task.created_product_id || "");
                          }}
                        >
                          <FileText className="h-3.5 w-3.5" />
                          查看商品草稿
                        </Button>
                      )}
                      {!task.created_product_id && !!task.missing_fields?.length && (
                        <Button variant="secondary" size="sm" onClick={() => editExistingTask(task)}>
                          <Tag className="h-3.5 w-3.5" />
                          补参数
                        </Button>
                      )}
                      {!task.created_product_id && !task.missing_fields?.length && (
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={busy}
                          onClick={async () => {
                            setBusy(true);
                            setLocalError("");
                            try {
                              await onContinueQueue();
                              await onRefresh();
                              setLastResultText("队列已继续，后台正在生成商品卡。");
                            } catch (err) {
                              setLocalError(err instanceof Error ? err.message : "继续队列失败");
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
                          继续队列
                        </Button>
                      )}
                    </div>
                  ))}
                </section>
              )}
            </div>
          )}
        </ScrollArea>

        <div className="launcher-actions">
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={busy}>
            关闭
          </Button>
          <Button onClick={submit} disabled={!links.length || !selectedStores.length || busy || missingInputCount > 0}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
            {missingInputCount > 0 ? "先补齐字段" : editingTaskId ? "保存参数并继续" : "加入自动队列"}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
