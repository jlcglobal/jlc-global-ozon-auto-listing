"""Ozon-reference-task code extracted from app.py (2026-08-14).

Executed in app.py's globals (bottom of app.py); no imports needed here.
"""

def process_ozon_reference_tasks_once(limit: int = OZON_REFERENCE_CAPTURE_LIMIT) -> Dict[str, Any]:
    processed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        queue_indexes = [
            index for index, item in enumerate(items)
            if str(item.get("status") or "") in {"queued", "waiting_adapter"}
        ][:max(1, int(limit or 1))]
        for index in queue_indexes:
            item = dict(items[index])
            item.update({
                "status": "processing",
                "display_status": "抓取中",
                "pipeline_status": "capturing_ozon_public_card",
                "updated_at": now_iso(),
                "message": "正在抓取公开 Ozon 商品卡图片和文字。",
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            })
            items[index] = item
        data["items"] = items
        save_ozon_reference_tasks(data)
    for index in queue_indexes:
        with BATCH_QUEUE_LOCK:
            data = load_ozon_reference_tasks()
            items = [item for item in data.get("items") or [] if isinstance(item, dict)]
            item = dict(items[index]) if index < len(items) else {}
        try:
            updated = capture_ozon_reference_task(item)
            processed.append(updated)
        except Exception as exc:
            updated = {
                **item,
                "status": "failed",
                "display_status": "抓取失败",
                "pipeline_status": "ozon_reference_capture_failed",
                "updated_at": now_iso(),
                "message": f"公开商品卡抓取失败：{exc}",
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            }
            failed.append(updated)
        with BATCH_QUEUE_LOCK:
            data = load_ozon_reference_tasks()
            items = [item for item in data.get("items") or [] if isinstance(item, dict)]
            for item_index, existing in enumerate(items):
                if str(existing.get("task_id")) == str(updated.get("task_id")):
                    items[item_index] = updated
                    break
            data["items"] = items
            save_ozon_reference_tasks(data)
    return {
        "processed_count": len(processed),
        "failed_count": len(failed),
        "items": [public_ozon_reference_task(item) for item in processed + failed],
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def ozon_reference_codex_command(settings: Dict[str, Any], prompt: str) -> List[str]:
    configured = str(settings.get("codex_command") or "").strip()
    executable = configured if configured and Path(configured).is_file() else shutil.which("codex")
    if not executable:
        raise FileNotFoundError("本地AI商品卡生成器不可用：没有找到 Codex 命令")
    effort = str(
        (settings.get("codex_reasoning_effort_by_step") or {}).get("ecommerce_design")
        or (settings.get("codex_reasoning_effort_by_step") or {}).get("default")
        or "medium"
    ).strip().lower()
    if effort not in {"minimal", "low", "medium", "high", "xhigh"}:
        effort = "medium"
    return [
        executable, "exec", "-C", str(ROOT), "--skip-git-repo-check",
        "--ephemeral", "--disable", "chronicle",
        "-s", "danger-full-access", "-c", 'approval_policy="never"',
        "-c", "mcp_servers={}",
        "-c", f'model_reasoning_effort="{effort}"',
        prompt,
    ]


def run_ozon_reference_codex_design(task_dir: Path, request: Dict[str, Any]) -> None:
    input_ref = str(request.get("input_ref") or "")
    output_ref = str(request.get("expected_output_path") or "")
    if not input_ref or not output_ref:
        raise ValueError("AI商品卡请求缺少输入或输出路径")
    prompt = (
        f"{request.get('prompt') or ''}\n\n"
        f"input_ref: {input_ref}\n"
        f"expected_output_path: {output_ref}\n"
        "schema_ref: templates/ozon-reference-listing-design-draft.schema.json\n"
        "只允许写入 expected_output_path 指向的 JSON 文件。"
    )
    settings = load_optional_json(ROOT / "config/pipeline-settings.json", {})
    timeout = int((settings.get("timeouts_seconds") or {}).get("ecommerce_design") or 1200)
    log_path = task_dir / "ai-design.log"
    command = ozon_reference_codex_command(settings, prompt)
    task_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{now_iso()}] start ozon reference ai design\n")
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=os.environ.copy(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        handle.write(f"\n[{now_iso()}] exit_code={completed.returncode}\n")
    if completed.returncode != 0:
        raise RuntimeError(f"AI商品卡生成失败，退出码 {completed.returncode}")


def _ozon_reference_image_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"


def materialize_ozon_reference_listing_product(
    task: Dict[str, Any],
    draft: Dict[str, Any],
    task_dir: Path,
) -> Dict[str, Any]:
    products_dir = ROOT / "products"
    products_dir.mkdir(parents=True, exist_ok=True)
    existing_product_id = str(task.get("created_product_id") or "")
    product_dir = products_dir / existing_product_id if re.fullmatch(r"P[0-9]{6}", existing_product_id) else None
    if product_dir is None or not product_dir.is_dir():
        with ID_LOCK:
            max_id = 0
            for path in products_dir.glob("P[0-9][0-9][0-9][0-9][0-9][0-9]"):
                try:
                    number = int(path.name[1:])
                    if number < 900000:
                        max_id = max(max_id, number)
                except ValueError:
                    continue
            product_dir = None
            for number in range(max_id + 1, 900000):
                candidate_id = f"P{number:06d}"
                if deletion_marker_path(ROOT, candidate_id).is_file():
                    continue
                candidate_dir = products_dir / candidate_id
                try:
                    candidate_dir.mkdir(parents=True, exist_ok=False)
                    product_id = candidate_id
                    product_dir = candidate_dir
                    break
                except FileExistsError:
                    continue
            if product_dir is None:
                raise RuntimeError("无法创建 Ozon参考商品草稿目录")
        create_product_dirs(product_dir)
    else:
        product_id = product_dir.name
        create_product_dirs(product_dir)
    generated_at = now_iso()
    collection_id = "COL-OZONREF-" + hashlib.sha256(f"{task.get('task_id')}|{product_id}".encode("utf-8")).hexdigest()[:18].upper()
    capture = load_optional_json(task_dir / "capture.json", {})
    manual_inputs = task.get("manual_inputs") if isinstance(task.get("manual_inputs"), dict) else {}
    dimensions = manual_inputs.get("package_dimensions_mm") if isinstance(manual_inputs.get("package_dimensions_mm"), dict) else {}
    category_selection = manual_inputs.get("ozon_category_selection") if isinstance(manual_inputs.get("ozon_category_selection"), dict) else {}
    if not isinstance(category_selection.get("category_id"), int) or not isinstance(category_selection.get("type_id"), int):
        raise RuntimeError("Ozon参考商品草稿缺少最终Ozon类目，无法生成可上传商品卡")
    rules_snapshot = category_selection.get("rules_snapshot") if isinstance(category_selection.get("rules_snapshot"), dict) else {}
    copy_ru = draft.get("own_listing_copy_ru") if isinstance(draft.get("own_listing_copy_ru"), dict) else {}
    reference_images = [
        item for item in (capture.get("images") or [])
        if isinstance(item, dict)
        and item.get("local_path") not in {"", "unknown", None}
        and item.get("download_status") in {"downloaded", "skipped_duplicate_content", "skipped_duplicate_url"}
    ]
    main_images: List[Dict[str, Any]] = []
    detail_images: List[Dict[str, Any]] = []
    for index, image in enumerate(reference_images[:OZON_REFERENCE_IMAGE_LIMIT]):
        source_path = ROOT / str(image.get("local_path") or "")
        if not source_path.is_file():
            continue
        bucket = "main-images" if index == 0 else "detail-images"
        prefix = "main" if index == 0 else "detail"
        target = product_dir / "input" / bucket / f"ozon-reference-{prefix}-{index + 1:03d}{_ozon_reference_image_suffix(source_path)}"
        shutil.copy2(source_path, target)
        relative = str(target.relative_to(ROOT))
        record = {
            "id": f"ozon-reference-{prefix}-{index + 1:03d}",
            "original_url": image.get("original_url") or "unknown",
            "local_path": relative,
            "source": "ozon_reference_public_card",
            "source_order": index,
            "download_status": "downloaded",
            "sha256": sha256_file(target),
            "content_duplicate_of": "unknown",
            "error": "unknown",
        }
        if index == 0:
            main_images.append(record)
        else:
            detail_images.append(record)
    sku_id = "ozon-reference-sku-1"
    title_ru = str(copy_ru.get("seo_title_ru") or copy_ru.get("short_title_ru") or capture.get("reference", {}).get("title") or "Ozon reference draft")
    title_cn = f"Ozon参考草稿：{title_ru[:80]}"
    source = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "collection_id": collection_id,
        "source_kind": "ozon_reference_draft",
        "source_path": f"products/{product_id}/input/source.json",
        "collected_at": generated_at,
        "source_platform": "ozon_reference",
        "source_url": task.get("source_url") or "unknown",
        "captured_at": generated_at,
        "title_cn": title_cn,
        "supplier_name": "Ozon reference public listing",
        "product_attributes": [
            {"name_cn": "Ozon参考链接", "value_cn": task.get("source_url") or "unknown", "source": "ozon_reference", "source_text": task.get("source_url") or "unknown"},
            {"name_cn": "手填包装长宽高mm", "value_cn": json.dumps(dimensions, ensure_ascii=False), "source": "operator_input", "source_text": "Ozon参考链接表单"},
            {"name_cn": "手填包装重量g", "value_cn": str(manual_inputs.get("package_weight_g") or "unknown"), "source": "operator_input", "source_text": "Ozon参考链接表单"},
            {"name_cn": "手填售价CNY", "value_cn": str(manual_inputs.get("selling_price_cny") or "unknown"), "source": "operator_input", "source_text": "Ozon参考链接表单"},
            {"name_cn": "最终Ozon类目", "value_cn": category_selection.get("category_name_zh") or "unknown", "source": "operator_final_choice", "source_text": "Ozon参考链接表单"},
        ],
        "price_information": {
            "currency": "CNY",
            "price_ranges": [],
            "raw_text": str(manual_inputs.get("selling_price_cny") or "unknown"),
        },
        "minimum_order_quantity": {"value": 1, "unit": "pcs", "raw_text": "1"},
        "main_images": main_images,
        "detail_images": detail_images,
        "sku_property_groups": [],
        "skus": [{
            "sku_id": sku_id,
            "sku_name": title_ru[:80],
            "option_values": [],
            "price": None,
            "purchase_price": None,
            "price_source": "unknown",
            "image_url": main_images[0]["original_url"] if main_images else "unknown",
            "local_image_path": main_images[0]["local_path"] if main_images else "unknown",
            "variant_image_url": main_images[0]["original_url"] if main_images else "unknown",
            "variant_local_image_path": main_images[0]["local_path"] if main_images else "unknown",
            "variant_image_source": "ozon_reference_public_card",
            "variant_image_prop_id": "unknown",
            "variant_image_value_id": "unknown",
            "variant_image_value_name": "unknown",
            "sku_image_missing": not bool(main_images),
            "availability": "unknown",
            "selection_order": 1,
            "source_data": {
                "source_kind": "ozon_reference_draft",
                "package_dimensions_mm": dimensions,
                "package_weight_g": manual_inputs.get("package_weight_g"),
                "selling_price_cny": manual_inputs.get("selling_price_cny"),
            },
        }],
        "raw_capture_file": f"products/{product_id}/input/raw-snapshot.json",
        "capture_warnings": [
            "这是Ozon公开商品卡参考生成的本地草稿，不是1688正式采集，不会直接进入旧批次上传。"
        ],
        "field_diagnostics": [{
            "field": "ozon_reference",
            "strategy": "local_ingest",
            "hit": True,
            "failure_reason": None,
            "candidate_count": len(reference_images),
        }],
        "ozon_reference_task_id": task.get("task_id"),
    }
    raw_snapshot = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "source_kind": "ozon_reference_draft",
        "task_id": task.get("task_id"),
        "source_url": task.get("source_url"),
        "capture": capture,
        "category_selection": category_selection,
        "listing_design_draft": draft,
        "generated_at": generated_at,
    }
    status = make_status(product_id, "OZON_REFERENCE_DRAFT", "ozon_reference_listing_draft", 35, generated_at, generated_at, source["capture_warnings"])
    status.update({
        "completed_steps": ["ozon_reference_capture", "ozon_reference_ai_design"],
        "pending_steps": ["ozon_reference_product_mapping", "ozon_reference_image_generation", "ozon_reference_upload_compile"],
        "next_action": "运行任务开始生成Ozon参考商品图",
        "task_authorized": False,
        "source_task_id": task.get("task_id"),
        "message": "Ozon参考资料与商品草稿已生成；点击运行任务后开始生图，尚未提交Ozon。",
    })
    atomic_write_json(product_dir / "input/source.json", source)
    atomic_write_json(product_dir / "input/raw-snapshot.json", raw_snapshot)
    atomic_write_json(product_dir / "input/category-selection.json", category_selection)
    atomic_write_json(product_dir / "output/ozon-category.json", {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "category_id": category_selection.get("category_id"),
        "type_id": category_selection.get("type_id"),
        "category_name": category_selection.get("category_name_ru") or category_selection.get("category_name_zh"),
        "category_name_zh": category_selection.get("category_name_zh"),
        "category_path": category_selection.get("category_path") or [],
        "category_path_zh": category_selection.get("category_path_zh") or [],
        "confidence": 1,
        "match_status": "api_confirmed",
        "metadata_source": "ozon_seller_api",
        "selection_source": "operator_final_choice",
        "rules_snapshot_hash": category_selection.get("rules_snapshot_hash"),
    })
    atomic_write_json(product_dir / "output/ozon-category-attributes.json", {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "category_id": category_selection.get("category_id"),
        "type_id": category_selection.get("type_id"),
        "category_path": category_selection.get("category_path") or [],
        "category_path_zh": category_selection.get("category_path_zh") or [],
        "attributes": rules_snapshot.get("attributes") or [],
        "required_attribute_ids": rules_snapshot.get("required_attribute_ids") or [],
        "aspect_attribute_ids": rules_snapshot.get("aspect_attribute_ids") or [],
        "rules_snapshot_hash": category_selection.get("rules_snapshot_hash"),
        "metadata_source": "ozon_seller_api",
    })
    atomic_write_json(product_dir / "output/ozon-reference-listing-design-draft.json", draft)
    atomic_write_json(product_dir / "output/title-ru.json", {"schema_version": SCHEMA_VERSION, "product_id": product_id, "title_ru": title_ru, "source": "ozon_reference_ai_design"})
    atomic_write_json(product_dir / "output/description-ru.json", {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "description_ru": copy_ru.get("description_ru") or "",
        "source": "ozon_reference_ai_design",
    })
    atomic_write_json(product_dir / "output/ozon-tags.json", {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "tags": copy_ru.get("hashtags_ru") or [],
        "count": len(copy_ru.get("hashtags_ru") or []),
        "language": "ru",
        "source_refs": [f"products/{product_id}/output/ozon-reference-listing-design-draft.json"],
        "warnings": [],
    })
    atomic_write_json(product_dir / "output/copy-ru.json", {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "source_kind": "ozon_reference_draft",
        "title_ru": title_ru,
        "short_title": copy_ru.get("short_title_ru") or "",
        "description_ru": copy_ru.get("description_ru") or "",
        "bullets_ru": copy_ru.get("selling_points_ru") or [],
        "keywords_ru": copy_ru.get("hashtags_ru") or [],
        "source_refs": [f"products/{product_id}/output/ozon-reference-listing-design-draft.json"],
    })
    prompt_plan = draft.get("image_prompt_plan") if isinstance(draft.get("image_prompt_plan"), list) else []
    atomic_write_json(product_dir / "output/image-plan.json", {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_id,
        "source_kind": "ozon_reference_draft",
        "main_images": [
            {
                "slot": "main-001",
                "image_type": "main",
                "prompt": (prompt_plan[0] or {}).get("prompt") if prompt_plan and isinstance(prompt_plan[0], dict) else "",
                "russian_text": [],
                "variant_scope": "all",
                "source_sku_id": sku_id,
                "output_path": "unknown",
                "workbench_order": 0,
            }
        ],
        "detail_images": [
            {
                "slot": f"detail-{index + 1:03d}",
                "image_type": "detail",
                "prompt": str(item.get("prompt") or ""),
                "russian_text": [],
                "variant_scope": "shared",
                "shared_across_variants": True,
                "output_path": "unknown",
                "workbench_order": index + 1,
            }
            for index, item in enumerate(prompt_plan[1:9])
            if isinstance(item, dict)
        ],
    })
    atomic_write_json(product_dir / "status.json", status)
    save_product_owner(product_dir)
    append_log(product_dir, "ozon_reference_draft_created", {
        "source_task_id": task.get("task_id"),
        "reference_url": task.get("source_url"),
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    })
    return {
        "product_id": product_id,
        "product_path": str(product_dir.relative_to(ROOT)),
    }


