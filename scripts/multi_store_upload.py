#!/usr/bin/env python3
"""Execute one product independently for each selected local Ozon store.

The shared product master is never duplicated in the workbench. Each store gets
an isolated runtime workspace and its own persisted result artifacts so one
failure cannot overwrite another store's task, product, or idempotency data.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping as RuntimeMapping
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
import re

try:
    from pipeline_runtime import load_json, normalize_checkpoint, now, write_json_atomic
    from store_publications import ensure_store_offer_ids, is_store_offer_id, load_publications, save_publications
    from task_database import cutover_active
    from workbench_stores import load_registry, mark_store_validation_failed
    from store_cluster_profiles import profile_from_store
    from ozon_ecommerce_designer_contract import materialize as materialize_design, store_variant_design
    from store_variant_assets import apply_variant_assets_to_isolated, has_store_variants
except ModuleNotFoundError:  # Imported as scripts.multi_store_upload by tests/tools.
    from scripts.pipeline_runtime import load_json, normalize_checkpoint, now, write_json_atomic
    from scripts.store_publications import ensure_store_offer_ids, is_store_offer_id, load_publications, save_publications
    from scripts.task_database import cutover_active
    from scripts.workbench_stores import load_registry, mark_store_validation_failed
    from scripts.store_cluster_profiles import profile_from_store
    from scripts.ozon_ecommerce_designer_contract import materialize as materialize_design, store_variant_design
    from scripts.store_variant_assets import apply_variant_assets_to_isolated, has_store_variants


ROOT = Path(__file__).resolve().parents[1]
PENDING_STATES = {"SUBMITTED", "QUEUED", "UPLOADING", "PENDING_REMOTE", "OZON_MODERATION"}
SUCCESS_STATES = {"SUCCESS", "IMPORTED", "UPLOADED", "ACTIVE"}
HANDOFF_STATE = "HANDED_OFF_TO_OZON"
RETRYABLE_STATES = {"SELECTED", "FAILED", "QUERY_ERROR"}
ATTENTION_STATES = {"NEEDS_ATTENTION"}
IMAGE_FAILURE_TOKENS = {
    "all_image_failed",
    "some_image_failed",
    "primary_image_load_failed",
    "pics_http_error",
    "pics_reading_timeout",
    "фото",
    "изображ",
    "картинк",
    "图片",
}
VARIANT_FAILURE_TOKENS = {
    "spu_already_exists_in_another_account",
    "double_without_merger_offer",
    "spu_already_exists_hint",
    "duplicate",
    "duplicates",
    "дублируется",
    "вариатив",
    "merge",
    "merger",
}
OZON_ISSUE_BUCKETS: Dict[str, Dict[str, Any]] = {
    "image_link": {
        "label": "图片链接失败",
        "action": "repair_images",
        "codes": {
            "all_image_failed",
            "some_image_failed",
            "primary_image_load_failed",
            "pics_http_error",
            "pics_internal",
            "pics_reading_timeout",
            "pics_download_server_unavailable",
        },
        "tokens": {"photo", "image", "picture", "фото", "изображ", "картинк", "图片", "照片"},
        "message": "Ozon 下载图片失败：不重新生图，只重新上传图片到可访问链接后重传图片。",
    },
    "numeric_contract": {
        "label": "字段数值格式错误",
        "action": "repair_attributes",
        "codes": {"value_must_be_decimal", "value_min_limit"},
        "tokens": {"value_must_be_decimal", "value_min_limit", "decimal", "min_limit", "数值格式"},
        "message": "Ozon 字段数值不合规：重新编译属性并更新商品卡，不需要重做图片。",
    },
    "logistics_weight": {
        "label": "体积重量异常",
        "action": "repair_measurements",
        "codes": {"ml_incorrect_volume_weight"},
        "tokens": {"volume_weight", "вес", "重量", "体积"},
        "message": "Ozon 认为体积重量异常：按采集资料和视觉事实修正包装尺寸重量后更新。",
    },
    "duplicate_spu": {
        "label": "重复或变体合并问题",
        "action": "repair_duplicate",
        "codes": {"double_without_merger_offer", "spu_already_exists_in_another_account"},
        "tokens": {"duplicate", "merger", "merge", "spu", "дублируется", "重复", "合并", "变体"},
        "message": "Ozon 认为商品重复或需要合并：走重复/变体修复，不要重新创建第二张商品卡。",
    },
    "description_decline": {
        "label": "简介审核失败",
        "action": "repair_description",
        "codes": {"description_decline"},
        "tokens": {"description", "описан", "简介", "描述"},
        "message": "Ozon 拒绝了简介：只修正文案，不动图片和已通过字段。",
    },
    "category_mismatch": {
        "label": "类目不匹配",
        "action": "repair_category",
        "codes": {"category_mismatch", "category_incorrect"},
        "tokens": {"category does not match", "категория не соответствует", "выбранная категория", "类目不匹配", "类目不对应"},
        "message": "Ozon 认为类目和商品不匹配：重新选择正确类目并编译字段，不要重做图片。",
    },
    "store_auth": {
        "label": "店铺授权失败",
        "action": "repair_store",
        "codes": {"unauthorized", "forbidden"},
        "tokens": {"api-key", "api key", "unauthorized", "forbidden", "deactivated", "授权失败"},
        "message": "店铺授权失败：检查店铺 API 配置后再继续，不会重复提交商品。",
    },
}
OZON_ISSUE_PRIORITY = (
    "store_auth",
    "duplicate_spu",
    "logistics_weight",
    "category_mismatch",
    "numeric_contract",
    "description_decline",
    "image_link",
    "other",
)
STORE_ARTIFACTS = (
    "ozon-result.json", "ozon-write-receipt.json", "ozon-idempotency.json",
    "ozon-last-upload-hashes.json", "product-exists-check.json",
    "ozon-upload-payload.json", "ozon-images.json", "ozon-image-transfer.json",
    "ozon-preflight.json", "ozon-update-request-summary.json",
    "store-offer-id-map.json",
)
RUSSIAN_HASHTAG_RE = re.compile(r"^#[А-Яа-яЁё]{2,29}$")


def _safe_store_id(value: str) -> str:
    if not value or any(part in value for part in ("/", "\\", "..")):
        raise ValueError("Invalid local store id")
    return value


def store_artifact_dir(product_dir: Path, store_id: str) -> Path:
    return product_dir / "output/store-runs" / _safe_store_id(store_id)


def store_workspace(root: Path, product_id: str, store_id: str) -> Path:
    return root / "runtime/store-upload-workspaces" / product_id / _safe_store_id(store_id)


def project_python(root: Path) -> str:
    candidate = root / ".venv/bin/python"
    return str(candidate) if candidate.is_file() else sys.executable


def _field_completion_build_package():
    sys.path.insert(0, str(ROOT / "ozon-field-completion"))
    from ozon_field_completion import build_package  # noqa: WPS433

    return build_package


def _tags_are_current(product_dir: Path) -> bool:
    tags_path = product_dir / "output/ozon-tags.json"
    if not tags_path.is_file():
        return True
    try:
        tags = load_json(tags_path).get("tags") or []
    except Exception:
        return False
    return (
        isinstance(tags, list)
        and len(tags) <= 30
        and len({str(item).casefold() for item in tags}) == len(tags)
        and all(RUSSIAN_HASHTAG_RE.fullmatch(str(item).strip()) for item in tags)
    )


def _color_policy_blocked(product_dir: Path) -> bool:
    policy_path = product_dir / "output/color-variant-policy.json"
    if not policy_path.is_file():
        return False
    try:
        return str(load_json(policy_path).get("status") or "").upper() == "BLOCK"
    except Exception:
        return True


def ensure_upload_config_exists(product_dir: Path, *, force_refresh: bool = False) -> None:
    """Materialize missing local upload files before any Ozon write can start."""
    config_path = product_dir / "output/ozon-upload-config.json"
    if (
        not force_refresh
        and config_path.is_file()
        and _tags_are_current(product_dir)
        and not _color_policy_blocked(product_dir)
    ):
        return
    try:
        _field_completion_build_package()(product_dir, write=True, pre_image=False)
    except Exception as exc:  # pragma: no cover - exact schema failure is tested upstream
        raise RuntimeError(
            f"本地上传资料缺失或过期，上传前自动重新生成失败：{exc}"
        ) from exc
    if not config_path.is_file():
        raise RuntimeError("本地上传资料缺少 ozon-upload-config.json，已在调用Ozon前停止")
    if not _tags_are_current(product_dir):
        raise RuntimeError("Ozon标签仍不符合当前规则，已在调用Ozon前停止")
    if _color_policy_blocked(product_dir):
        raise RuntimeError("主SKU颜色图仍不满足上传条件，已在调用Ozon前停止")


def _load_env_file(path: Path, environ: Dict[str, str]) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        environ[key.strip()] = value.strip().strip('"').strip("'")


def _process_state(pid: int) -> str:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _image_channel_pids(stop_path: Path) -> list[int]:
    """Find every worker watching this stop file, including older duplicates."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    marker = str(stop_path)
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        if "ozon_uploader.image_channel_worker" not in line or marker not in line:
            continue
        try:
            pid = int(line.strip().split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        if not _process_state(pid).upper().startswith("Z"):
            pids.append(pid)
    return pids


def stop_workspace_image_channels(workspace: Path, wait_seconds: float = 12) -> None:
    """Gracefully close all tunnel workers before deleting their state files."""
    stop_paths = list(workspace.glob("products/P*/output/image-channel.stop"))
    state_paths = list(workspace.glob("products/P*/output/image-channel-state.json"))
    for state_path in state_paths:
        stop_path = state_path.with_name("image-channel.stop")
        if stop_path not in stop_paths:
            stop_paths.append(stop_path)
    for stop_path in stop_paths:
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.write_text("isolated_workspace_rebuild\n", encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        live = [pid for stop_path in stop_paths for pid in _image_channel_pids(stop_path)]
        if not live:
            return
        time.sleep(0.25)
    live = sorted({pid for stop_path in stop_paths for pid in _image_channel_pids(stop_path)})
    if live:
        raise RuntimeError(f"旧图片通道仍在退出中，已阻止删除工作区：{live}")


def _is_merge_model_name_attribute(item: Mapping[str, Any]) -> bool:
    """识别 Ozon「型号名（用于合并 SPU）」属性，而非名称模板里的型号名。

    合并型号名在 Ozon 各分类的 attribute_id 可能不同，9048 只是最常见的一个；
    语义名字（Название модели для объединения в одну карточку）才是稳定判据。
    「Название модели для шаблона наименования」（模板型号名）不用于跨账号合并，
    必须排除，否则多店仍会共用同一模板名。
    """
    try:
        attribute_id = int(item.get("attribute_id") or 0)
    except (TypeError, ValueError):
        attribute_id = 0
    if attribute_id == 9048:
        return True
    name = re.sub(r"[^a-zа-яё0-9]", "", str(item.get("attribute_name") or "").casefold())
    return "названиемоделидляобъединения" in name


def _scope_store_model_name(isolated: Path, store_id: str) -> None:
    """多店上架时给每个店独立的合并型号名，避免 Ozon 判跨账号 SPU 重复。

    Ozon 的「型号名（для объединения в одну карточку）」是跨 SKU/跨账号合并
    SPU 的稳定键。同一商品发多个店时若共用同一型号名，Ozon 会报
    spu_already_exists_in_another_account。给每个店加 store 后缀，让每个店成为
    独立 SPU；店内所有 SKU 仍共享同一型号名，颜色变体照常合并为一张卡。
    """
    attrs_path = isolated / "output" / "ozon-attributes-final.json"
    if not attrs_path.is_file():
        return
    attrs = load_json(attrs_path)
    suffix = f"-{_safe_store_id(store_id)}"
    changed = False
    for key in ("common_attributes", "attributes"):
        for item in attrs.get(key) or []:
            if not _is_merge_model_name_attribute(item):
                continue
            for field in ("value", "canonical_value", "target_value", "ozon_value"):
                value = str(item.get(field) or "").strip()
                if value and value.casefold() not in {"unknown", "none", "null"} and not value.endswith(suffix):
                    item[field] = value + suffix
                    changed = True
    if changed:
        write_json_atomic(attrs_path, attrs)


def prepare_isolated_product(
    root: Path,
    product_dir: Path,
    store_id: str,
    publication: Mapping[str, Any],
    *,
    selected_store_count: int = 1,
) -> Path:
    repair_images = image_repair_retryable(publication)
    repair_variant = variant_repair_retryable(publication)
    force_refresh = (
        str(publication.get("status") or "") in {"FAILED", "QUERY_ERROR"}
        or repair_images
        or repair_variant
    )
    ensure_upload_config_exists(product_dir, force_refresh=force_refresh)
    workspace = store_workspace(root, product_dir.name, store_id)
    isolated = workspace / "products" / product_dir.name
    if workspace.exists():
        stop_workspace_image_channels(workspace)
        shutil.rmtree(workspace)
    isolated.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(product_dir, isolated)
    output = isolated / "output"
    master_design_path = output / "ozon-ecommerce-design.json"
    store_variant_selected = False
    if selected_store_count > 1 and not master_design_path.is_file():
        raise RuntimeError(
            "多店商品缺少店铺独立资料，已阻止复用同一标题、文案或图片提交"
        )
    if master_design_path.is_file():
        master_design = load_json(master_design_path)
        if selected_store_count > 1:
            # A multi-store listing must never silently fall back to the
            # product-master copy or image set.  The original implementation
            # only installed store assets when the design happened to contain
            # variants; a resumed/legacy product could therefore publish the
            # same card to every selected store.  Missing assets are a hard
            # pre-write error, including on image-repair retries.
            if not master_design.get("store_variants"):
                raise RuntimeError(
                    "多店商品缺少店铺独立资料，已阻止复用同一标题、文案或图片提交"
                )
            if not has_store_variants(product_dir):
                raise RuntimeError(
                    "多店商品缺少已验证的店铺独立图片资产，已阻止提交"
                )
            # This is a store-scoped projection inside an isolated workspace.
            # It changes only buyer copy and visual direction; source facts,
            # SKU plan and compiled Ozon attributes stay shared and untouched.
            projected = store_variant_design(master_design, store_id)
            materialize_design(isolated, projected)
            store_variant_selected = True
            # The upload workspace must receive the exact plan, files and QC
            # receipts produced for this store.  A missing manifest is a hard
            # pre-write stop, never a fallback to another store's images.
            apply_variant_assets_to_isolated(product_dir, isolated, store_id)
        elif master_design.get("store_variants"):
            # A single-store retry may still use its store-scoped plan when
            # the source product was previously prepared for a store cluster.
            projected = store_variant_design(master_design, store_id)
            materialize_design(isolated, projected)
            store_variant_selected = has_store_variants(product_dir)
            if store_variant_selected:
                apply_variant_assets_to_isolated(product_dir, isolated, store_id)
    for name in STORE_ARTIFACTS:
        (output / name).unlink(missing_ok=True)
    previous = store_artifact_dir(product_dir, store_id)
    if previous.is_dir():
        for name in STORE_ARTIFACTS:
            source = previous / name
            if source.is_file():
                shutil.copy2(source, output / name)
    status = normalize_checkpoint(load_json(isolated / "status.json"))
    if repair_images:
        status.update({
            "status": "UPLOADED", "current_step": "ozon_upload",
            "next_action": "retry_failed_store", "task_authorized": True,
            "api_write_count": int(publication.get("api_write_count") or 1),
            "ozon": {"upload_status": "failed", "errors": [{"reason": publication.get("last_error") or "图片下载失败"}]},
            "error_code": "STORE_IMAGE_UPLOAD_FAILED",
            "error_message": publication.get("last_error") or "图片下载失败",
            "failed_step": "ozon_upload",
        })
    elif repair_variant:
        status.update({
            "status": "WAITING_MANUAL_REVIEW", "current_step": "field_completion",
            "next_action": "ozon_upload", "task_authorized": True,
            "api_write_count": 0, "ozon": {"upload_status": "not_started", "errors": []},
            "error_code": "unknown", "error_message": "unknown", "failed_step": "unknown",
        })
    else:
        status.update({
            "status": "WAITING_MANUAL_REVIEW", "current_step": "field_completion",
            "next_action": "ozon_upload", "task_authorized": True,
            "api_write_count": 0, "ozon": {"upload_status": "not_started", "errors": []},
            "error_code": "unknown", "error_message": "unknown", "failed_step": "unknown",
        })
    status["completed_steps"] = [step for step in status.get("completed_steps") or [] if step != "ozon_upload"]
    status["pending_steps"] = ["ozon_upload"]
    write_json_atomic(isolated / "status.json", status)
    ensure_upload_config_exists(
        isolated,
        force_refresh=force_refresh or store_variant_selected,
    )
    config_path = output / "ozon-upload-config.json"
    config = load_json(config_path)
    config["shop_name"] = store_id
    shop = next(
        (
            item for item in (load_registry(root).get("shops") or [])
            if str(item.get("id") or "") == store_id
        ),
        {},
    )
    # A profile price is an automatic *suggestion* applied only when this
    # store has no explicit SKU override.  Thus users keep full control over
    # deliberate price edits while every cluster store receives a genuinely
    # distinct commercial card by default.
    price_multiplier = float(profile_from_store(shop).get("price_multiplier") or 1.0)
    prices = {
        str(item.get("sku_id")): item.get("price_override_cny")
        for item in publication.get("sku_publications") or []
        if item.get("price_override_cny") not in {None, "", "unknown"}
    }
    for item in config.get("sku_prices") or []:
        sku_id = str(item.get("source_sku_id"))
        if sku_id in prices:
            item["price"] = f"{float(prices[sku_id]):.2f}"
        elif item.get("price") not in {None, "", "unknown"}:
            item["price"] = f"{float(item['price']) * price_multiplier:.2f}"
    write_json_atomic(config_path, config)
    offer_ids = {
        str(item.get("sku_id")): str(item.get("offer_id"))
        for item in publication.get("sku_publications") or []
        if not _unknown(item.get("offer_id"))
    }
    draft_path = output / "ozon-draft.json"
    if draft_path.is_file():
        draft = load_json(draft_path)
        draft_sku_ids = [str(item.get("source_sku_id")) for item in draft.get("skus") or []]
        missing = [sku_id for sku_id in draft_sku_ids if sku_id not in offer_ids]
        if missing:
            raise RuntimeError(
                f"店铺 {store_id} 缺少SKU专属货号，上传前需要处理：{', '.join(missing)}"
            )
        if len({offer_ids[sku_id] for sku_id in draft_sku_ids}) != len(draft_sku_ids):
            raise RuntimeError(f"店铺 {store_id} 存在重复SKU货号，上传前需要处理")
        for sku in draft.get("skus") or []:
            sku["offer_id"] = offer_ids[str(sku.get("source_sku_id"))]
        if draft_sku_ids:
            draft["offer_id"] = offer_ids[draft_sku_ids[0]]
        write_json_atomic(draft_path, draft)
        grouping_path = output / "variant-grouping-result.json"
        if grouping_path.is_file():
            grouping = load_json(grouping_path)
            for variant in grouping.get("variants") or []:
                sku_id = str(variant.get("sku_id"))
                if sku_id in offer_ids:
                    variant["offer_id"] = offer_ids[sku_id]
            write_json_atomic(grouping_path, grouping)
        generated_mapping = bool(draft_sku_ids) and all(
            is_store_offer_id(offer_ids[sku_id]) for sku_id in draft_sku_ids
        )
        write_json_atomic(output / "store-offer-id-map.json", {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "store_id": store_id,
            "strategy": "store_specific_random_v1" if generated_mapping else "legacy_preserved",
            "requires_create": generated_mapping and not repair_variant,
            "requires_update": repair_variant,
            "requires_image_repair": repair_images,
            "requires_variant_repair": repair_variant,
            "prepared_at": now(),
            "sku_offer_ids": [
                {"sku_id": sku_id, "offer_id": offer_ids[sku_id]}
                for sku_id in draft_sku_ids
            ],
        })
    # 多店上架：每个店用独立的 9048 型号名，避免 Ozon 判跨账号 SPU 重复。
    if selected_store_count > 1:
        _scope_store_model_name(isolated, store_id)
    return isolated


def default_runner(root: Path, isolated: Path, store_id: str) -> Dict[str, Any]:
    env = dict(os.environ, UPLOAD_MODE="production")
    _load_env_file(root / "ozon-adapter" / f".env.{store_id}", env)
    log_path = isolated / "logs/store-upload.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        project_python(root), str(root / "ozon-uploader/cli.py"),
        str(isolated), "--shop", store_id,
    ]
    offer_map_path = isolated / "output/store-offer-id-map.json"
    offer_map = load_json(offer_map_path) if offer_map_path.is_file() else {}
    if offer_map.get("requires_image_repair") is True:
        command.extend(["--repair-images", "--force-image-resubmit"])
    else:
        if offer_map.get("requires_update") is True:
            command.extend(["--require-action", "update"])
        elif offer_map.get("requires_create") is True:
            command.extend(["--require-action", "create"])
        command.append("--execute")
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    status = load_json(isolated / "status.json")
    if completed.returncode and status.get("error_message") in {None, "", "unknown", "UNKNOWN"}:
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        failure = next((line[2:].strip() for line in reversed(lines) if line.startswith("- ")), None)
        status["error_message"] = failure or (lines[-1] if lines else "店铺上传进程失败，未记录具体原因")
    result_path = isolated / "output/ozon-result.json"
    idempotency_path = isolated / "output/ozon-idempotency.json"
    return {
        "returncode": completed.returncode,
        "status": status,
        "result": load_json(result_path) if result_path.is_file() else {},
        "idempotency": load_json(idempotency_path) if idempotency_path.is_file() else {},
    }


def _persist_store_artifacts(product_dir: Path, store_id: str, isolated: Path) -> None:
    target = store_artifact_dir(product_dir, store_id)
    target.mkdir(parents=True, exist_ok=True)
    for name in STORE_ARTIFACTS:
        source = isolated / "output" / name
        if source.is_file():
            shutil.copy2(source, target / name)


def _unknown(value: Any) -> bool:
    return value in {None, "", "unknown", "UNKNOWN"}


def _issue_text(issue: Any) -> str:
    if isinstance(issue, RuntimeMapping):
        return " ".join(
            str(issue.get(key) or "")
            for key in ("code", "field", "attribute_id", "message", "reason", "error", "level")
        ).casefold()
    return str(issue or "").casefold()


def _issue_code(issue: Any) -> str:
    if not isinstance(issue, RuntimeMapping):
        return ""
    return str(issue.get("code") or issue.get("error_code") or "").strip().casefold()


def _issue_severity(issue: Any, default: str = "error") -> str:
    if not isinstance(issue, RuntimeMapping):
        return default
    level = str(issue.get("level") or issue.get("severity") or default).casefold()
    if "warning" in level or "warn" in level:
        return "warning"
    if "error" in level:
        return "error"
    return default


def ozon_issue_bucket(issue: Any) -> str:
    code = _issue_code(issue)
    text = _issue_text(issue)
    for bucket in ("store_auth", "duplicate_spu", "logistics_weight", "category_mismatch", "description_decline", "numeric_contract", "image_link"):
        config = OZON_ISSUE_BUCKETS[bucket]
        if code and code in config["codes"]:
            return bucket
    for bucket, config in OZON_ISSUE_BUCKETS.items():
        if any(token in text for token in config["tokens"]):
            return bucket
    return "other"


def _iter_store_result_issues(product_dir: Path, store_id: str) -> Iterable[Dict[str, Any]]:
    result_path = store_artifact_dir(product_dir, store_id) / "ozon-result.json"
    if not result_path.is_file():
        return []
    result = load_json(result_path)
    issues: List[Dict[str, Any]] = []
    for issue in result.get("errors") or []:
        if isinstance(issue, RuntimeMapping):
            issues.append({"store_id": store_id, "severity": _issue_severity(issue), **dict(issue)})
        else:
            issues.append({"store_id": store_id, "severity": "error", "message": str(issue)})
    for issue in result.get("warnings") or []:
        if isinstance(issue, RuntimeMapping):
            issues.append({"store_id": store_id, "severity": _issue_severity(issue, "warning"), **dict(issue)})
        else:
            issues.append({"store_id": store_id, "severity": "warning", "message": str(issue)})
    for item in result.get("items") or []:
        if not isinstance(item, RuntimeMapping):
            continue
        offer_id = str(item.get("offer_id") or "unknown")
        sku_id = str(item.get("source_sku_id") or item.get("sku_id") or "unknown")
        for issue in item.get("errors") or []:
            if isinstance(issue, RuntimeMapping):
                issues.append({
                    "store_id": store_id,
                    "offer_id": offer_id,
                    "sku_id": sku_id,
                    "severity": _issue_severity(issue),
                    **dict(issue),
                })
            else:
                issues.append({
                    "store_id": store_id,
                    "offer_id": offer_id,
                    "sku_id": sku_id,
                    "severity": "error",
                    "message": str(issue),
                })
    return issues


def _iter_record_issues(record: Mapping[str, Any], store_id: str) -> Iterable[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    record_status = str(record.get("status") or "").upper()
    if record_status in {"FAILED", "QUERY_ERROR"} and not _unknown(record.get("last_error")):
        issues.append({"store_id": store_id, "severity": "error", "message": str(record.get("last_error"))})
    for sku in record.get("sku_publications") or []:
        if not isinstance(sku, RuntimeMapping):
            continue
        base = {
            "store_id": store_id,
            "offer_id": str(sku.get("offer_id") or "unknown"),
            "sku_id": str(sku.get("sku_id") or "unknown"),
        }
        for key, default_severity in (("errors", "error"), ("warnings", "warning")):
            for issue in sku.get(key) or []:
                if isinstance(issue, RuntimeMapping):
                    issues.append({**base, "severity": _issue_severity(issue, default_severity), **dict(issue)})
                else:
                    issues.append({**base, "severity": default_severity, "message": str(issue)})
    return issues


def summarize_ozon_issues(
    product_dir: Path,
    publications: Mapping[str, Any],
) -> Dict[str, Any]:
    """Classify Ozon feedback once so upload, recovery and UI share one answer."""
    deduped: Dict[tuple[str, str, str, str, str], Dict[str, Any]] = {}
    for store_id, record in (publications.get("stores") or {}).items():
        if not record.get("selected"):
            continue
        for issue in [
            *_iter_record_issues(record, str(store_id)),
            *_iter_store_result_issues(product_dir, str(store_id)),
        ]:
            if not _issue_text(issue).strip():
                continue
            bucket = ozon_issue_bucket(issue)
            normalized = {
                **issue,
                "bucket": bucket,
                "bucket_label": OZON_ISSUE_BUCKETS.get(bucket, {}).get("label", "其他 Ozon 返回"),
                "severity": _issue_severity(issue, str(issue.get("severity") or "error")),
            }
            key = (
                str(normalized.get("store_id") or ""),
                str(normalized.get("offer_id") or ""),
                str(normalized.get("code") or normalized.get("error_code") or ""),
                str(normalized.get("field") or normalized.get("attribute_id") or ""),
                _issue_text(normalized)[:180],
            )
            deduped[key] = normalized
    issues = list(deduped.values())
    counts: Dict[str, int] = {}
    severity_counts = {"error": 0, "warning": 0}
    for issue in issues:
        bucket = str(issue.get("bucket") or "other")
        counts[bucket] = counts.get(bucket, 0) + 1
        severity = str(issue.get("severity") or "error")
        severity_counts["warning" if severity == "warning" else "error"] += 1
    primary = next((bucket for bucket in OZON_ISSUE_PRIORITY if counts.get(bucket)), "none")
    primary_config = OZON_ISSUE_BUCKETS.get(primary, {})
    return {
        "schema_version": "1.0.0",
        "has_issues": bool(issues),
        "primary_bucket": primary,
        "primary_label": primary_config.get("label", "其他 Ozon 返回") if issues else "无",
        "primary_action": primary_config.get("action", "inspect_ozon_result") if issues else "none",
        "message": primary_config.get("message", "Ozon 返回了未归类问题，请查看原始结果。") if issues else "无 Ozon 问题",
        "counts": counts,
        "error_count": severity_counts["error"],
        "warning_count": severity_counts["warning"],
        "total": len(issues),
        "samples": issues[:8],
    }


def ozon_issue_message(summary: Mapping[str, Any], fallback: str) -> str:
    if not summary.get("has_issues"):
        return fallback
    return str(summary.get("message") or fallback)


def has_task_without_product(record: Mapping[str, Any]) -> bool:
    skus = list(record.get("sku_publications") or [])
    return any(not _unknown(sku.get("task_id")) for sku in skus) and not any(
        not _unknown(sku.get("ozon_product_id")) for sku in skus
    )


def credential_failure(error: Any) -> bool:
    text = str(error or "").casefold()
    return any(token in text for token in (
        "api-key is deactivated", "api key is deactivated",
        "invalid api-key", "invalid api key", "unauthorized",
    ))


def definitely_retryable(record: Mapping[str, Any]) -> bool:
    if str(record.get("status") or "") not in {"FAILED", "QUERY_ERROR", "SELECTED"}:
        return False
    for sku in record.get("sku_publications") or []:
        if not _unknown(sku.get("task_id")):
            return False
        if str(sku.get("moderation_status") or "").upper() in PENDING_STATES:
            return False
    return True


def image_repair_retryable(record: Mapping[str, Any]) -> bool:
    if str(record.get("status") or "") not in {"FAILED", "QUERY_ERROR"}:
        return False
    skus = list(record.get("sku_publications") or [])
    if not skus:
        return False
    if int(record.get("api_write_count") or 0) <= 0:
        return False
    if not any(not _unknown(sku.get("ozon_product_id")) for sku in skus):
        return False
    parts = [record.get("last_error")]
    for sku in skus:
        parts.extend(sku.get("errors") or [])
        parts.extend(sku.get("warnings") or [])
    haystack = " ".join(str(part) for part in parts if part).casefold()
    return any(token in haystack for token in IMAGE_FAILURE_TOKENS)


def variant_repair_retryable(record: Mapping[str, Any]) -> bool:
    if str(record.get("status") or "") not in {"FAILED", "QUERY_ERROR"}:
        return False
    skus = list(record.get("sku_publications") or [])
    if not skus:
        return False
    if int(record.get("api_write_count") or 0) <= 0:
        return False
    if not any(not _unknown(sku.get("ozon_product_id")) for sku in skus):
        return False
    parts = [record.get("last_error")]
    for sku in skus:
        parts.extend(sku.get("errors") or [])
        parts.extend(sku.get("warnings") or [])
    haystack = " ".join(str(part) for part in parts if part).casefold()
    return bool(record.get("requires_variant_merge")) or any(
        token in haystack for token in VARIANT_FAILURE_TOKENS
    )


def stale_prewrite_pending(record: Mapping[str, Any]) -> bool:
    """A previous local attempt stopped before any Ozon write identity existed."""
    if str(record.get("status") or "") not in PENDING_STATES | {"UPLOADING"}:
        return False
    if int(record.get("api_write_count") or 0) > 0:
        return False
    for sku in record.get("sku_publications") or []:
        if not _unknown(sku.get("task_id")) or not _unknown(sku.get("ozon_product_id")):
            return False
    return True


def _store_result(record: Dict[str, Any], outcome: Mapping[str, Any], increment_version: bool = True) -> None:
    status = dict(outcome.get("status") or {})
    result = dict(outcome.get("result") or {})
    idempotency = dict(outcome.get("idempotency") or {})
    items = [item for item in (result.get("items") or []) if isinstance(item, Mapping)]
    # Ozon can create a card while rejecting one or more image URLs.  That is
    # not a successful publication: the marketplace silently substitutes a
    # fallback picture, which makes SKU cards look identical.  Preserve the
    # remote product IDs, but force the store into the dedicated image-repair
    # path instead of reporting it as uploaded.
    remote_issues = [
        issue
        for item in items
        for issue in [*(item.get("errors") or []), *(item.get("warnings") or [])]
    ] + list(result.get("errors") or []) + list(result.get("warnings") or [])
    remote_image_failure = any(
        ozon_issue_bucket(issue) == "image_link" for issue in remote_issues
    )
    remote_variant_failure = any(
        ozon_issue_bucket(issue) == "duplicate_spu" for issue in remote_issues
    )
    write_count = int(status.get("api_write_count") or 0)
    raw_status = str(status.get("status") or "NEEDS_ATTENTION").upper()
    has_task_id = (
        not _unknown(result.get("task_id"))
        or any(not _unknown(item.get("task_id")) for item in (result.get("items") or []))
    )
    result_failed = str(result.get("status") or "").upper() == "FAILED"
    if (
        remote_image_failure
        or remote_variant_failure
        or raw_status in {"FAILED", "NEEDS_ATTENTION"}
        or result_failed
    ):
        store_status = "FAILED"
    elif raw_status in {"ACTIVE", "UPLOADED"}:
        store_status = "SUCCESS"
    elif has_task_id:
        # A task_id only proves Ozon accepted the async import job.  The card is
        # not confirmed until read-only recovery returns product IDs or a
        # terminal validation error.
        store_status = "PENDING_REMOTE"
    elif raw_status in {"PENDING_REMOTE", "OZON_MODERATION"}:
        store_status = "PENDING_REMOTE"
    elif raw_status in {"SUBMITTED", "UPLOADING"} and has_task_id:
        store_status = HANDOFF_STATE
    elif write_count > 0 and any(not _unknown(item.get("task_id")) for item in (result.get("items") or [])):
        store_status = HANDOFF_STATE
    elif write_count > 0:
        store_status = "PENDING_REMOTE"
    else:
        store_status = "FAILED"
    by_sku = {str(item.get("source_sku_id") or item.get("sku_id") or ""): item for item in items}
    action = str(result.get("action") or result.get("upload_action") or "UNKNOWN").upper()
    status_error_message = status.get("error_message")
    errors = result.get("errors") or (status.get("ozon") or {}).get("errors") or []
    if (remote_image_failure or remote_variant_failure) and not errors:
        errors = remote_issues
    if not errors and raw_status in {"FAILED", "NEEDS_ATTENTION"} and not _unknown(status_error_message):
        errors = [{
            "step": "ozon_upload",
            "reason": str(status_error_message),
            "retryable": write_count == 0,
        }]
    if store_status != "FAILED":
        errors = []
    for sku in record.get("sku_publications") or []:
        item = by_sku.get(str(sku.get("sku_id"))) or (items[0] if len(items) == 1 else {})
        item_action = str(item.get("action") or "UNKNOWN").upper()
        resolved_action = item_action if not _unknown(item_action) else action if not _unknown(action) else str(sku.get("action") or "UNKNOWN").upper()
        sku_issues = [*(item.get("errors") or []), *(item.get("warnings") or [])]
        sku.update({
            "offer_id": item.get("offer_id") or sku.get("offer_id") or "unknown",
            "action": resolved_action,
            "task_id": str(item.get("task_id") or result.get("task_id") or "unknown"),
            "ozon_product_id": str(item.get("product_id") or item.get("ozon_product_id") or "unknown"),
            "payload_hash": idempotency.get("payload_hash") or result.get("payload_hash") or sku.get("payload_hash") or "unknown",
            "moderation_status": store_status.lower(),
            "errors": sku_issues if store_status == "FAILED" else [],
            "warnings": (item.get("warnings") or result.get("warnings") or []),
        })
    record.update({
        "selected": True, "status": store_status,
        "api_write_count": write_count,
        "submission_version": int(record.get("submission_version") or 0) + (1 if increment_version else 0),
        "last_submitted_at": now() if write_count else record.get("last_submitted_at"),
        "last_checked_at": now(),
        "last_error": None if store_status != "FAILED" else (
            "Ozon 无法下载图片直链；需使用稳定的公开图片直链重传图片。"
            if remote_image_failure
            else (
                "Ozon 要求将颜色 SKU 合并为同一商品卡；将保留同一型号名并更新现有卡。"
                if remote_variant_failure
                else result.get("error_message") or status_error_message or "店铺上传失败"
            )
        ),
    })


def aggregate_product_status(
    product_dir: Path,
    publications: Mapping[str, Any],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    raw_status = load_json(product_dir / "status.json")
    # ARCHIVED is a terminal local lifecycle state (user_archived_in_ozon):
    # a later read-only status poll must never resurrect an archived product
    # back into WAITING_MANUAL_REVIEW / PENDING_REMOTE, and normalize_checkpoint
    # must not rewrite its next_action back to a pipeline step.  Return the
    # archived snapshot untouched.
    if str(raw_status.get("status") or "") == "ARCHIVED" or str(raw_status.get("local_lifecycle_status") or "") == "ARCHIVED":
        return raw_status
    status = normalize_checkpoint(raw_status)
    previous_status = str(status.get("status") or "unknown")
    selected = [item for item in (publications.get("stores") or {}).values() if item.get("selected")]
    states = {str(item.get("status") or "") for item in selected}
    total_writes = sum(int(item.get("api_write_count") or 0) for item in selected)
    has_unsubmitted = bool(states & {"SELECTED"})
    issue_summary = summarize_ozon_issues(product_dir, publications)
    if states & {"FAILED", "QUERY_ERROR"}:
        target, error = "NEEDS_ATTENTION", ozon_issue_message(
            issue_summary,
            "一家或多家店铺上传失败；只允许重试失败店铺",
        )
    elif has_unsubmitted and states & (SUCCESS_STATES | {HANDOFF_STATE} | PENDING_STATES):
        target, error = "PARTIAL", "部分已选店铺尚未提交；可继续上传未完成店铺"
    elif states and states <= SUCCESS_STATES:
        target, error = "UPLOADED", "unknown"
    elif states and states <= {HANDOFF_STATE}:
        target, error = "PENDING_REMOTE", "已提交Ozon，等待Ozon生成商品卡"
    elif HANDOFF_STATE in states:
        target, error = "PENDING_REMOTE", "已提交Ozon，等待Ozon生成商品卡"
    elif states & PENDING_STATES:
        target, error = "PENDING_REMOTE", "unknown"
    else:
        target, error = "WAITING_MANUAL_REVIEW", "unknown"
    published_skus = [
        sku
        for record in selected
        for sku in (record.get("sku_publications") or [])
        if isinstance(sku, dict)
    ]
    first_offer = next((str(sku.get("offer_id")) for sku in published_skus if not _unknown(sku.get("offer_id"))), "unknown")
    first_product = next((str(sku.get("ozon_product_id")) for sku in published_skus if not _unknown(sku.get("ozon_product_id"))), "unknown")
    first_task = next((str(sku.get("task_id")) for sku in published_skus if not _unknown(sku.get("task_id"))), "unknown")
    first_store = next((str(store_id) for store_id, record in (publications.get("stores") or {}).items() if record.get("selected")), "unknown")
    failed_errors = []
    for store_id, record in (publications.get("stores") or {}).items():
        if not record.get("selected") or str(record.get("status") or "") not in {"FAILED", "QUERY_ERROR"}:
            continue
        reason = record.get("last_error")
        if _unknown(reason):
            reason = error
        failed_errors.append({
            "store_id": str(store_id),
            "step": "ozon_upload",
            "reason": str(reason),
            "bucket": issue_summary.get("primary_bucket", "other"),
            "bucket_label": issue_summary.get("primary_label", "其他 Ozon 返回"),
            "api_write_count": int(record.get("api_write_count") or 0),
            "retryable": definitely_retryable(record)
            or image_repair_retryable(record)
            or variant_repair_retryable(record),
        })
    ozon = dict(status.get("ozon") or {})
    ozon.update({
        "upload_status": "handed_off" if target == HANDOFF_STATE else "uploaded" if target in {"UPLOADED", "ACTIVE"} else "uploading" if target == "PENDING_REMOTE" else "failed" if target in ATTENTION_STATES else "not_started",
        "product_id": first_product,
        "offer_id": first_offer,
        "task_id": first_task,
        "shop_name": first_store,
        "last_response": ozon.get("last_response"),
        "errors": failed_errors if target in ATTENTION_STATES else [],
        "issue_summary": issue_summary,
    })
    status.update({
        "status": target, "current_step": "ozon_upload", "active_step": None,
        "progress": 100 if target in {"UPLOADED", HANDOFF_STATE} else 99 if target == "PENDING_REMOTE" else 95,
        "completed_at": now() if target in {"UPLOADED", HANDOFF_STATE} else "unknown",
        "api_write_count": total_writes, "last_run_at": now(),
        "error_code": (
            f"OZON_{str(issue_summary.get('primary_bucket') or 'store_upload').upper()}"
            if target in ATTENTION_STATES
            else "unknown"
        ),
        "error_message": error, "failed_step": "ozon_upload" if target in ATTENTION_STATES else "unknown",
        "next_action": "retry_failed_store" if target in ATTENTION_STATES else "ozon_upload" if target == "PARTIAL" else "read_only_status_query" if target in {"PENDING_REMOTE", HANDOFF_STATE} else "complete",
        "ozon": ozon,
        "ozon_issue_summary": issue_summary,
    })
    if target == "UPLOADED":
        status["task_authorized"] = False
        status["upload_priority_state"] = "completed"
    if target in {HANDOFF_STATE, "PENDING_REMOTE"}:
        status["task_authorized"] = False
        status["upload_priority_state"] = "waiting_remote"
    if target in {"UPLOADED", HANDOFF_STATE, "PENDING_REMOTE"}:
        status["warnings"] = [
            warning for warning in status.get("warnings") or []
            if "等待用户检查并确认上传" not in str(warning)
            and "等待用户手动上传" not in str(warning)
            and "图片技术质检已通过" not in str(warning)
        ]
    history = status.setdefault("history", [])
    last_history_status = str((history[-1] if history else {}).get("to") or "unknown")
    transition_from = previous_status if last_history_status == "unknown" else last_history_status
    if transition_from != target:
        if transition_from == "WAITING_MANUAL_REVIEW" and target == "PENDING_REMOTE":
            history.append({
                "from": "WAITING_MANUAL_REVIEW",
                "to": "UPLOADING",
                "at": now(),
                "reason": "The selected store upload started.",
            })
            transition_from = "UPLOADING"
        history.append({
            "from": transition_from,
            "to": target,
            "at": now(),
            "reason": "Aggregated independent per-store Ozon publication states.",
        })
    status.pop("target_store_ids_for_run", None)
    if target in {"UPLOADED", HANDOFF_STATE, "PENDING_REMOTE"} and "ozon_upload" not in status["completed_steps"]:
        status["completed_steps"].append("ozon_upload")
        status["pending_steps"] = [step for step in status["pending_steps"] if step != "ozon_upload"]
    ozon_steps = [item for item in status.setdefault("steps", []) if item.get("name") == "ozon_upload"]
    if target in {"UPLOADED", HANDOFF_STATE, "PENDING_REMOTE"} and (not ozon_steps or ozon_steps[-1].get("status") != "completed"):
        status["steps"].append({
            "name": "ozon_upload", "status": "completed",
            "started_at": now(), "finished_at": now(),
            "retry_count": int((status.get("retry_count_by_step") or {}).get("ozon_upload", 0)),
            "retryable": True, "error": None,
        })
    if target in ATTENTION_STATES and (not ozon_steps or ozon_steps[-1].get("status") != "failed"):
        failed_reason = next(
            (
                str(record.get("last_error"))
                for record in selected
                if str(record.get("status") or "") in {"FAILED", "QUERY_ERROR"}
                and not _unknown(record.get("last_error"))
            ),
            error,
        )
        status["steps"].append({
            "name": "ozon_upload", "status": "failed",
            "started_at": now(), "finished_at": now(),
            "retry_count": int((status.get("retry_count_by_step") or {}).get("ozon_upload", 0)),
            "retryable": True,
            "error": {
                "step": "ozon_upload",
                "reason": failed_reason,
                "occurred_at": now(),
                "retryable": True,
            },
        })
    write_json_atomic(product_dir / "output/ozon-issue-summary.json", issue_summary)
    # After the explicit cutover SQLite is the only mutable task-state source.
    # status.json remains a compatibility snapshot for rollback and legacy
    # readers; it is not updated by asynchronous recovery.
    # Use the caller's root so isolated/temp runs do not accidentally inherit
    # the production repository's cutover marker.
    state_root = root or product_dir.parents[1]
    if not cutover_active(state_root):
        write_json_atomic(product_dir / "status.json", status)
    return status


def default_recovery_runner(root: Path, isolated: Path, store_id: str) -> Dict[str, Any]:
    env = dict(os.environ, UPLOAD_MODE="production")
    _load_env_file(root / "ozon-adapter" / f".env.{store_id}", env)
    log_path = isolated / "logs/store-recovery.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(
            [
                project_python(root), str(root / "scripts/recover_ozon_results.py"),
                "--product-dir", str(isolated), "--shop", store_id, "--timeout", "1",
            ],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=False,
        )
    status = load_json(isolated / "status.json")
    result_path = isolated / "output/ozon-result.json"
    idempotency_path = isolated / "output/ozon-idempotency.json"
    return {
        "returncode": completed.returncode, "status": status,
        "result": load_json(result_path) if result_path.is_file() else {},
        "idempotency": load_json(idempotency_path) if idempotency_path.is_file() else {},
    }


def refresh_pending_stores(
    root: Path,
    product_dir: Path,
    runner: Optional[Callable[[Path, Path, str], Dict[str, Any]]] = None,
    only_store_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    publications = load_publications(product_dir)
    recover = runner or default_recovery_runner
    only = {str(item) for item in only_store_ids} if only_store_ids is not None else None
    checked = []
    for store_id, record in (publications.get("stores") or {}).items():
        if only is not None and str(store_id) not in only:
            continue
        record_status = str(record.get("status") or "")
        if record_status not in PENDING_STATES and not (
            record_status == HANDOFF_STATE and has_task_without_product(record)
        ):
            continue
        isolated = store_workspace(root, product_dir.name, store_id) / "products" / product_dir.name
        if not (isolated / "status.json").is_file():
            record["last_error"] = "店铺异步任务工作区缺失，保持处理中并禁止重传"
            record["last_checked_at"] = now()
            continue
        try:
            outcome = recover(root, isolated, store_id)
        except Exception as exc:
            record["last_error"] = f"只读状态查询失败：{exc}"
            record["last_checked_at"] = now()
            continue
        _persist_store_artifacts(product_dir, store_id, isolated)
        _store_result(record, outcome, increment_version=False)
        checked.append({"store_id": store_id, "status": record["status"]})
    save_publications(product_dir, publications)
    status = aggregate_product_status(product_dir, publications, root)
    # SQLite owns publication state after cutover, but the workbench still has
    # legacy readers that render status.json.  Materialize the read-only
    # recovery snapshot so the UI does not stay stuck at PENDING_REMOTE after
    # Ozon has returned product IDs.
    write_json_atomic(product_dir / "status.json", status)
    return {
        "product_id": product_dir.name, "checked": checked, "status": status["status"],
        "write_api_calls": 0, "inventory_api_calls": 0,
    }


def execute_selected_stores(
    root: Path,
    product_dir: Path,
    only_store_ids: Optional[Iterable[str]] = None,
    runner: Optional[Callable[[Path, Path, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    # Allocate every selected store/SKU article in one persisted pass before
    # creating a workspace or allowing the first Ozon request.
    publications = ensure_store_offer_ids(product_dir)
    selected_store_count = sum(
        1 for record in (publications.get("stores") or {}).values()
        if isinstance(record, Mapping) and record.get("selected")
    )
    only = set(only_store_ids or [])
    run = runner or default_runner
    attempted = []
    skipped = []
    for store_id, record in (publications.get("stores") or {}).items():
        if not record.get("selected") or (only and store_id not in only):
            continue
        current = str(record.get("status") or "")
        if stale_prewrite_pending(record):
            record["status"] = "SELECTED"
            record["last_error"] = "前次店铺上传在调用Ozon前中断，允许从本地断点重试"
            current = "SELECTED"
        if current in PENDING_STATES or current in SUCCESS_STATES:
            skipped.append({"store_id": store_id, "reason": "already_submitted_or_pending"})
            continue
        if (
            not definitely_retryable(record)
            and not image_repair_retryable(record)
            and not variant_repair_retryable(record)
        ):
            skipped.append({"store_id": store_id, "reason": "ambiguous_state_blocks_resubmit"})
            continue
        isolated = prepare_isolated_product(
            root, product_dir, store_id, record,
            selected_store_count=selected_store_count,
        )
        record["status"] = "UPLOADING"
        save_publications(product_dir, publications)
        try:
            outcome = run(root, isolated, store_id)
        except Exception as exc:
            outcome = {"returncode": 1, "status": {"status": "NEEDS_ATTENTION", "api_write_count": 0, "error_message": str(exc)}, "result": {}}
        _persist_store_artifacts(product_dir, store_id, isolated)
        _store_result(record, outcome)
        if credential_failure(record.get("last_error")):
            try:
                mark_store_validation_failed(root, store_id, str(record.get("last_error")))
            except KeyError:
                pass
        save_publications(product_dir, publications)
        attempted.append({"store_id": store_id, "status": record["status"], "api_write_count": record.get("api_write_count", 0)})
    status = aggregate_product_status(product_dir, publications, root)
    # This is the synchronous, user-authorized upload path.  SQLite owns the
    # publication identities after cutover, while status.json remains the
    # workbench/batch compatibility snapshot.  Materialize the already
    # aggregated result before run_batch validates/completes the step; no
    # remote query or additional Ozon write is performed here.
    write_json_atomic(product_dir / "status.json", status)
    return {
        "product_id": product_dir.name, "attempted": attempted, "skipped": skipped,
        "status": status["status"], "api_write_count": status["api_write_count"],
        "inventory_api_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent per-store Ozon uploader")
    parser.add_argument("product_dir")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--only-store", action="append", default=[])
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("--execute is required")
    product_dir = Path(args.product_dir).resolve()
    result = execute_selected_stores(ROOT, product_dir, args.only_store or None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(item["status"] != "FAILED" for item in result["attempted"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
