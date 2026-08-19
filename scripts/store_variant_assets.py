#!/usr/bin/env python3
"""Persist and verify store-scoped image assets for multi-store listings.

The product master keeps source facts shared.  A store variant is allowed to
change only commercial copy and visual direction, so its generated image plan,
files and QC evidence must travel together.  This module deliberately has no
Ozon client and never performs an API write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from pipeline_runtime import load_json, now, write_json_atomic
    from ozon_ecommerce_designer_contract import materialize, store_variant_design
    from store_publications import load_publications
except ModuleNotFoundError:  # Imported as scripts.store_variant_assets in tests.
    from scripts.pipeline_runtime import load_json, now, write_json_atomic
    from scripts.ozon_ecommerce_designer_contract import materialize, store_variant_design
    from scripts.store_publications import load_publications


ROOT = Path(__file__).resolve().parents[1]
ASSET_FILES = ("image-plan.json", "image-qc-report.json", "image-hard-gate.json")
# Store 1 is the user's designated primary listing lane.  Keep the remaining
# stores deterministic so resumptions cannot silently switch the reference
# image set that is staged first.
STORE_PRIMARY_ORDER = {"zhonglian1": 0, "zhonglian2": 1, "zhonglian5": 2, "jlc-blobal-6": 3}


def safe_store_id(store_id: str) -> str:
    value = str(store_id or "").strip()
    if not value or any(token in value for token in ("/", "\\", "..")):
        raise ValueError("Invalid local store id")
    return value


def variant_asset_dir(product_dir: Path, store_id: str) -> Path:
    return product_dir / "output" / "store-variants" / safe_store_id(store_id)


def selected_store_ids(product_dir: Path) -> List[str]:
    publications = load_publications(product_dir)
    stores = [
        str(store_id)
        for store_id, item in (publications.get("stores") or {}).items()
        if isinstance(item, dict) and item.get("selected") is True
    ]
    return sorted(stores, key=lambda store_id: (STORE_PRIMARY_ORDER.get(store_id, 100), store_id))


def has_store_variants(product_dir: Path) -> bool:
    path = product_dir / "output" / "ozon-ecommerce-design.json"
    if not path.is_file():
        return False
    try:
        return len(selected_store_ids(product_dir)) > 1 and bool(load_json(path).get("store_variants"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _copytree_replace(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    if source.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _planned_outputs(product_dir: Path, plan: Dict[str, Any]) -> List[Path]:
    outputs: List[Path] = []
    for section in ("main_images", "detail_images", "disclaimer_images"):
        for item in plan.get(section) or []:
            raw = str(item.get("output_path") or "").strip()
            marker = f"products/{product_dir.name}/"
            relative = raw.split(marker, 1)[1] if marker in raw else raw
            candidate = (product_dir / relative).resolve()
            if not candidate.is_relative_to((product_dir / "output").resolve()):
                raise ValueError(f"图位输出路径越界：{raw}")
            outputs.append(candidate)
    return outputs


def _asset_manifest(product_dir: Path, store_id: str, asset_dir: Path) -> Dict[str, Any]:
    plan = load_json(asset_dir / "image-plan.json")
    output_root = asset_dir / "generated-images"
    files = []
    for source in _planned_outputs(product_dir, plan):
        relative = source.relative_to((product_dir / "output" / "generated-images").resolve())
        staged = output_root / relative
        if not staged.is_file():
            raise ValueError(f"店铺 {store_id} 缺少图片资产：{relative}")
        files.append({"path": str(relative), "sha256": _sha256(staged)})
    report = load_json(asset_dir / "image-qc-report.json")
    if report.get("critical_failures"):
        raise ValueError(f"店铺 {store_id} 图片质检存在硬错误")
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "store_id": store_id,
        "status": "PASS",
        "image_count": len(files),
        "files": files,
        "created_at": now(),
    }


def stage_variant_assets(product_dir: Path, store_id: str, source_product_dir: Path | None = None) -> Path:
    """Copy one fully checked product image set into a durable store asset set."""
    source = (source_product_dir or product_dir).resolve()
    output = source / "output"
    for name in ASSET_FILES:
        if not (output / name).is_file():
            raise ValueError(f"店铺 {store_id} 缺少已验证图片资料：{name}")
    asset_dir = variant_asset_dir(product_dir, store_id)
    asset_dir.mkdir(parents=True, exist_ok=True)
    for name in ASSET_FILES:
        shutil.copy2(output / name, asset_dir / name)
    _copytree_replace(output / "generated-images", asset_dir / "generated-images")
    _copytree_replace(output / "image-slot-results", asset_dir / "image-slot-results")
    design_path = product_dir / "output" / "ozon-ecommerce-design.json"
    design = load_json(design_path)
    write_json_atomic(asset_dir / "ozon-ecommerce-design.json", store_variant_design(design, store_id))
    manifest = _asset_manifest(product_dir, store_id, asset_dir)
    write_json_atomic(asset_dir / "asset-manifest.json", manifest)
    return asset_dir


def verify_variant_assets(product_dir: Path, store_id: str) -> Dict[str, Any]:
    asset_dir = variant_asset_dir(product_dir, store_id)
    manifest_path = asset_dir / "asset-manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"店铺 {store_id} 尚未生成独立图片资产，已阻止上传")
    manifest = load_json(manifest_path)
    if manifest.get("product_id") != product_dir.name or manifest.get("store_id") != store_id:
        raise ValueError(f"店铺 {store_id} 图片资产归属不一致，已阻止上传")
    verified = _asset_manifest(product_dir, store_id, asset_dir)
    expected = {str(item.get("path")): str(item.get("sha256")) for item in manifest.get("files") or []}
    actual = {str(item.get("path")): str(item.get("sha256")) for item in verified.get("files") or []}
    if expected != actual or manifest.get("status") != "PASS":
        raise ValueError(f"店铺 {store_id} 图片资产已变化或不完整，已阻止上传")
    return manifest


def apply_variant_assets_to_isolated(product_dir: Path, isolated_product_dir: Path, store_id: str) -> None:
    """Install only the selected store's checked assets into an upload workspace."""
    verify_variant_assets(product_dir, store_id)
    source = variant_asset_dir(product_dir, store_id)
    output = isolated_product_dir / "output"
    for name in ASSET_FILES:
        shutil.copy2(source / name, output / name)
    _copytree_replace(source / "generated-images", output / "generated-images")
    _copytree_replace(source / "image-slot-results", output / "image-slot-results")