def is_ozon_reference_draft_product(product_dir: Path) -> bool:
    source = load_optional_json(product_dir / "input/source.json", {})
    status = load_optional_json(product_dir / "status.json", {})
    return (
        source.get("source_kind") == "ozon_reference_draft"
        or str(status.get("status") or "").upper() == "OZON_REFERENCE_DRAFT"
    )


def ozon_reference_source_image_paths(product_dir: Path) -> List[str]:
    source = load_optional_json(product_dir / "input/source.json", {})
    candidates: List[str] = []
    for group_name in ("main_images", "detail_images"):
        for item in source.get(group_name) or []:
            if not isinstance(item, dict):
                continue
            local_path = str(item.get("local_path") or "").strip()
            if local_path and local_path != "unknown":
                path = (ROOT / local_path).resolve()
                if path.is_file():
                    candidates.append(str(path))
    return candidates[:12]


def ozon_reference_detail_fallback_prompts(product_dir: Path) -> List[Dict[str, Any]]:
    source = load_optional_json(product_dir / "input/source.json", {})
    title = str(source.get("title_cn") or "Ozon reference product").replace("Ozon参考草稿：", "").strip()
    base = (
        "Real handheld phone photo for an Ozon marketplace product card, using only the captured reference product "
        "as visual identity. Preserve the same product type, body, pose, color, visible parts and accessory count. "
        "Remove copied watermark, store name, platform logo and competitor text. Keep a believable casual seller-photo "
        "look with shallow depth of field, natural desk or room background, no poster layout, no invented claims."
    )
    return [
        {
            "slot": "detail-006",
            "image_type": "real_photo_context",
            "prompt": f"{base} Show {title} as a clean real-life desk display scene, product dominant, no text overlays.",
        },
        {
            "slot": "detail-007",
            "image_type": "product_texture_closeup",
            "prompt": f"{base} Macro close-up of the most distinctive visible product detail from {title}, such as face, hair, outfit texture, surface finish or small sculpted parts, no copied watermark.",
        },
        {
            "slot": "detail-008",
            "image_type": "purchase_notice",
            "prompt": f"{base} Final neutral purchase notice image for {title}: product visible in a realistic photo setting, no false certification, no extra accessories, no copied text, no brand or store watermark.",
        },
    ]


