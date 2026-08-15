#!/usr/bin/env python3
"""Enforce product and collection isolation for formal production inputs.

Formal production may consume only one workbench collection stored below the
current ``products/<product_id>/input`` directory.  Manual conversation assets,
test fixtures, archived products, another product's files and every generated
output are intentionally rejected instead of being matched or silently reused.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


PRODUCT_ID_RE = re.compile(r"^P[0-9]{6}$")
FORMAL_PRODUCT_ID_MAX = 899999
COLLECTION_ID_RE = re.compile(r"^COL-[A-Za-z0-9._-]{8,80}$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}


class ProductionInputError(RuntimeError):
    """A formal product attempted to use data outside its collection boundary."""


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def project_root_for(product_dir: Path) -> Path:
    resolved = product_dir.resolve()
    if resolved.parent.name != "products":
        raise ProductionInputError(
            "正式生产商品必须位于 products/<product_id>；test-data 和手动测试素材禁止进入生产"
        )
    return resolved.parent.parent


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    allowed = root.resolve()
    return resolved == allowed or allowed in resolved.parents


def resolve_project_path(product_dir: Path, value: Any) -> Path:
    raw = Path(str(value or "").strip())
    root = project_root_for(product_dir)
    if raw.is_absolute():
        return raw.resolve()
    # Designer prompts intentionally use concise references such as
    # ``input/source.json#/skus``.  They are scoped to the current product,
    # while project-relative ``products/P000123/...`` references remain valid.
    if raw.parts and raw.parts[0] in {"input", "output"}:
        return (product_dir / raw).resolve()
    return (root / raw).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    """Public, streaming SHA256 helper used by frozen batch bindings."""
    return _sha256(path)


def source_snapshot_binding(product_dir: Path) -> Dict[str, str]:
    """Return the immutable collection identity frozen into a production batch."""
    source_path = product_dir / "input/source.json"
    manifest_path = product_dir / "input/source-manifest.json"
    if not source_path.is_file() or not manifest_path.is_file():
        raise ProductionInputError("正式商品缺少来源文件或来源清单，无法冻结批次")
    source = load_json(source_path)
    return {
        "product_id": product_dir.name,
        "collection_id": str(source.get("collection_id") or ""),
        "source_manifest_path": _relative_to_project(product_dir, manifest_path),
        "source_manifest_sha256": _sha256(manifest_path),
    }


def validate_source_snapshot_binding(product_dir: Path, binding: Dict[str, Any]) -> None:
    current = source_snapshot_binding(product_dir)
    for field in ("product_id", "collection_id", "source_manifest_path", "source_manifest_sha256"):
        if str(binding.get(field) or "") != str(current.get(field) or ""):
            raise ProductionInputError(
                "本次批次冻结的采集来源已变化；禁止原地补资料，请重新采集并创建新采集版本"
            )


def _relative_to_project(product_dir: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_root_for(product_dir)))


def build_source_manifest(product_dir: Path) -> Dict[str, Any]:
    """Build the immutable provenance list for the current collection only.

    The manifest is deliberately derived from files already written by the
    collector.  It never searches another product, a test-data directory or an
    output directory for a replacement.
    """
    source_path = product_dir / "input/source.json"
    if not source_path.is_file():
        raise ProductionInputError("生成采集来源清单前必须先写入 input/source.json")
    source = load_json(source_path)
    product_id = str(source.get("product_id") or "")
    collection_id = str(source.get("collection_id") or "")
    collected_at = str(source.get("collected_at") or source.get("captured_at") or "")
    if product_id != product_dir.name or source.get("source_kind") != "workbench_collection":
        raise ProductionInputError("只能为当前工作台采集商品生成生产来源清单")
    records: List[Dict[str, str]] = []
    candidates = [
        source_path,
        product_dir / "input/raw-snapshot.json",
        product_dir / "input/category-selection.json",
    ]
    for directory in ("sku-images", "main-images", "detail-images"):
        candidates.extend(sorted((product_dir / "input" / directory).glob("*")))
    for path in candidates:
        if not path.is_file():
            continue
        validate_current_product_path(product_dir, path, area="input")
        records.append({
            "record_id": hashlib.sha256(_relative_to_project(product_dir, path).encode("utf-8")).hexdigest()[:20],
            "product_id": product_id,
            "collection_id": collection_id,
            "source_kind": "workbench_collection",
            "source_path": _relative_to_project(product_dir, path),
            "sha256": _sha256(path),
            "collected_at": collected_at,
        })
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "records": records,
    }


def write_source_manifest(product_dir: Path) -> Dict[str, Any]:
    value = build_source_manifest(product_dir)
    path = product_dir / "input/source-manifest.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    return value


def validate_current_product_path(
    product_dir: Path,
    value: Any,
    *,
    area: str = "input",
    require_exists: bool = True,
) -> Path:
    path = resolve_project_path(product_dir, value)
    allowed = (product_dir / area).resolve()
    if not _inside(path, allowed):
        raise ProductionInputError(
            f"{value} 不属于当前商品 {product_dir.name} 的 {area}；禁止跨商品、测试区、归档商品或输入输出互相补资料"
        )
    if require_exists and not path.is_file():
        raise ProductionInputError(f"当前商品采集文件不存在：{value}")
    return path


def validate_current_product_trace_ref(product_dir: Path, value: Any) -> str:
    """Validate a design/analysis provenance reference for this exact product.

    Public URLs may be evidence.  Local references must stay in the current
    product, may never point at generated/rejected/accepted images, and may not
    come from a manual-test tree.
    """
    text = str(value or "").strip()
    if text.startswith(("https://", "http://")):
        return text
    # Provenance references often point at a JSON document plus an internal
    # fragment, for example ``products/P000015/input/source.json#/skus/0``.
    # Filesystem boundary checks must be applied to the file portion only; the
    # fragment is evidence detail, not part of the path.
    local_path_text = text.split("#", 1)[0]
    path = resolve_project_path(product_dir, local_path_text)
    assert_not_test_path(path)
    current = product_dir.resolve()
    if not _inside(path, current):
        raise ProductionInputError(f"来源引用不属于当前 product_id + collection_id：{value}")
    forbidden = (
        product_dir / "output/generated-images",
        product_dir / "output/rejected-generation",
        product_dir / "output/accepted-images",
    )
    if any(_inside(path, root) for root in forbidden):
        raise ProductionInputError(f"AI输出图片不得回流为商品事实或规划输入：{value}")
    if not path.is_file():
        raise ProductionInputError(f"来源引用文件不存在：{value}")
    return text


def validate_registered_input_file(product_dir: Path, value: Any) -> Path:
    """Require a local input file to be part of this collection manifest."""
    path = validate_current_product_path(product_dir, value, area="input")
    manifest_path = product_dir / "input/source-manifest.json"
    if not manifest_path.is_file():
        raise ProductionInputError("正式商品缺少本次采集来源清单")
    relative = _relative_to_project(product_dir, path)
    record = next(
        (item for item in (load_json(manifest_path).get("records") or []) if item.get("source_path") == relative),
        None,
    )
    if not record:
        raise ProductionInputError(f"文件未登记在当前 product_id + collection_id：{value}")
    if str(record.get("sha256") or "").lower() != _sha256(path):
        raise ProductionInputError(f"文件哈希不属于当前采集快照：{value}")
    return path


def _image_records(source: Dict[str, Any]) -> Iterable[tuple[str, Dict[str, Any]]]:
    for group, values in (("main-images", source.get("main_images")), ("detail-images", source.get("detail_images"))):
        for item in values or []:
            if isinstance(item, dict):
                yield group, item
    for sku in source.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        for field in ("local_image_path", "variant_local_image_path"):
            value = str(sku.get(field) or "").strip()
            if value and value != "unknown":
                yield "sku-images", {
                    "local_path": value,
                    "sha256": (sku.get("source_data") or {}).get("image_sha256") or "unknown",
                    "record_name": f"SKU {sku.get('sku_id') or 'unknown'} {field}",
                }


def validate_formal_product_input(product_dir: Path) -> Dict[str, Any]:
    """Validate one formal workbench product before any analysis or generation."""
    product_dir = product_dir.resolve()
    project_root_for(product_dir)
    if not PRODUCT_ID_RE.fullmatch(product_dir.name):
        raise ProductionInputError("正式商品ID格式错误")
    if int(product_dir.name[1:]) > FORMAL_PRODUCT_ID_MAX:
        raise ProductionInputError("P900000-P999999 为测试/审计保留编号，禁止进入正式生产、批次或上传")
    source_path = product_dir / "input/source.json"
    status_path = product_dir / "status.json"
    if not source_path.is_file() or not status_path.is_file():
        raise ProductionInputError("正式商品缺少 input/source.json 或 status.json")
    source = load_json(source_path)
    status = load_json(status_path)
    if str(source.get("product_id") or "") != product_dir.name:
        raise ProductionInputError("source.json 的 product_id 与当前商品目录不一致")
    if source.get("source_kind") != "workbench_collection":
        raise ProductionInputError(
            "正式生产只接受 source_kind=workbench_collection；manual_test/对话附件只能用于独立测试"
        )
    collection_id = str(source.get("collection_id") or "")
    if not COLLECTION_ID_RE.fullmatch(collection_id):
        raise ProductionInputError("正式商品缺少有效 collection_id")
    expected_source = f"products/{product_dir.name}/input/source.json"
    if str(source.get("source_path") or "") != expected_source:
        raise ProductionInputError("source_path 与当前商品本次采集目录不一致")
    if not str(source.get("collected_at") or "").strip():
        raise ProductionInputError("正式商品缺少 collected_at")
    if str(status.get("status") or "").upper() in {"ARCHIVED", "ABANDONED"}:
        raise ProductionInputError("归档商品不能作为新商品的输入或补图来源")

    manifest_path = product_dir / "input/source-manifest.json"
    if not manifest_path.is_file():
        raise ProductionInputError("正式商品缺少本次采集的 input/source-manifest.json")
    manifest = load_json(manifest_path)
    if (
        manifest.get("product_id") != product_dir.name
        or manifest.get("collection_id") != collection_id
        or manifest.get("source_kind") != "workbench_collection"
    ):
        raise ProductionInputError("采集来源清单与当前 product_id + collection_id 不一致")
    records = manifest.get("records") or []
    if not records:
        raise ProductionInputError("采集来源清单为空")
    record_by_path: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if (
            record.get("product_id") != product_dir.name
            or record.get("collection_id") != collection_id
            or record.get("source_kind") != "workbench_collection"
            or record.get("collected_at") != source.get("collected_at")
        ):
            raise ProductionInputError("采集来源记录混入了其他商品、其他批次或测试素材")
        value = str(record.get("source_path") or "")
        path = validate_current_product_path(product_dir, value, area="input")
        if str(record.get("sha256") or "").lower() != _sha256(path):
            raise ProductionInputError(f"采集来源文件已变化或不属于本次采集：{value}")
        record_by_path[value] = record
    for required_path in (
        f"products/{product_dir.name}/input/source.json",
        f"products/{product_dir.name}/input/raw-snapshot.json",
        f"products/{product_dir.name}/input/category-selection.json",
    ):
        if required_path not in record_by_path:
            raise ProductionInputError(f"采集来源清单缺少必要记录：{required_path}")

    raw_capture = str(source.get("raw_capture_file") or "")
    validate_current_product_path(product_dir, raw_capture, area="input")
    checked: List[str] = []
    for expected_dir, record in _image_records(source):
        value = str(record.get("local_path") or "")
        if not value or value == "unknown":
            continue
        path = validate_current_product_path(product_dir, value, area=f"input/{expected_dir}")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ProductionInputError(f"采集图片类型不受支持：{value}")
        recorded_hash = str(record.get("sha256") or "unknown").lower()
        if recorded_hash not in {"", "unknown"} and recorded_hash != _sha256(path):
            raise ProductionInputError(f"采集图片哈希与本次工作台记录不一致：{value}")
        relative = _relative_to_project(product_dir, path)
        if relative not in record_by_path:
            raise ProductionInputError(f"采集图片未登记在当前 collection_id 的来源清单：{value}")
        checked.append(str(path))
    frozen = status.get("source_snapshot_binding")
    if isinstance(frozen, dict) and frozen:
        validate_source_snapshot_binding(product_dir, frozen)
    return {
        "product_id": product_dir.name,
        "collection_id": collection_id,
        "source_kind": "workbench_collection",
        "checked_image_records": len(checked),
        "source_path": expected_source,
    }


def assert_not_test_path(path: Path) -> None:
    parts = {part.casefold() for part in path.resolve().parts}
    if "test-data" in parts or "manual-input" in parts or "manual-output" in parts:
        raise ProductionInputError("manual_test 路径禁止进入正式商品生产")