def _workspace(product_dir: Path, store_id: str) -> Path:
    return ROOT / "runtime" / "store-variant-image-workspaces" / product_dir.name / safe_store_id(store_id) / "products" / product_dir.name


def _clear_workspace_image_outputs(product_dir: Path) -> None:
    output = product_dir / "output"
    for name in ("generated-images", "image-slot-results"):
        path = output / name
        if path.exists():
            shutil.rmtree(path)
    for name in ("image-plan.json", "image-qc-report.json", "image-hard-gate.json", "image-regeneration-request.json"):
        (output / name).unlink(missing_ok=True)


def _workspace_can_resume(product_dir: Path, workspace: Path, store_id: str) -> bool:
    """Resume only a matching isolated store workspace.

    A finished slot already has an image receipt and hard-gate result.  Dropping
    that state after one transient image failure wastes the entire store run.
    """
    try:
        if not (workspace / "input/source.json").is_file() or not (workspace / "output/image-plan.json").is_file():
            return False
        if _sha256(workspace / "input/source.json") != _sha256(product_dir / "input/source.json"):
            return False
        active = (load_json(workspace / "output/ozon-ecommerce-design.json").get("active_store_variant") or {})
        return str(active.get("store_id") or "") == str(store_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def generate_variant_assets(product_dir: Path, store_id: str, settings: Dict[str, Any]) -> Path:
    """Generate one non-primary store visual set, then stage it after QC.

    This uses the existing image worker exclusively and does not contact Ozon.
    """
    try:
        from run_batch import run_parallel_image_generation
        from image_planner import build_image_plan
    except ModuleNotFoundError:
        from scripts.run_batch import run_parallel_image_generation
        from scripts.image_planner import build_image_plan
    workspace = _workspace(product_dir, store_id)
    root = workspace.parent.parent
    if not _workspace_can_resume(product_dir, workspace, store_id):
        if root.exists():
            shutil.rmtree(root)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(product_dir, workspace)
        _clear_workspace_image_outputs(workspace)
        design = load_json(workspace / "output" / "ozon-ecommerce-design.json")
        materialize(workspace, store_variant_design(design, store_id))
        plan = build_image_plan(
            workspace,
            load_json(workspace / "input" / "source.json"),
            load_json(workspace / "output" / "product-analysis.json"),
        )
        write_json_atomic(workspace / "output" / "image-plan.json", plan)
    log = workspace / "logs" / "store-variant-images.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    result = run_parallel_image_generation(workspace, settings, log)
    if result.get("failed") or result.get("service_unavailable") or result.get("prelaunch_failure"):
        raise RuntimeError(f"店铺 {store_id} 图片生成未完成：{result}")
    for args in (("--hard-gate", "--write"), ("--verify-report",)):
        command = [sys.executable, str(ROOT / "scripts/image_qc.py"), str(workspace), *args]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"店铺 {store_id} 图片质检失败：{completed.stderr or completed.stdout}")
    return stage_variant_assets(product_dir, store_id, workspace)


def prepare_all_variant_assets(product_dir: Path, settings: Dict[str, Any]) -> Dict[str, Any]:
    stores = selected_store_ids(product_dir)
    if len(stores) <= 1 or not has_store_variants(product_dir):
        return {"mode": "single_store", "stores": []}
    # image_plan projects the first selected store into the master image lane;
    # preserve that checked output, then generate each remaining store in an
    # isolated image workspace so no store can reuse another's picture set.
    staged = []
    for store_id in stores:
        try:
            verify_variant_assets(product_dir, store_id)
            staged.append(str(variant_asset_dir(product_dir, store_id)))
        except ValueError:
            if store_id == stores[0]:
                staged.append(str(stage_variant_assets(product_dir, store_id)))
            else:
                staged.append(str(generate_variant_assets(product_dir, store_id, settings)))
    return {"mode": "store_variants", "stores": stores, "asset_dirs": staged}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify per-store image assets without Ozon API writes.")
    parser.add_argument("product_dir")
    parser.add_argument("--prepare", action="store_true", help="stage primary and generate remaining store assets")
    parser.add_argument("--verify", action="store_true", help="verify existing store image assets only")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    stores = selected_store_ids(product_dir)
    if args.prepare:
        settings_path = ROOT / "config" / "pipeline-settings.json"
        print(json.dumps(prepare_all_variant_assets(product_dir, load_json(settings_path)), ensure_ascii=False, indent=2))
    elif args.verify:
        print(json.dumps([verify_variant_assets(product_dir, store) for store in stores], ensure_ascii=False, indent=2))
    else:
        parser.error("choose --prepare or --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