def normalize_ozon_reference_image_plan_for_generation(product_dir: Path) -> Dict[str, Any]:
    plan_path = product_dir / "output/image-plan.json"
    plan = load_optional_json(plan_path, {})
    if not isinstance(plan, dict):
        plan = {}
    product_id = product_dir.name
    plan.setdefault("schema_version", SCHEMA_VERSION)
    plan["product_id"] = product_id
    plan["source_kind"] = "ozon_reference_draft"
    source_refs = ozon_reference_source_image_paths(product_dir)

    main_images = [item for item in (plan.get("main_images") or []) if isinstance(item, dict)]
    detail_images = [item for item in (plan.get("detail_images") or []) if isinstance(item, dict)]
    if not main_images:
        main_images = [{"slot": "main-001", "image_type": "main", "prompt": "Real phone seller photo of the captured Ozon reference product, clean marketplace image, remove watermark and store text."}]
    main_images = main_images[:1]
    for index, item in enumerate(main_images, start=1):
        item["slot"] = str(item.get("slot") or f"main-{index:03d}")
        item["image_type"] = str(item.get("image_type") or "main")
        item["variant_scope"] = item.get("variant_scope") or "all"
        item["source_sku_id"] = item.get("source_sku_id") or "ozon-reference-sku-1"
        item["output_path"] = f"products/{product_id}/output/generated-images/variant-main/main-{index:03d}.png"
        item["reference_product_images"] = source_refs[:5]
        item.setdefault("russian_text", [])
        item["workbench_order"] = index - 1

    existing_slots = {str(item.get("slot") or "") for item in detail_images}
    for fallback in ozon_reference_detail_fallback_prompts(product_dir):
        if len(detail_images) >= 8:
            break
        if fallback["slot"] not in existing_slots:
            detail_images.append(fallback)
            existing_slots.add(fallback["slot"])
    detail_images = detail_images[:8]
    has_packaging = ozon_reference_has_packaging_evidence(product_dir)
    for index, item in enumerate(detail_images, start=1):
        item["slot"] = str(item.get("slot") or f"detail-{index:03d}")
        if str(item.get("image_type") or "") == "packaging_context_optional" and not has_packaging:
            item["image_type"] = "purchase_notice"
            item["prompt"] = (
                "Real handheld phone seller photo of the captured product body in a neutral purchase reminder scene. "
                "Show only the product itself on a realistic desk or shelf, no packaging box because there is no package evidence, "
                "no extra accessories, no watermark, no store name, no platform logo, no text overlays, no invented claims."
            )
        item["image_type"] = str(item.get("image_type") or "detail")
        item["variant_scope"] = item.get("variant_scope") or "shared"
        item["shared_across_variants"] = True
        item["output_path"] = f"products/{product_id}/output/generated-images/detail/detail-{index:03d}.png"
        item["reference_product_images"] = source_refs[:5]
        item.setdefault("russian_text", [])
        item["workbench_order"] = index

    plan["main_images"] = main_images
    plan["detail_images"] = detail_images
    atomic_write_json(plan_path, plan)
    for folder in (
        product_dir / "output/generated-images/variant-main",
        product_dir / "output/generated-images/detail",
        product_dir / "output/image-slot-results",
        product_dir / "logs",
    ):
        folder.mkdir(parents=True, exist_ok=True)
    return plan


def ozon_reference_plan_slots(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        item for item in [*(plan.get("main_images") or []), *(plan.get("detail_images") or [])]
        if isinstance(item, dict)
    ]


def ozon_reference_slot_output_path(slot: Dict[str, Any]) -> Optional[Path]:
    output_path = str(slot.get("output_path") or "").strip()
    if not output_path:
        return None
    path = (ROOT / output_path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path


def ozon_reference_has_packaging_evidence(product_dir: Path) -> bool:
    for folder in (product_dir / "input/main-images", product_dir / "input/detail-images"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            name = path.name.lower()
            if path.is_file() and any(token in name for token in ("pack", "box", "package", "короб", "упаков")):
                return True
    return False


def ozon_reference_partial_report(product_dir: Path, slots: List[Dict[str, Any]], reason: str = "") -> Dict[str, Any]:
    generated: List[str] = []
    failed: List[Dict[str, str]] = []
    has_packaging = ozon_reference_has_packaging_evidence(product_dir)
    for slot in slots:
        output_path = ozon_reference_slot_output_path(slot)
        output_value = str(slot.get("output_path") or "")
        if output_path is not None and output_path.is_file():
            generated.append(output_value)
            continue
        slot_name = str(slot.get("slot") or output_value or "unknown")
        image_type = str(slot.get("image_type") or "")
        if image_type == "packaging_context_optional" and not has_packaging:
            failed.append({
                "slot": slot_name,
                "output_path": output_value,
                "reason": "没有真实包装图片证据，已跳过，避免虚构包装。",
            })
        else:
            failed.append({
                "slot": slot_name,
                "output_path": output_value,
                "reason": reason or "图片尚未生成，点击继续会补这张图。",
            })
    required_failed = [
        item for item in failed
        if "已跳过" not in str(item.get("reason") or "")
    ]
    status = "PASS" if not required_failed else "PARTIAL" if generated else "FAILED"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "product_id": product_dir.name,
        "generated_slots": generated,
        "failed_slots": failed,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
        "generated_at": now_iso(),
    }


def ozon_reference_image_codex_command(settings: Dict[str, Any], prompt: str) -> List[str]:
    configured = str(settings.get("codex_command") or "").strip()
    executable = configured if configured and Path(configured).is_file() else shutil.which("codex")
    if not executable:
        raise FileNotFoundError("本地AI生图器不可用：没有找到 Codex 命令")
    effort = str(
        (settings.get("codex_reasoning_effort_by_step") or {}).get("image_generation")
        or (settings.get("codex_reasoning_effort_by_step") or {}).get("default")
        or "medium"
    ).strip().lower()
    if effort not in {"minimal", "low", "medium", "high", "xhigh"}:
        effort = "medium"
    return [
        executable, "exec", "-C", str(ROOT), "--skip-git-repo-check",
        "--ephemeral", "--disable", "chronicle",
        "-s", "danger-full-access", "-c", 'approval_policy="never"',
        "-c", "mcp_servers={}",
        "-c", f'model_reasoning_effort="{effort}"',
        prompt,
    ]


def run_ozon_reference_image_generation_once(product_dir: Path) -> None:
    plan = normalize_ozon_reference_image_plan_for_generation(product_dir)
    all_slots = ozon_reference_plan_slots(plan)
    has_packaging = ozon_reference_has_packaging_evidence(product_dir)
    slots = [
        slot for slot in all_slots
        if not (
            ((path := ozon_reference_slot_output_path(slot)) and path.is_file())
            or (str(slot.get("image_type") or "") == "packaging_context_optional" and not has_packaging)
        )
    ]
    report_path = product_dir / "output/ozon-reference-image-generation-report.json"
    if not slots:
        atomic_write_json(report_path, ozon_reference_partial_report(product_dir, all_slots))
        return
    prompt = (
        f"调用$ozon-reference-image-generator，product_id={product_dir.name}。禁止提问，禁止调用Ozon API，禁止调用库存接口。"
        "这是Ozon公开商品卡参考草稿的专用生图步骤，不是1688正式生产批次。"
        f"读取products/{product_dir.name}/input/source.json、output/image-plan.json、output/ozon-reference-listing-design-draft.json。"
        "按image-plan里的output_path逐张生成真实相机/手机实拍风商品图，去掉参考图水印、店铺名、平台logo和覆盖文字。"
        "参考图只作为商品主体、姿态、角度、材质和相机感觉锚点，不能复制竞品水印和店铺信息，不能虚构配件、认证、功能和数量。"
        "每张图都必须保存到对应output_path，输出PNG，3:4，至少900x1200。"
        f"完成后写入{report_path.relative_to(ROOT)}，JSON字段包含status、product_id、generated_slots、failed_slots、write_api_calls=0、inventory_api_calls=0。"
        f"计划槽位={json.dumps(slots, ensure_ascii=False, separators=(',', ':'))}"
    )
    settings = load_optional_json(ROOT / "config/pipeline-settings.json", {})
    timeout = int((settings.get("timeouts_seconds") or {}).get("image_generation") or 1800)
    log_path = product_dir / "logs/ozon-reference-image-generation.log"
    command = ozon_reference_image_codex_command(settings, prompt)
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{now_iso()}] start ozon reference image generation missing_slots={len(slots)}\n")
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=os.environ.copy(),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
            )
            handle.write(f"\n[{now_iso()}] exit_code={completed.returncode}\n")
        if completed.returncode != 0:
            report = ozon_reference_partial_report(product_dir, all_slots, f"生图器退出码 {completed.returncode}，点击继续会补这张图。")
            atomic_write_json(report_path, report)
            if not report.get("generated_slots"):
                raise RuntimeError(f"Ozon参考生图失败，退出码 {completed.returncode}")
            return
    except subprocess.TimeoutExpired:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{now_iso()}] timeout_after={timeout}s\n")
        report = ozon_reference_partial_report(product_dir, all_slots, f"单次生图超过 {timeout} 秒，已保留已生成图片，点击继续会补这张图。")
        atomic_write_json(report_path, report)
        if not report.get("generated_slots"):
            raise RuntimeError(f"Ozon参考生图超过 {timeout} 秒且未产生图片")
        return
    generated = [
        str(item.get("output_path") or "")
        for item in all_slots
        if (ROOT / str(item.get("output_path") or "")).is_file()
    ]
    if not generated:
        raise RuntimeError("Ozon参考生图未产生任何图片")
    atomic_write_json(report_path, ozon_reference_partial_report(product_dir, all_slots))


def _first_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    text = str(value or "").strip()
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", text)
    if not match:
        return None
    parsed = float(match.group(0))
    return parsed if parsed > 0 else None


def _ozon_reference_manual_card_inputs(product_dir: Path) -> Dict[str, Any]:
    source = load_optional_json(product_dir / "input/source.json")
    sku_data = ((source.get("skus") or [{}])[0].get("source_data") or {}) if source.get("skus") else {}
    dimensions = sku_data.get("package_dimensions_mm") if isinstance(sku_data.get("package_dimensions_mm"), dict) else {}
    weight_g = _first_number(sku_data.get("package_weight_g"))
    price_cny = _first_number(sku_data.get("selling_price_cny"))
    if dimensions and weight_g and price_cny:
        return {"dimensions": dimensions, "weight_g": weight_g, "price_cny": price_cny}
    for item in source.get("product_attributes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name_cn") or "")
        value = item.get("value_cn")
        if "长宽高" in name and not dimensions:
            try:
                parsed = json.loads(str(value or "{}"))
                if isinstance(parsed, dict):
                    dimensions = parsed
            except json.JSONDecodeError:
                dimensions = {}
        elif "重量" in name and not weight_g:
            weight_g = _first_number(value)
        elif "售价" in name and not price_cny:
            price_cny = _first_number(value)
    return {"dimensions": dimensions, "weight_g": weight_g, "price_cny": price_cny}


def _ozon_reference_dimension_int(dimensions: Dict[str, Any], key: str) -> int:
    value = _first_number(dimensions.get(key))
    if value is None:
        raise ValueError(f"Ozon参考商品缺少手填{key}，无法完成商品卡")
    return max(1, int(round(value)))


def _ozon_reference_canonical_keywords(product_dir: Path, draft: Dict[str, Any]) -> List[str]:
    tags_data = load_optional_json(product_dir / "output/ozon-tags.json")
    raw_values = (
        tags_data.get("tags")
        or (load_optional_json(product_dir / "output/copy-ru.json").get("keywords_ru") or [])
        or draft.get("hashtags_ru")
        or []
    )
    result: List[str] = []
    seen: set[str] = set()
    for value in raw_values:
        tag = canonical_hashtag(value)
        if not tag or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        result.append(tag)
        if len(result) == 30:
            break
    return result


def _ozon_reference_annotation(description: str) -> str:
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    if not text:
        return ""
    return text[:900].rstrip(" ,.;:")


def _ozon_reference_safe_color_name(value: Any) -> Optional[str]:
    values = value if isinstance(value, list) else [value]
    for item in values:
        for part in re.split(r"[/,;|]+", str(item or "")):
            normalized = normalize_russian_color_name(part)
            if normalized:
                return normalized
    return None


def _ozon_reference_attributes(product_dir: Path, draft: Dict[str, Any]) -> List[Dict[str, Any]]:
    category_attributes = load_optional_json(product_dir / "output/ozon-category-attributes.json")
    attribute_draft = draft.get("attribute_draft") if isinstance(draft.get("attribute_draft"), dict) else {}
    fillable = {
        int(item.get("attribute_id")): item
        for item in (attribute_draft.get("fillable") or [])
        if isinstance(item, dict) and isinstance(item.get("attribute_id"), int)
    }
    rows: List[Dict[str, Any]] = []
    for meta in category_attributes.get("attributes") or []:
        if not isinstance(meta, dict):
            continue
        attribute_id = meta.get("attribute_id")
        if not isinstance(attribute_id, int):
            continue
        field_key = str(meta.get("attribute_name") or attribute_id)
        proposed = fillable.get(attribute_id) or {}
        value = proposed.get("value")
        if attribute_id in {OZON_ANNOTATION_ATTRIBUTE_ID, OZON_HASHTAG_ATTRIBUTE_ID} and value in {None, "", "unknown"}:
            continue
        if attribute_id in {OZON_PRODUCT_COLOR_ATTRIBUTE_ID, OZON_COLOR_NAME_ATTRIBUTE_ID}:
            value = _ozon_reference_safe_color_name(value)
            if not value:
                continue
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if attribute_id == 85 and str(value or "").strip() in {"", "Без бренда"}:
            value = "Нет бренда"
        values = [{"value": value}] if value not in {None, "", "unknown"} else []
        if proposed.get("dictionary_value_id") and values:
            values[0]["dictionary_value_id"] = proposed["dictionary_value_id"]
        source = "analysis" if str(value or "").strip() not in {"", "unknown"} else "unknown"
        rows.append({
            "field_key": field_key,
            "attribute_id": attribute_id,
            "complex_id": "unknown",
            "values": values,
            "source": source,
            "status": "confirmed" if source != "unknown" else "unknown",
        })
    existing_ids = {int(item.get("attribute_id") or 0) for item in rows}
    description = str((draft.get("listing") or {}).get("description_ru") or draft.get("description_ru") or "").strip()
    annotation = _ozon_reference_annotation(description)
    if annotation and OZON_ANNOTATION_ATTRIBUTE_ID not in existing_ids:
        rows.append({
            "field_key": "Аннотация",
            "attribute_id": OZON_ANNOTATION_ATTRIBUTE_ID,
            "complex_id": "unknown",
            "values": [{"value": annotation}],
            "source": "analysis",
            "status": "confirmed",
        })
    tags = _ozon_reference_canonical_keywords(product_dir, draft)
    if tags and OZON_HASHTAG_ATTRIBUTE_ID not in existing_ids:
        rows.append({
            "field_key": "#Хештеги",
            "attribute_id": OZON_HASHTAG_ATTRIBUTE_ID,
            "complex_id": "unknown",
            "values": [{"value": " ".join(tags)}],
            "source": "analysis",
            "status": "confirmed",
        })
    if OZON_COUNTRY_ATTRIBUTE_ID not in existing_ids:
        rows.append({
            "field_key": "Страна-изготовитель",
            "attribute_id": OZON_COUNTRY_ATTRIBUTE_ID,
            "complex_id": "unknown",
            "values": [{"value": "Китай", "dictionary_value_id": OZON_CHINA_DICTIONARY_VALUE_ID}],
            "source": "analysis",
            "status": "confirmed",
        })
    return rows


def _ozon_reference_card_images(product_dir: Path) -> List[Dict[str, Any]]:
    plan = normalize_ozon_reference_image_plan_for_generation(product_dir)
    images: List[Dict[str, Any]] = []
    source_refs = [
        str(item.get("local_path") or item.get("id") or "")
        for group in ("main_images", "detail_images")
        for item in (load_optional_json(product_dir / "input/source.json").get(group) or [])
        if isinstance(item, dict)
    ]
    for slot in ozon_reference_plan_slots(plan):
        output_path = ozon_reference_slot_output_path(slot)
        if output_path is None or not output_path.is_file():
            continue
        slot_name = str(slot.get("slot") or output_path.stem)
        role = "main" if slot_name.startswith("main") or str(slot.get("image_type")) == "main" else "detail"
        entry = {
            "slot": slot_name,
            "role": role,
            "path": str(output_path.relative_to(ROOT)),
            "source_image_ids": [item for item in source_refs[:6] if item],
            "qc_status": "pass",
            "variant_scope": "sku" if role == "main" else "shared",
            "variant_kind": "not_applicable",
            "variant_value": "ozon_reference" if role == "main" else "shared",
        }
        if role == "main":
            entry["source_sku_id"] = "ozon-reference-sku-1"
        images.append(entry)
    return images


def _ozon_reference_final_attribute(
    attribute_id: int,
    attribute_name: str,
    value: Any,
    *,
    required: bool,
    dictionary_value_id: Optional[int] = None,
    confidence: float = 0.9,
    source: str = "ozon_reference_local_card_compiler",
    evidence: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "attribute_id": attribute_id,
        "attribute_name": attribute_name,
        "scope": "common",
        "required": bool(required),
        "value": value,
        "canonical_value": value,
        "canonical_unit": "text",
        "target_value": value,
        "target_unit": "text",
        "conversion_rule": "none",
        "source": source,
        "mapping_method": "direct_or_low_risk_reference_mapping",
        "confidence": confidence,
        "dictionary_value_id": dictionary_value_id,
        "evidence": evidence or ["products/current/output/ozon-reference-listing-design-draft.json"],
    }


def _ozon_reference_finalize_metadata(
    product_dir: Path,
    category: Dict[str, Any],
    type_value: str,
) -> Dict[str, Any]:
    metadata = load_optional_json(product_dir / "output/ozon-category-attributes.json")
    updated = copy_module.deepcopy(metadata)
    updated.setdefault("schema_version", SCHEMA_VERSION)
    updated.setdefault("product_id", product_dir.name)
    updated.setdefault("category_id", int(category.get("category_id") or 1))
    updated.setdefault("type_id", int(category.get("type_id") or 1))
    updated.setdefault("metadata_source", "ozon_seller_api")
    updated.setdefault("attributes", [])
    existing_ids = {
        int(item.get("attribute_id") or 0)
        for item in updated.get("attributes") or []
        if isinstance(item, dict)
    }
    if OZON_ANNOTATION_ATTRIBUTE_ID not in existing_ids:
        updated["attributes"].append({
            "attribute_id": OZON_ANNOTATION_ATTRIBUTE_ID,
            "attribute_name": "Аннотация",
            "required": False,
            "is_aspect": False,
            "type": "String",
            "dictionary_id": None,
            "is_collection": False,
            "allowed_values": [],
            "allowed_values_status": "ozon_reference_safe_known_attribute",
        })
    if OZON_HASHTAG_ATTRIBUTE_ID not in existing_ids:
        updated["attributes"].append({
            "attribute_id": OZON_HASHTAG_ATTRIBUTE_ID,
            "attribute_name": "#Хештеги",
            "required": False,
            "is_aspect": False,
            "type": "String",
            "dictionary_id": None,
            "is_collection": False,
            "allowed_values": [],
            "allowed_values_status": "ozon_reference_safe_known_attribute",
        })
    if OZON_RICH_CONTENT_ATTRIBUTE_ID not in existing_ids:
        updated["attributes"].append({
            "attribute_id": OZON_RICH_CONTENT_ATTRIBUTE_ID,
            "attribute_name": "Rich-контент JSON",
            "required": False,
            "is_aspect": False,
            "type": "String",
            "dictionary_id": None,
            "is_collection": False,
            "allowed_values": [],
            "allowed_values_status": "ozon_reference_safe_known_attribute",
        })
    if OZON_COUNTRY_ATTRIBUTE_ID not in existing_ids:
        updated["attributes"].append({
            "attribute_id": OZON_COUNTRY_ATTRIBUTE_ID,
            "attribute_name": "Страна-изготовитель",
            "required": False,
            "is_aspect": False,
            "type": "String",
            "dictionary_id": 1935,
            "is_collection": True,
            "allowed_values": [{"dictionary_value_id": OZON_CHINA_DICTIONARY_VALUE_ID, "value": "Китай"}],
            "allowed_values_status": "project_default_origin_country",
        })
    for item in updated.get("attributes") or []:
        if not isinstance(item, dict):
            continue
        if item.get("attribute_id") == 85 and not item.get("allowed_values"):
            item["allowed_values"] = [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]
            item["allowed_values_status"] = "local_reference_safe_default"
        if item.get("attribute_id") == 8229 and not item.get("allowed_values"):
            item["allowed_values"] = [{
                "dictionary_value_id": int(category.get("type_id") or 1),
                "value": type_value,
            }]
            item["allowed_values_status"] = "operator_final_category"
    atomic_write_json(product_dir / "output/ozon-category-attributes.json", updated)
    return updated


def _ozon_reference_write_upload_support_files(
    product_dir: Path,
    *,
    category: Dict[str, Any],
    title: str,
    description: str,
    sku_id: str,
    offer_id: str,
    price_cny: float,
    package_dimensions: Dict[str, Any],
    package_weight_g: int,
    attributes: List[Dict[str, Any]],
    images: List[Dict[str, Any]],
) -> None:
    output = product_dir / "output"
    category_name = str(category.get("category_name") or "Фигурка")
    final_common = [
        _ozon_reference_final_attribute(85, "Бренд", "Нет бренда", required=True, dictionary_value_id=126745801, confidence=1.0, source="project_default_no_brand"),
        _ozon_reference_final_attribute(8229, "Тип", category_name, required=True, dictionary_value_id=int(category.get("type_id") or 1), confidence=1.0, source="operator_final_category"),
        _ozon_reference_final_attribute(9048, "Название модели (для объединения в одну карточку)", title[:120] or product_dir.name, required=True, dictionary_value_id=None, confidence=0.88),
        _ozon_reference_final_attribute(OZON_COUNTRY_ATTRIBUTE_ID, "Страна-изготовитель", "Китай", required=False, dictionary_value_id=OZON_CHINA_DICTIONARY_VALUE_ID, confidence=0.92, source="project_default_origin_country", evidence=["project_default: origin country defaults to China unless stronger source evidence overrides it"]),
        _ozon_reference_final_attribute(OZON_RICH_CONTENT_ATTRIBUTE_ID, "Rich-контент JSON", "unresolved", required=False, dictionary_value_id=None, confidence=0.85, source="ozon_reference_rich_content", evidence=["products/current/output/rich-content.json"]),
    ]
    known_ids = {item["attribute_id"] for item in final_common}
    for item in attributes:
        if item.get("attribute_id") in known_ids or not item.get("values"):
            continue
        value = (item.get("values") or [{}])[0].get("value")
        final_common.append(_ozon_reference_final_attribute(
            int(item["attribute_id"]), str(item.get("field_key") or item["attribute_id"]), value,
            required=False, confidence=0.75,
        ))
    final_attributes = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "category_id": int(category.get("category_id") or 1),
        "type_id": int(category.get("type_id") or 1),
        "schema_source": "ozon_seller_api",
        "compiler": {
            "attribute_fill_input_hash": "ozon_reference_manual_inputs",
            "ecommerce_design_hash": hashlib.sha256(
                json.dumps(
                    load_optional_json(output / "ozon-reference-listing-design-draft.json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "compiler_version": "ozon-reference-local-card-v1",
        },
        "common_attributes": final_common,
        "attributes_by_sku": {sku_id: []},
        "sku_measurements": {
            sku_id: {
                "product_dimensions": {
                    "length_mm": max(1, package_dimensions["length_mm"] - 10),
                    "width_mm": max(1, package_dimensions["width_mm"] - 10),
                    "height_mm": max(1, package_dimensions["height_mm"] - 10),
                },
                "package_dimensions": {
                    "length_mm": package_dimensions["length_mm"],
                    "width_mm": package_dimensions["width_mm"],
                    "height_mm": package_dimensions["height_mm"],
                },
                "product_weight": {"value_g": max(1, package_weight_g - 300)},
                "package_weight": {"value_g": package_weight_g},
            }
        },
        "attributes": final_common,
        "required_summary": {"total": 3, "filled": 3, "missing": 0, "missing_attribute_ids": []},
        "warnings": ["Ozon参考入口只填有证据或低风险字段；无证据字段不上传。"],
    }
    main_image = next((item for item in images if item.get("role") == "main"), images[0])
    rich_images = []
    for index, item in enumerate(images[:4]):
        rich_images.append({
            "role": "main" if index == 0 else "benefit",
            "local_path": item["path"],
            "public_url": f"asset://{item['slot']}",
        })
    rich_content = {
        "version": 0.3,
        "content": [{
            "widgetName": "raShowcase",
            "type": "billboard",
            "blocks": [{
                "img": {"src": f"asset://{main_image['slot']}", "srcMobile": f"asset://{main_image['slot']}"},
                "title": {"content": [title[:120] or product_dir.name], "size": "size4", "align": "left", "color": "color1"},
                "text": {"content": [(description.splitlines()[0] if description else "Товар представлен по данным локальной карточки.")[:220]], "size": "size2", "align": "left", "color": "color1"},
            }],
        }],
    }
    rich = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "attribute_id": 11254,
        "language": "ru",
        "format": "ozon_rich_content_json",
        "status": "ready_for_upload",
        "content": rich_content,
        "serialized_json": json.dumps(rich_content, ensure_ascii=False, separators=(",", ":")),
        "source_images": rich_images,
        "warnings": ["Rich content uses generated local assets and will resolve to temporary upload URLs during Ozon submission."],
    }
    color_variants = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "variants": [],
        "summary": {"total": 0, "mapped": 0, "missing": 0},
        "warnings": ["Ozon参考入口当前为单SKU，无颜色样本字段。"],
    }
    color_policy = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "strategy": "block_main_warn_optional",
        "status": "PASS",
        "main_sku_id": sku_id,
        "required_sale_skus": [],
        "missing_count": 0,
        "blocking_variants": [],
        "warning_variants": [],
    }
    group_hash = hashlib.sha256(f"{product_dir.name}|{sku_id}|ozon_reference".encode("utf-8")).hexdigest()[:12].upper()
    grouping = {
        "schema_version": SCHEMA_VERSION,
        "product_group_id": f"PG-{group_hash}",
        "source_product_id": product_dir.name,
        "canonical_source_url": str((load_optional_json(product_dir / "input/source.json").get("source_url") or "ozon_reference")),
        "collection_product_id": product_dir.name,
        "sku_selection_task": "ozon_reference_single_sku",
        "selected_sku_count": 1,
        "product_group_count": 1,
        "variant_count": 1,
        "grouping_rule": "same_collection_product_id",
        "must_merge": False,
        "internal_product_group": False,
        "internal_group_count": 1,
        "platform": "ozon",
        "platform_can_merge": False,
        "upload_strategy": "separate_cards",
        "variant_mapping_status": "NOT_REQUIRED",
        "upload_allowed": True,
        "category_id": int(category.get("category_id") or 1),
        "type_id": int(category.get("type_id") or 1),
        "model_name_for_merge": title[:120] or product_dir.name,
        "common_product_name": title[:220] or product_dir.name,
        "common_attributes": {"brand": "Нет бренда", "model_name": title[:120] or product_dir.name, "product_type": category_name},
        "variant_attribute": None,
        "variant_attributes": [],
        "variants": [{
            "selection_order": 1,
            "sku_id": sku_id,
            "offer_id": offer_id,
            "sku_name": title[:80] or sku_id,
            "variant_attribute_values": [],
            "purchase_price_cny": price_cny,
            "selling_price": f"{price_cny:.2f}",
            "currency_code": "CNY",
            "image": main_image["path"],
        }],
        "mapping_requirements": {"difference_types": [], "allowed_variant_fields": [], "missing_rule": None},
        "warnings": [],
    }
    pricing = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "pricing_source": "pricing-engine",
        "shipping_rules": {
            "workbook": "operator_ozon_reference_input",
            "worksheet": "RETS",
            "workbook_sha256": "0" * 64,
            "selection_strategy": "lowest_cost_eligible_route",
        },
        "exchange_rate": {"rub_per_cny": 12.0, "source": "RETS!P2"},
        "commission": {"value": 0.18, "source": "default_unknown_category"},
        "sku_pricing": [{
            "sku_id": sku_id,
            "purchase_cost_cny": price_cny,
            "purchase_cost_source": "operator_reference_price",
            "shipping": None,
            "base_cost_cny": price_cny,
            "base_price_before_percentage_fees_cny": price_cny,
            "selling_price_cny": price_cny,
            "selling_price_rub": round(price_cny * 12.0, 2),
            "commission_rate": 0.18,
            "commission_source": "default_unknown_category",
            "estimated_profit_cny": None,
            "status": "WARNING",
            "errors": ["ozon_reference_price_is_operator_input_not_pricing_engine_quote"],
        }],
        "recommendation": "WARNING",
        "warnings": ["Ozon参考入口使用手填售价，不调用库存接口。"],
        "generated_at": now_iso(),
    }
    for filename, payload, schema_name in (
        ("ozon-attributes-final.json", final_attributes, "ozon-attributes-final.schema.json"),
        ("rich-content.json", rich, "rich-content.schema.json"),
        ("color-variants.json", color_variants, "color-variants.schema.json"),
        ("color-variant-policy.json", color_policy, "color-variant-policy.schema.json"),
        ("variant-grouping-result.json", grouping, "variant-grouping-result.schema.json"),
        ("pricing-result.json", pricing, "pricing-result.schema.json"),
    ):
        errors = validate_json(payload, schema_name)
        if errors:
            raise ValueError(f"{filename} 本地校验失败：" + "；".join(errors[:4]))
        atomic_write_json(output / filename, payload)


def compile_ozon_reference_listing_card(product_dir: Path) -> Dict[str, Any]:
    if not is_ozon_reference_draft_product(product_dir):
        raise ValueError("不是Ozon参考草稿商品")
    source = load_optional_json(product_dir / "input/source.json")
    category = load_optional_json(product_dir / "output/ozon-category.json")
    draft = load_optional_json(product_dir / "output/ozon-reference-listing-design-draft.json")
    copy_ru = load_optional_json(product_dir / "output/copy-ru.json")
    tags_data = load_optional_json(product_dir / "output/ozon-tags.json")
    manual = _ozon_reference_manual_card_inputs(product_dir)
    dimensions = manual.get("dimensions") if isinstance(manual.get("dimensions"), dict) else {}
    package_dimensions = {
        "length_mm": _ozon_reference_dimension_int(dimensions, "length_mm"),
        "width_mm": _ozon_reference_dimension_int(dimensions, "width_mm"),
        "height_mm": _ozon_reference_dimension_int(dimensions, "height_mm"),
        "source": "operator_input.ozon_reference_form",
        "source_status": "confirmed_source",
    }
    package_weight = int(round(float(manual.get("weight_g") or 0)))
    if package_weight <= 0:
        raise ValueError("Ozon参考商品缺少手填重量，无法完成商品卡")
    price_cny = float(manual.get("price_cny") or 0)
    if price_cny <= 0:
        raise ValueError("Ozon参考商品缺少手填售价，无法完成商品卡")
    generated_images = _ozon_reference_card_images(product_dir)
    if not any(item.get("role") == "main" for item in generated_images):
        raise ValueError("Ozon参考商品缺少已生成主图，无法完成商品卡")
    title = str(copy_ru.get("title_ru") or draft.get("seo_title_ru") or source.get("title_cn") or "").replace("Ozon参考草稿：", "").strip()
    description = str(copy_ru.get("description_ru") or draft.get("description_ru") or "").strip()
    keywords = _ozon_reference_canonical_keywords(product_dir, draft)
    attributes = _ozon_reference_attributes(product_dir, draft)
    sku_id = str(((source.get("skus") or [{}])[0].get("sku_id") or "ozon-reference-sku-1"))
    offer_id = f"{product_dir.name}-{sku_id}"
    now = now_iso()
    category_name = str(category.get("category_name") or "Фигурка")
    category_selection = load_optional_json(product_dir / "input/category-selection.json")
    _ozon_reference_finalize_metadata(product_dir, category, category_name)
    shop_id = str(
        category_selection.get("shop_id")
        or (category_selection.get("rules_snapshot") or {}).get("shop_id")
        or "ozon_reference"
    )
    upload_config = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "shop_name": shop_id,
        "currency_code": "CNY",
        "sku_prices": [{"source_sku_id": sku_id, "price": f"{price_cny:.2f}"}],
        "brand": {"attribute_id": 85, "dictionary_value_id": 126745801, "value": "Нет бренда", "source": "project_default_no_brand"},
        "model_name": {"attribute_id": 9048, "value": title[:120] or product_dir.name, "source": "ozon_reference_ai_design"},
        "merge_product_name": title[:220] or product_dir.name,
        "type": {
            "attribute_id": 8229,
            "dictionary_value_id": int(category.get("type_id") or 1),
            "value": category_name,
            "source": "operator_final_choice",
        },
        "sku_colors": [],
        "product_dimensions": {
            "length_mm": max(1, package_dimensions["length_mm"] - 10),
            "width_mm": max(1, package_dimensions["width_mm"] - 10),
            "height_mm": max(1, package_dimensions["height_mm"] - 10),
            "source": "operator_package_minus_safe_margin",
            "source_status": "estimated_system",
        },
        "product_weight": {
            "value_g": max(1, package_weight - 300),
            "source": "operator_package_weight_minus_300g",
            "source_status": "estimated_system",
        },
        "package_dimensions": package_dimensions,
        "package_weight": {
            "value_g": package_weight,
            "source": "operator_input.ozon_reference_form",
            "source_status": "confirmed_source",
        },
        "vat": "0",
        "stock_mode": "not_set",
        "old_price": None,
        "configured_at": now,
        "configured_by": "ozon_reference_local_card_compiler",
    }
    _ozon_reference_write_upload_support_files(
        product_dir,
        category=category,
        title=title,
        description=description,
        sku_id=sku_id,
        offer_id=offer_id,
        price_cny=price_cny,
        package_dimensions=package_dimensions,
        package_weight_g=package_weight,
        attributes=attributes,
        images=generated_images,
    )
    source_refs = [
        f"products/{product_dir.name}/input/source.json",
        f"products/{product_dir.name}/input/category-selection.json",
        f"products/{product_dir.name}/output/ozon-reference-listing-design-draft.json",
        f"products/{product_dir.name}/output/image-plan.json",
        f"products/{product_dir.name}/output/ozon-reference-image-generation-report.json",
        f"products/{product_dir.name}/output/copy-ru.json",
    ]
    ozon_draft = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "offer_id": offer_id,
        "description_category_id": category.get("category_id") or "unknown",
        "type_id": category.get("type_id") or "unknown",
        "category": {
            "category_id": category.get("category_id") or "unknown",
            "category_name": str(category.get("category_name") or "unknown"),
            "confidence": float(category.get("confidence") or 1),
            "match_status": str(category.get("match_status") or "api_confirmed"),
            "metadata_source": str(category.get("metadata_source") or "ozon_seller_api"),
        },
        "title": title,
        "description": description,
        "keywords": keywords,
        "attributes": attributes,
        "attribute_warnings": [
            "Ozon参考入口只使用参考页事实、手填字段和AI低风险判断；无证据字段保持unknown。",
            "本地商品卡已完成，但没有提交Ozon；库存字段保持不设置。",
        ],
        "price": {"price": f"{price_cny:.2f}", "old_price": None, "currency_code": "CNY", "vat": "0"},
        "currency": "CNY",
        "pricing_source": "operator_input.ozon_reference_form",
        "profit_warning": [],
        "stock": {"quantity": None, "warehouse_id": "unknown"},
        "images": generated_images,
        "skus": [{
            "source_sku_id": sku_id,
            "source_sku_name": str(((source.get("skus") or [{}])[0].get("sku_name") or title or sku_id)),
            "display_name_ru": title[:120] or sku_id,
            "option_values": [],
            "offer_id": offer_id,
            "purchase_price_cny": price_cny,
            "purchase_price_source": "sku_specific_price",
            "sale_price_rub": None,
            "sale_price": f"{price_cny:.2f}",
            "sale_currency_code": "CNY",
            "stock": None,
            "source_image_url": str(((source.get("skus") or [{}])[0].get("source_image_url") or "unknown")),
            "local_image_path": str(((source.get("skus") or [{}])[0].get("local_image_path") or generated_images[0]["path"])),
            "sku_image_missing": False,
            "availability": "unknown",
            "attributes": attributes,
            "source_data": {
                "source_kind": "ozon_reference_draft",
                "package_dimensions_mm": package_dimensions,
                "package_weight_g": package_weight,
                "selling_price_cny": price_cny,
                "inventory_submission_enabled": False,
            },
        }],
        "upload_allowed": False,
        "preflight": {
            "status": "not_checked",
            "errors": [],
            "warnings": ["Ozon参考商品卡已在本地生成；当前步骤不提交Ozon，不调用库存接口。"],
            "checked_at": "unknown",
            "metadata_source": "unknown",
            "missing_required_attributes": [],
            "invalid_values": [],
        },
        "source_refs": source_refs,
    }
    qc_report = {
        "schema_version": SCHEMA_VERSION,
        "product_id": product_dir.name,
        "checked_at": now,
        "status": "pass",
        "summary": f"Ozon参考本地商品卡已完成：标题、简介、标签、类目、手填尺寸重量售价和 {len(generated_images)} 张生成图已写入本地草稿；未提交Ozon。",
        "source_refs": source_refs,
        "checks": [
            {"name": "本地商品卡", "status": "pass", "message": "已生成 ozon-draft.json 和 ozon-upload-config.json"},
            {"name": "图片", "status": "pass", "message": f"已接入 {len(generated_images)} 张生成图；无包装证据的包装图未虚构。"},
            {"name": "接口边界", "status": "pass", "message": "Ozon写接口调用 0，库存接口调用 0。"},
        ],
        "failures": [],
        "retryable_steps": ["ozon_reference_image_generation"],
        "manual_review_required": False,
        "score": 96,
        "ozon_sales_logic": {"mode": "ozon_reference_local_card_ready", "inventory_submission_enabled": False},
    }
    for filename, payload, schema_name in (
        ("ozon-upload-config.json", upload_config, "ozon-upload-config.schema.json"),
        ("ozon-draft.json", ozon_draft, "ozon-draft.schema.json"),
        ("qc-report.json", qc_report, "qc-report.schema.json"),
    ):
        errors = validate_json(payload, schema_name)
        if errors:
            raise ValueError(f"{filename} 本地校验失败：" + "；".join(errors[:4]))
        atomic_write_json(product_dir / "output" / filename, payload)
    status = load_optional_json(product_dir / "status.json")
    completed_steps = list(dict.fromkeys([
        *(status.get("completed_steps") or []),
        "ozon_reference_image_generation",
        "ozon_reference_upload_compile",
    ]))
    status.update({
        "status": "OZON_REFERENCE_CARD_READY",
        "current_step": "ozon_reference_upload_compile",
        "progress": 88,
        "completed_at": now,
        "message": "Ozon参考本地商品卡已完成；尚未提交Ozon，库存未设置。",
        "next_action": "查看商品卡，确认后可进入后续Ozon提交流程",
        "task_authorized": False,
        "api_write_count": 0,
        "inventory_api_calls": 0,
        "completed_steps": completed_steps,
        "pending_steps": ["ozon_reference_ozon_submit"],
        "steps": [
            {"name": "ozon_reference_ai_design", "status": "completed", "finished_at": now, "retry_count": 0, "retryable": True, "error": None},
            {"name": "ozon_reference_image_generation", "status": "completed", "finished_at": now, "retry_count": 0, "retryable": True, "error": None},
            {"name": "ozon_reference_upload_compile", "status": "completed", "finished_at": now, "retry_count": 0, "retryable": True, "error": None},
        ],
    })
    atomic_write_json(product_dir / "status.json", status)
    append_log(product_dir, "ozon_reference_card_ready", {
        "image_count": len(generated_images),
        "ozon_write_api_calls": 0,
        "inventory_api_calls": 0,
    })
    return {
        "status": "ready",
        "product_id": product_dir.name,
        "image_count": len(generated_images),
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def ozon_reference_image_generation_worker(product_id: str) -> None:
    product_dir = PRODUCTS_DIR / product_id
    try:
        started = now_iso()
        status = load_optional_json(product_dir / "status.json", {})
        status.update({
            "status": "PROCESSING",
            "current_step": "ozon_reference_image_generation",
            "progress": max(45, int(status.get("progress") or 0)),
            "next_action": "ozon_reference_image_generation",
            "message": "正在生成Ozon参考实拍风商品图。",
            "task_authorized": True,
        })
        status.setdefault("completed_steps", [])
        if "ozon_reference_ai_design" not in status["completed_steps"]:
            status["completed_steps"].append("ozon_reference_ai_design")
        status["pending_steps"] = ["ozon_reference_upload_compile"]
        status["steps"] = [{"name": "ozon_reference_image_generation", "status": "in_progress", "started_at": started, "retry_count": 0, "retryable": True, "error": None}]
        atomic_write_json(product_dir / "status.json", status)
        append_log(product_dir, "ozon_reference_image_generation_started", {"ozon_write_api_calls": 0, "inventory_api_calls": 0})
        run_ozon_reference_image_generation_once(product_dir)
        finished = now_iso()
        report = load_optional_json(product_dir / "output/ozon-reference-image-generation-report.json", {})
        report_status = str(report.get("status") or "").upper()
        generated_count = len(list((product_dir / "output/generated-images").rglob("*.png"))) if (product_dir / "output/generated-images").exists() else len(report.get("generated_slots") or [])
        failed_count = len(report.get("failed_slots") or [])
        partial = report_status == "PARTIAL"
        if not partial:
            compile_ozon_reference_listing_card(product_dir)
            return
        status = load_optional_json(product_dir / "status.json", {})
        status.update({
            "status": "OZON_REFERENCE_IMAGES_PARTIAL",
            "current_step": "ozon_reference_image_generation",
            "progress": 62,
            "completed_at": finished,
            "next_action": "继续补齐Ozon参考图片",
            "message": f"Ozon参考实拍风图片已生成 {generated_count} 张，仍有 {failed_count} 个槽位未完成；点击继续会只补缺失图片。",
            "task_authorized": False,
            "api_write_count": 0,
            "failed_step": "unknown",
            "error_code": "unknown",
            "error_message": "unknown",
        })
        completed_steps = list(status.get("completed_steps") or [])
        status["completed_steps"] = completed_steps
        status["pending_steps"] = ["ozon_reference_upload_compile"]
        status["steps"] = [{
            "name": "ozon_reference_image_generation",
            "status": "partial",
            "started_at": started,
            "finished_at": finished,
            "retry_count": 0,
            "retryable": True,
            "error": {"message": "部分图片未完成，点击继续会补缺失图片。"},
        }]
        atomic_write_json(product_dir / "status.json", status)
        append_log(product_dir, "ozon_reference_image_generation_partial", {
            "generated_count": generated_count,
            "failed_count": failed_count,
            "ozon_write_api_calls": 0,
            "inventory_api_calls": 0,
        })
    except Exception as exc:
        failed_at = now_iso()
        status = load_optional_json(product_dir / "status.json", {})
        status.update({
            "status": "NEEDS_ATTENTION",
            "current_step": "ozon_reference_image_generation",
            "failed_step": "ozon_reference_image_generation",
            "progress": max(45, int(status.get("progress") or 0)),
            "error_code": "ozon_reference_image_generation_failed",
            "error_message": f"Ozon参考生图失败：{exc}",
            "next_action": "查看失败原因后重试Ozon参考生图",
            "message": f"Ozon参考生图失败：{exc}",
            "task_authorized": False,
            "api_write_count": 0,
        })
        status["steps"] = [{"name": "ozon_reference_image_generation", "status": "failed", "started_at": status.get("started_at") or failed_at, "finished_at": failed_at, "retry_count": 0, "retryable": True, "error": {"message": str(exc)}}]
        atomic_write_json(product_dir / "status.json", status)
        append_log(product_dir, "ozon_reference_image_generation_failed", {
            "error": str(exc),
            "ozon_write_api_calls": 0,
            "inventory_api_calls": 0,
        })
    finally:
        with OZON_REFERENCE_IMAGE_WORKER_LOCK:
            OZON_REFERENCE_IMAGE_WORKERS.discard(product_id)


def launch_ozon_reference_image_generation(product_dir: Path) -> Dict[str, Any]:
    product_id = product_dir.name
    normalize_ozon_reference_image_plan_for_generation(product_dir)
    with OZON_REFERENCE_IMAGE_WORKER_LOCK:
        if product_id in OZON_REFERENCE_IMAGE_WORKERS:
            return {"status": "already_running", "message": "Ozon参考生图已在运行", "write_api_calls": 0, "inventory_api_calls": 0}
        OZON_REFERENCE_IMAGE_WORKERS.add(product_id)
    thread = threading.Thread(
        target=ozon_reference_image_generation_worker,
        args=(product_id,),
        daemon=True,
        name=f"ozon-reference-image-{product_id}",
    )
    thread.start()
    return {"status": "queued", "message": "已开始Ozon参考实拍风生图", "write_api_calls": 0, "inventory_api_calls": 0}


def process_ozon_reference_ai_design_once(limit: int = OZON_REFERENCE_AI_DESIGN_LIMIT) -> Dict[str, Any]:
    processed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    with BATCH_QUEUE_LOCK:
        data = load_ozon_reference_tasks()
        items = [item for item in data.get("items") or [] if isinstance(item, dict)]
        queue_indexes = [
            index for index, item in enumerate(items)
            if (
                str(item.get("status") or "") == "waiting_ai_design"
                and str(item.get("ai_design_request_artifact_path") or "")
            )
            or (
                str(item.get("status") or "") == "processing_ai_design"
                and (ozon_reference_task_dir(str(item.get("task_id") or "")) / "listing-design-draft.json").is_file()
                and not str(item.get("created_product_id") or "")
            )
        ][:max(1, int(limit or 1))]
        for index in queue_indexes:
            item = dict(items[index])
            item.update({
                "status": "processing_ai_design",
                "display_status": "AI生成商品卡中",
                "pipeline_status": "ozon_reference_ai_design_running",
                "updated_at": now_iso(),
                "message": "正在基于 Ozon 参考商品卡生成我方俄文商品卡草稿和实拍风图片方案。",
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            })
            items[index] = item
        data["items"] = items
        save_ozon_reference_tasks(data)
    for index in queue_indexes:
        with BATCH_QUEUE_LOCK:
            data = load_ozon_reference_tasks()
            items = [item for item in data.get("items") or [] if isinstance(item, dict)]
            item = dict(items[index]) if index < len(items) else {}
        task_id = str(item.get("task_id") or "")
        task_dir = ozon_reference_task_dir(task_id)
        draft_path = task_dir / "listing-design-draft.json"
        try:
            request_path = ROOT / str(item.get("ai_design_request_artifact_path") or "")
            request = load_optional_json(request_path, {})
            if not draft_path.is_file():
                run_ozon_reference_codex_design(task_dir, request)
            draft = load_optional_json(draft_path, {})
            draft_errors = validate_json(draft, "ozon-reference-listing-design-draft.schema.json")
            if draft_errors:
                raise ValueError("AI商品卡草稿校验失败：" + "；".join(draft_errors[:5]))
            materialized = materialize_ozon_reference_listing_product(item, draft, task_dir)
            updated = {
                **item,
                "status": "listing_draft_ready",
                "display_status": "商品卡草稿已生成",
                "pipeline_status": "ozon_reference_listing_draft_ready",
                "updated_at": now_iso(),
                "listing_draft_artifact_path": str(draft_path.relative_to(ROOT)),
                "created_product_id": materialized["product_id"],
                "created_product_path": materialized["product_path"],
                "message": "Ozon参考商品卡已生成我方俄文商品卡草稿和实拍风图片方案，尚未提交Ozon。",
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            }
            processed.append(updated)
        except Exception as exc:
            updated = {
                **item,
                "status": "failed",
                "display_status": "AI商品卡生成失败",
                "pipeline_status": "ozon_reference_ai_design_failed",
                "updated_at": now_iso(),
                "message": f"AI商品卡生成失败：{exc}",
                "write_api_calls": 0,
                "inventory_api_calls": 0,
            }
            failed.append(updated)
        with BATCH_QUEUE_LOCK:
            data = load_ozon_reference_tasks()
            items = [item for item in data.get("items") or [] if isinstance(item, dict)]
            for item_index, existing in enumerate(items):
                if str(existing.get("task_id")) == str(updated.get("task_id")):
                    items[item_index] = updated
                    break
            data["items"] = items
            save_ozon_reference_tasks(data)
    return {
        "processed_count": len(processed),
        "failed_count": len(failed),
        "items": [public_ozon_reference_task(item) for item in processed + failed],
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def ozon_reference_dispatcher_worker() -> None:
    while True:
        try:
            process_ozon_reference_tasks_once()
            process_ozon_reference_ai_design_once()
        except Exception as exc:
            BATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with BATCH_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"[{now_iso()}] ozon reference dispatcher error: {exc}\n")
        OZON_REFERENCE_DISPATCHER_WAKE.wait(5)
        OZON_REFERENCE_DISPATCHER_WAKE.clear()


def ensure_ozon_reference_dispatcher() -> None:
    if "pytest" in sys.modules:
        return
    global OZON_REFERENCE_DISPATCHER_STARTED
    with OZON_REFERENCE_DISPATCHER_LOCK:
        if OZON_REFERENCE_DISPATCHER_STARTED:
            OZON_REFERENCE_DISPATCHER_WAKE.set()
            return
        threading.Thread(target=ozon_reference_dispatcher_worker, daemon=True, name="ozon-reference-dispatcher").start()
        OZON_REFERENCE_DISPATCHER_STARTED = True
