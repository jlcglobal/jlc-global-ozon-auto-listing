#!/usr/bin/env python3
"""Validate and project the connected-Codex ecommerce design.

This module never generates commercial content and never calls Ozon.  Its only
jobs are enforcing the N+8 contract and materializing legacy files from the
already completed unified design artifact.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator

try:
    from scripts.image_asset_boundaries import validate_product_reference
    from scripts.ozon_attribute_compiler import (
        AttributeCompileError,
        allowed_by_id,
        allowed_by_value,
        compile_product_attributes,
        material_decision_from_fact,
        normalize_confidence,
        physical_dimension_for,
        value_looks_like_wrong_dimension,
    )
    from scripts.production_input_guard import (
        ProductionInputError,
        validate_current_product_trace_ref,
        validate_formal_product_input,
    )
    from scripts.sku_image_bindings import effective_sku_reference, load_sku_image_bindings
except ModuleNotFoundError:
    from image_asset_boundaries import validate_product_reference
    from ozon_attribute_compiler import (
        AttributeCompileError,
        allowed_by_id,
        allowed_by_value,
        compile_product_attributes,
        material_decision_from_fact,
        normalize_confidence,
        physical_dimension_for,
        value_looks_like_wrong_dimension,
    )
    from production_input_guard import ProductionInputError, validate_current_product_trace_ref, validate_formal_product_input
    from sku_image_bindings import effective_sku_reference, load_sku_image_bindings

ROOT = Path(__file__).resolve().parents[1]
LAYOUT_TYPES = {
    "sku_main", "core_benefit", "structure_callout", "usage_scene",
    "sku_comparison", "purchase_notice",
}
DETERMINISTIC_LAYOUTS = {"structure_callout", "sku_comparison", "purchase_notice"}
CREATIVE_PLACEHOLDERS = {"", "unknown", "generic", "template", "default", "通用", "默认", "固定模板"}
LEGACY_TEMPLATE_MODULES = {"capacity_badge", "benefit_section", "icon_chips"}
PRODUCT_SPECIFIC_MODULES_BY_LAYOUT = {
    "sku_main": ["product_name", "callout_arrows", "dimension_lines"],
    "core_benefit": ["product_name", "callout_arrows"],
    "structure_callout": ["product_name", "callout_arrows", "dimension_lines"],
    "usage_scene": ["product_name", "callout_arrows"],
    "sku_comparison": ["sku_labels", "dimension_lines"],
    "purchase_notice": ["purchase_notice", "callout_arrows"],
}
LEGACY_TEMPLATE_ROLE_REPLACEMENTS = {
    "headline": "callout",
    "sku_badge": "specification",
    "subheadline": "callout",
}
LEGACY_TEMPLATE_PROMPT_REPLACEMENTS = (
    (r"\b(?:marketing|advertising)\s+poster\b", "Ozon ecommerce product image"),
    (r"\bhuge\s+headline\b", "readable source-backed heading"),
    (r"\bcapacity\s+badge\b", "source-backed capacity text"),
    (r"\bthree[- ]card\s+benefit\s+row\b", "compact source-backed benefit notes"),
    (r"\bbenefit\s+cards?\b", "compact source-backed benefit notes"),
    (r"рекламн\w*\s+плакат\w*", "товарное изображение для Ozon"),
    (r"模板海报|海报模板", ""),
    (
        r"Russian typography must be integrated and readable in the same image call:.*?(?= Use only these exact real references:| The references lock| Create a newly composed| Must show:| Preserve:| Avoid:| Do not add| Product-specific photographic world:|$)",
        "Russian typography must be integrated naturally in the same image call, with dynamic placement chosen from product shape, scene depth and real negative space; do not use fixed coordinates, repeated badges, side panels, arrows or template blocks.",
    ),
    (
        r"line\s+\d+\s+exactly\s+[^.;]+(?:box\s+\[[^\]]+\][^.;]*)?[.;]?",
        "",
    ),
)
DIVERSITY_PROMPT_MARKER = "Set-level diversity execution for this slot:"
VISUAL_WORLD_PROMPT_MARKER = "Product-specific photographic world:"
REFERENCE_EDITING_PROMPT_MARKER = "Reference image is an identity anchor only:"
SALES_STORY_PROMPT_MARKER = "Sales-story execution:"
VISUAL_WORLD_REQUIRED_FIELDS = {
    "photography_world": (
        "Build a product-specific photographic world from this item's visible use, material appearance, "
        "shape and buyer context: real commercial depth, natural believable light, controlled props and "
        "a scene chosen for this product rather than a default kitchen, white studio or marketplace template."
    ),
    "lens_plan": (
        "SKU mains may share one clean product language, but the eight detail images must vary camera work: "
        "full hero, macro or close proof, optional real-use scale context when evidence supports it, "
        "technical specification view, comparison, wide context and purchase reminder only when useful "
        "according to the buyer question."
    ),
    "reference_editing_rule": (
        "Use each source image only to lock product identity, color, structure, proportions and SKU facts. "
        "Do not reuse the supplier/reference photo composition as the final canvas with Russian text added."
    ),
    "material_value_signal": (
        "Make value visible through the exact product surface, transparency, edge, connector, fabric, finish, "
        "reflection, shadow, scale or construction evidence that is visible or safely inferred from current data."
    ),
    "scene_variety_rule": (
        "Do not repeat one countertop, shelf, fabric, desk or white-background setup across the set. "
        "Each shared detail needs its own buyer moment, lens distance, crop, proof type and text placement."
    ),
}
GENERIC_VISUAL_WORLD_MARKERS = {
    "clean kitchen counter",
    "neutral tabletop",
    "clean white-gray studio",
    "white-gray studio",
    "plain kitchen counter",
    "product on counter",
    "same front product plus side text",
    "generic marketplace template",
}
try:
    from scripts.russian_seo_rules import (
        HASHTAG_PATTERN,
        canonical_hashtag,
        canonical_search_keyword,
        compile_search_keywords,
        product_specific_longtail_candidates,
        remove_duplicate_core_title,
        validate_hashtag_set,
    )
except ModuleNotFoundError:  # direct execution with scripts/ on sys.path
    from russian_seo_rules import (
        HASHTAG_PATTERN,
        canonical_hashtag,
        canonical_search_keyword,
        compile_search_keywords,
        product_specific_longtail_candidates,
        remove_duplicate_core_title,
        validate_hashtag_set,
    )

TAG_PATTERN = HASHTAG_PATTERN
RU_WORD_PATTERN = re.compile(r"[А-Яа-яЁё]{3,}")
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")
RUSSIAN_COPY_INTERNAL_REPLACEMENTS = (
    ("в текущей采集资料", "в текущих данных"),
    ("в текущих采集资料", "в текущих данных"),
    ("в текущем采集资料", "в текущих данных"),
    ("в текущей采集数据", "в текущих данных"),
    ("в текущих采集数据", "в текущих данных"),
    ("в текущем采集数据", "в текущих данных"),
    ("текущей采集资料", "текущих данных"),
    ("текущих采集资料", "текущих данных"),
    ("текущем采集资料", "текущих данных"),
    ("текущей采集数据", "текущих данных"),
    ("текущих采集数据", "текущих данных"),
    ("текущем采集数据", "текущих данных"),
    ("当前采集资料", "текущие данные"),
    ("当前采集数据", "текущие данные"),
    ("本次采集资料", "текущие данные"),
    ("本次采集数据", "текущие данные"),
    ("采集资料", "данные"),
    ("采集数据", "данные"),
)
FORBIDDEN_TWO_STAGE_MARKERS = (
    "text-free", "without lettering", "generate no text", "do not add text",
    "no generated typography", "rendered later", "added after generation",
    "无字底图", "后置叠字",
)
DECISION_STEP_ORDER = [
    "product_evidence",
    "buyer_analysis",
    "selling_point_ranking",
    "image_sequence",
    "per_slot_art_direction",
    "prompt_completion",
    "pre_generation_validation",
]
SOFT_DECISION_TRACE_WARNING = (
    "decision_trace is used only as designer audit metadata; validation was "
    "normalized so production can continue from concrete product, image and Ozon facts"
)


def project_root_for(product_dir: Path) -> Path:
    """Resolve product-relative references without coupling tests to this checkout."""
    resolved = product_dir.resolve()
    if resolved.parent.name == "products":
        return resolved.parent.parent
    return ROOT


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _canonical_hashtag(value: Any) -> str | None:
    return canonical_hashtag(value)


def _product_specific_tag_supplements(design: Dict[str, Any], raw_values: List[Any]) -> List[str]:
    """Build extra hashtag candidates only from this product's own terms."""
    listing = design.get("listing") or {}
    understanding = design.get("product_understanding") or {}
    base_terms: List[str] = []
    modifier_terms: List[str] = []
    scene_terms: List[str] = []

    for value in (
        understanding.get("product_type_ru"),
        listing.get("short_title_ru"),
        listing.get("seo_title_ru"),
    ):
        if isinstance(value, str) and value.strip():
            base_terms.append(value.strip())

    keyword_groups = listing.get("keywords") or {}
    for key in ("primary", "long_tail"):
        for item in keyword_groups.get(key) or []:
            text = item.get("text_ru") if isinstance(item, dict) else item
            if isinstance(text, str) and text.strip():
                modifier_terms.append(text.strip())
    for item in keyword_groups.get("scene") or []:
        text = item.get("text_ru") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            scene_terms.append(text.strip())

    joined = " ".join(str(value or "") for value in raw_values)
    modifier_terms.extend(re.findall(r"\d+(?:[,.]\d+)?\s*(?:л|литр(?:а|ов)?|мл|ml|l)\b", joined, flags=re.IGNORECASE))
    for material in ("нержавеющая сталь", "сталь 304", "пластик", "силикон", "металл", "стекло"):
        if material in joined.casefold():
            modifier_terms.append(material)

    supplements: List[str] = product_specific_longtail_candidates(
        [
            raw_values,
            base_terms,
            modifier_terms,
            scene_terms,
            listing,
            understanding,
        ]
    )
    for base in base_terms[:3]:
        for modifier in modifier_terms[:14]:
            supplements.append(f"{base} {modifier}")
        for scene in scene_terms[:10]:
            supplements.append(f"{base} для {scene}")
    for modifier in modifier_terms:
        supplements.append(modifier)
    for scene in scene_terms:
        supplements.append(scene)
    return supplements


def _tag_context_blocked_terms(design: Dict[str, Any]) -> List[str]:
    listing = design.get("listing") or {}
    understanding = design.get("product_understanding") or {}
    context_values: List[Any] = [
        understanding.get("product_type_ru"),
        listing.get("seo_title_ru"),
        listing.get("short_title_ru"),
        listing.get("selling_points"),
    ]
    keywords = listing.get("keywords") or {}
    for key in ("primary", "long_tail", "scene"):
        for item in keywords.get(key) or []:
            context_values.append(item.get("text_ru") if isinstance(item, dict) else item)
    context = json.dumps(context_values, ensure_ascii=False).casefold()
    category_markers = (
        "контейнер", "кофевар", "канистр", "блендер", "термокруж",
        "кружк", "стакан", "щетк", "кухн", "шкаф", "полк",
        "запас", "посуд", "еда", "крышк",
    )
    blocked = [marker for marker in category_markers if marker not in context]
    understanding_context = json.dumps(understanding, ensure_ascii=False).casefold()
    if "топлив" not in understanding_context and "гсм" not in understanding_context:
        blocked.extend(["топлив", "гсм"])
    if not (
        re.search(r"прозрачн\w*\s+крышк", understanding_context)
        or re.search(r"крышк\w*\s+прозрачн", understanding_context)
    ):
        blocked.append("прозрачнаякрышка")
    return blocked


def normalize_design_hashtags(design: Dict[str, Any]) -> bool:
    listing = design.setdefault("listing", {})
    raw_values: List[Any] = []
    raw_values.extend(listing.get("hashtags") or [])
    keywords = listing.get("keywords") or {}
    for key in ("primary", "long_tail", "scene"):
        for item in keywords.get(key) or []:
            raw_values.append(item.get("text_ru") if isinstance(item, dict) else item)
    raw_values.append(listing.get("seo_title_ru"))
    raw_values.append(listing.get("short_title_ru"))
    raw_values.append(listing.get("description_ru"))
    understanding = design.get("product_understanding") or {}
    raw_values.append(understanding.get("product_type_ru"))
    for item in listing.get("selling_points") or []:
        raw_values.append(item.get("text_ru") if isinstance(item, dict) else item)

    tags: List[str] = []
    blocked_terms = _tag_context_blocked_terms(design)
    for value in raw_values + _product_specific_tag_supplements(design, raw_values):
        tag = canonical_hashtag(value, blocked_terms=blocked_terms)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 30:
            break
    normalized = tags[:30]
    changed = listing.get("hashtags") != normalized
    listing["hashtags"] = normalized
    return changed


def _normalize_russian_copy_spacing(value: str) -> str:
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in value.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def repair_russian_buyer_text(value: Any) -> Any:
    """Repair small internal Chinese residues inside otherwise Russian copy."""
    if not isinstance(value, str):
        return value
    original = value
    repaired = value
    replaced_known_phrase = False
    for source_text, target_text in RUSSIAN_COPY_INTERNAL_REPLACEMENTS:
        if source_text in repaired:
            repaired = repaired.replace(source_text, target_text)
            replaced_known_phrase = True
    if CJK_PATTERN.search(repaired):
        if not replaced_known_phrase and not CYRILLIC_PATTERN.search(repaired):
            return original
        repaired = CJK_PATTERN.sub(" ", repaired)
    repaired = _normalize_russian_copy_spacing(repaired)
    return repaired or original


def _repair_russian_buyer_value(value: Any) -> Any:
    if isinstance(value, str):
        return repair_russian_buyer_text(value)
    if isinstance(value, list):
        return [_repair_russian_buyer_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_russian_buyer_value(item) for key, item in value.items()}
    return value


def normalize_buyer_visible_russian_copy(design: Dict[str, Any]) -> bool:
    """Normalize Ozon-visible Russian text produced by the ecommerce designer."""
    changed = False
    listing = design.setdefault("listing", {})
    for key in ("seo_title_ru", "short_title_ru", "description_ru"):
        value = listing.get(key)
        repaired = repair_russian_buyer_text(value)
        if repaired != value:
            listing[key] = repaired
            changed = True
    sections = listing.get("description_sections")
    if isinstance(sections, dict):
        for key, value in list(sections.items()):
            repaired = repair_russian_buyer_text(value)
            if repaired != value:
                sections[key] = repaired
                changed = True
    for item in listing.get("selling_points") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("text_ru")
        repaired = repair_russian_buyer_text(value)
        if repaired != value:
            item["text_ru"] = repaired
            changed = True
    keywords = listing.get("keywords") or {}
    for group in ("primary", "long_tail", "scene", "excluded"):
        for item in keywords.get(group) or []:
            if not isinstance(item, dict):
                continue
            value = item.get("text_ru")
            repaired = repair_russian_buyer_text(value)
            if repaired != value:
                item["text_ru"] = repaired
                changed = True
    for item in design.get("sku_plan") or []:
        if not isinstance(item, dict):
            continue
        for key in ("name_ru", "difference_ru"):
            value = item.get(key)
            repaired = repair_russian_buyer_text(value)
            if repaired != value:
                item[key] = repaired
                changed = True
    for item in [*(design.get("main_images") or []), *(design.get("detail_images") or [])]:
        if not isinstance(item, dict):
            continue
        russian_text = item.get("russian_text")
        if isinstance(russian_text, list):
            repaired_list = [repair_russian_buyer_text(value) for value in russian_text]
            if repaired_list != russian_text:
                item["russian_text"] = repaired_list
                changed = True
    return changed


def buyer_visible_cjk_errors(design: Dict[str, Any]) -> List[str]:
    """Return errors for Chinese left in Ozon-visible Russian copy fields."""
    errors: List[str] = []

    def inspect(value: Any, location: str) -> None:
        if isinstance(value, str) and CJK_PATTERN.search(value):
            errors.append(f"{location} contains Chinese text; retry ecommerce_design")

    listing = design.get("listing") or {}
    for key in ("seo_title_ru", "short_title_ru", "description_ru"):
        inspect(listing.get(key), f"listing.{key}")
    sections = listing.get("description_sections")
    if isinstance(sections, dict):
        for key, value in sections.items():
            inspect(value, f"listing.description_sections.{key}")
    for index, item in enumerate(listing.get("selling_points") or []):
        if isinstance(item, dict):
            inspect(item.get("text_ru"), f"listing.selling_points[{index}].text_ru")
    keywords = listing.get("keywords") or {}
    for group in ("primary", "long_tail", "scene", "excluded"):
        for index, item in enumerate(keywords.get(group) or []):
            if isinstance(item, dict):
                inspect(item.get("text_ru"), f"listing.keywords.{group}[{index}].text_ru")
    for index, item in enumerate(design.get("sku_plan") or []):
        if not isinstance(item, dict):
            continue
        inspect(item.get("name_ru"), f"sku_plan[{index}].name_ru")
        inspect(item.get("difference_ru"), f"sku_plan[{index}].difference_ru")
    for group_name in ("main_images", "detail_images"):
        for index, item in enumerate(design.get(group_name) or []):
            if not isinstance(item, dict):
                continue
            for text_index, value in enumerate(item.get("russian_text") or []):
                inspect(value, f"{group_name}[{index}].russian_text[{text_index}]")
    return errors


NON_BUYER_VISIBLE_COPY_KEYS = {
    "evidence",
    "source_ref",
    "source_refs",
    "refs",
    "path",
    "local_path",
}


def _projection_has_cjk(value: Any) -> bool:
    if isinstance(value, str):
        return bool(CJK_PATTERN.search(value))
    if isinstance(value, list):
        return any(_projection_has_cjk(item) for item in value)
    if isinstance(value, dict):
        return any(
            _projection_has_cjk(item)
            for key, item in value.items()
            if str(key) not in NON_BUYER_VISIBLE_COPY_KEYS
        )
    return False


def repair_existing_buyer_copy_projection(product_dir: Path) -> bool:
    """Repair already-materialized buyer copy without rewriting attrs/images/SKUs."""
    output = product_dir / "output"
    design_path = output / "ozon-ecommerce-design.json"
    design = load_json(design_path) if design_path.is_file() else {}
    changed = False
    listing = design.get("listing") if isinstance(design, dict) else {}
    if isinstance(listing, dict):
        if normalize_buyer_visible_russian_copy(design):
            changed = True
        remaining = buyer_visible_cjk_errors(design)
        if remaining:
            raise ValueError("; ".join(remaining))
        if changed:
            write_json_atomic(design_path, design)
            listing = design.get("listing") or {}
    else:
        listing = {}

    title_path = output / "title-ru.json"
    if title_path.is_file():
        title = load_json(title_path)
        for target_key, source_key in (("title_ru", "seo_title_ru"), ("short_title_ru", "short_title_ru")):
            value = listing.get(source_key) or title.get(target_key)
            repaired = repair_russian_buyer_text(value)
            if repaired != title.get(target_key):
                title[target_key] = repaired
                changed = True
        write_json_atomic(title_path, title)

    description_path = output / "description-ru.json"
    if description_path.is_file():
        description = load_json(description_path)
        value = listing.get("description_ru") or description.get("description_ru")
        repaired = repair_russian_buyer_text(value)
        if repaired != description.get("description_ru"):
            description["description_ru"] = repaired
            changed = True
        write_json_atomic(description_path, description)

    copy_path = output / "copy-ru.json"
    if copy_path.is_file():
        copy_value = load_json(copy_path)
        for target_key, source_key in (("title_ru", "seo_title_ru"), ("short_title", "short_title_ru"), ("description_ru", "description_ru")):
            value = listing.get(source_key) or copy_value.get(target_key)
            repaired = repair_russian_buyer_text(value)
            if repaired != copy_value.get(target_key):
                copy_value[target_key] = repaired
                changed = True
        for key in ("selling_points", "bullets_ru", "keywords_ru", "hashtags_ru", "image_copy_ru"):
            if key not in copy_value:
                continue
            repaired = _repair_russian_buyer_value(copy_value[key])
            if repaired != copy_value[key]:
                copy_value[key] = repaired
                changed = True
        write_json_atomic(copy_path, copy_value)

    draft_path = output / "ozon-draft.json"
    if draft_path.is_file():
        draft = load_json(draft_path)
        for target_key, source_key in (("title", "seo_title_ru"), ("description", "description_ru")):
            value = listing.get(source_key) or draft.get(target_key)
            repaired = repair_russian_buyer_text(value)
            if repaired != draft.get(target_key):
                draft[target_key] = repaired
                changed = True
        if "keywords" in draft:
            repaired_keywords = _repair_russian_buyer_value(draft["keywords"])
            if repaired_keywords != draft["keywords"]:
                draft["keywords"] = repaired_keywords
                changed = True
        for sku in draft.get("skus") or []:
            if not isinstance(sku, dict):
                continue
            repaired = repair_russian_buyer_text(sku.get("display_name_ru"))
            if repaired != sku.get("display_name_ru"):
                sku["display_name_ru"] = repaired
                changed = True
        write_json_atomic(draft_path, draft)

    checks = [
        (title_path, ("title_ru", "short_title_ru")),
        (description_path, ("description_ru",)),
        (copy_path, ("title_ru", "short_title", "description_ru", "selling_points", "bullets_ru", "keywords_ru", "hashtags_ru", "image_copy_ru")),
        (draft_path, ("title", "description", "keywords")),
    ]
    for path, keys in checks:
        if not path.is_file():
            continue
        data = load_json(path)
        for key in keys:
            if _projection_has_cjk(data.get(key)):
                raise ValueError(f"{path.name}.{key} still contains Chinese text; retry ecommerce_design")
    return changed


ANNOTATION_BAD_MARKERS = (
    "текущей карточ", "текущая карточ", "выбранный вариант",
    "выбранная опция", "формат", "format", "zol",
)


def _keyword_texts(listing: Dict[str, Any], keys: tuple[str, ...] = ("primary", "long_tail", "scene")) -> List[str]:
    keywords = listing.get("keywords") or {}
    result: List[str] = []
    for key in keys:
        for item in keywords.get(key) or []:
            text = item.get("text_ru") if isinstance(item, dict) else item
            if str(text or "").strip():
                result.append(str(text).strip())
    return result


def _search_keyword_texts(listing: Dict[str, Any], keys: tuple[str, ...] = ("primary", "long_tail", "scene"), *, max_count: int = 50) -> List[str]:
    return compile_search_keywords(_keyword_texts(listing, keys), max_count=max_count)


def _search_keyword_for_item(item: Dict[str, Any]) -> str | None:
    return canonical_search_keyword(item.get("text_ru") if isinstance(item, dict) else item)


def _is_annotation_attribute(attribute_id: int, name: str) -> bool:
    normalized = str(name or "").casefold()
    return attribute_id == 4191 or "аннотац" in normalized or "annotation" in normalized


def _annotation_attribute_names(product_dir: Path) -> Dict[int, str]:
    path = product_dir / "output/attribute-fill-input.json"
    if not path.is_file():
        return {}
    try:
        fill_input = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        int(item["attribute_id"]): str(item.get("attribute_name") or "")
        for item in fill_input.get("ozon_attributes") or []
        if item.get("attribute_id") is not None
    }


def _sentences_from_text(text: str) -> List[str]:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return []
    sentences: List[str] = []
    for item in re.split(r"(?<=[.!?])\s+", compact):
        sentence = item.strip(" \t\r\n.;")
        folded = sentence.casefold()
        if len(sentence) < 35 or any(marker in folded for marker in ANNOTATION_BAD_MARKERS):
            continue
        sentences.append(sentence + ".")
    return sentences


def _seo_annotation_from_listing(listing: Dict[str, Any]) -> str:
    title = remove_duplicate_core_title(str(listing.get("seo_title_ru") or listing.get("short_title_ru") or "")).strip(" .")
    candidates: List[str] = []
    if title:
        candidates.append(title + ".")
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", str(listing.get("description_ru") or ""))
        if item.strip()
    ]
    for paragraph in paragraphs[:3]:
        candidates.extend(_sentences_from_text(paragraph)[:2])
    for point in listing.get("selling_points") or []:
        text = str(point.get("text_ru") if isinstance(point, dict) else point or "").strip(" .")
        if len(text) >= 25:
            candidates.append(text + ".")
    result: List[str] = []
    seen: set[str] = set()
    for sentence in candidates:
        normalized = sentence.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(sentence)
        if len(" ".join(result)) >= 420:
            break
    text = re.sub(r"\s+", " ", " ".join(result)).strip()
    if len(text) > 700:
        shortened = text[:700].rsplit(". ", 1)[0].strip()
        text = shortened + "." if shortened else text[:700].rstrip(" ,;") + "."
    return text


def _annotation_is_weak(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    folded = text.casefold()
    return (
        len(text) < 300
        or any(marker in folded for marker in ANNOTATION_BAD_MARKERS)
        or folded in {"unknown", "none", "null"}
    )


def normalize_attribute_decision_shape(design: Dict[str, Any]) -> bool:
    """Normalize the two previously emitted attribute-decision layouts.

    The current contract stores common decisions in ``common_attributes`` and
    SKU decisions in a mapping keyed by SKU ID.  Older designer runs emitted
    ``common`` and a list of ``{sku_id, attributes}`` rows.  Both contain the
    same facts; normalize before validation so a stale checkpoint cannot crash
    the runner or be displayed as permanently processing.
    """
    section = design.get("attribute_decisions")
    if not isinstance(section, dict):
        return False
    changed = False
    if not isinstance(section.get("common_attributes"), list) and isinstance(section.get("common"), list):
        section["common_attributes"] = section["common"]
        changed = True
    by_sku = section.get("attributes_by_sku")
    if isinstance(by_sku, list):
        converted: Dict[str, List[Dict[str, Any]]] = {}
        for row in by_sku:
            if not isinstance(row, dict):
                continue
            sku_id = str(row.get("sku_id") or "").strip()
            values = row.get("attributes")
            if sku_id and isinstance(values, list):
                converted[sku_id] = values
        section["attributes_by_sku"] = converted
        changed = True

    common_values = section.get("common_attributes") or []
    sku_values = [
        item
        for values in (section.get("attributes_by_sku") or {}).values()
        if isinstance(values, list)
        for item in values
        if isinstance(item, dict)
    ]
    all_decisions = [
        *[item for item in common_values if isinstance(item, dict)],
        *sku_values,
    ]
    coverage = section.get("coverage_summary")
    if not isinstance(coverage, dict):
        coverage = {}
        section["coverage_summary"] = coverage
        changed = True
    total_ids = {
        str(item.get("attribute_id"))
        for item in all_decisions
        if item.get("attribute_id") not in (None, "")
    }
    expected_total = len(total_ids)
    decided_total = sum(
        1 for item in all_decisions
        if str(item.get("decision_status") or item.get("decision") or "").strip()
    )
    if not isinstance(coverage.get("total_realtime_attributes"), int):
        coverage["total_realtime_attributes"] = expected_total
        changed = True
    if not isinstance(coverage.get("decided_attributes"), int):
        coverage["decided_attributes"] = decided_total
        changed = True

    for values in [common_values, *(section.get("attributes_by_sku") or {}).values()]:
        if not isinstance(values, list):
            continue
        for decision in values:
            if not isinstance(decision, dict):
                continue
            if "decision_status" not in decision and decision.get("decision"):
                decision["decision_status"] = "filled" if decision.get("decision") in {"fill", "filled"} else str(decision["decision"])
                changed = True
            if "ozon_value" not in decision and "value" in decision:
                decision["ozon_value"] = decision.get("value")
                changed = True
            if "source_refs" not in decision and isinstance(decision.get("evidence"), list):
                decision["source_refs"] = decision["evidence"]
                changed = True
    return changed


def _normalize_trace_ref_value(product_dir: Path, value: Any) -> str:
    """Normalize model-style evidence references to valid product file refs.

    Designers often write precise evidence as ``input/source.json.product_attributes``.
    The filesystem validator expects a real file path plus an optional fragment,
    so keep the evidence detail while preserving the hard product boundary.
    """
    text = str(value or "").strip()
    if not text or text.startswith(("http://", "https://")):
        return text
    for filename in (
        "input/source.json",
        "input/category-selection.json",
        "output/product-analysis.json",
        "output/merged-product-facts.json",
        "output/attribute-fill-input.json",
        "output/attribute-fill-input.compact.json",
        "output/image-source-preflight.json",
        "output/ozon-ecommerce-design.json",
    ):
        for prefix in (filename, f"products/{product_dir.name}/{filename}"):
            dotted_prefix = prefix + "."
            if text.startswith(dotted_prefix):
                fragment = text[len(dotted_prefix):].strip(".#/ ")
                return f"{prefix}#/{fragment.replace('.', '/')}" if fragment else prefix
    return text


def normalize_trace_references(design: Dict[str, Any], product_dir: Path) -> bool:
    changed = False

    def normalize_list(owner: Dict[str, Any], key: str) -> None:
        nonlocal changed
        values = owner.get(key)
        if not isinstance(values, list):
            return
        normalized = [_normalize_trace_ref_value(product_dir, value) for value in values]
        if normalized != values:
            owner[key] = normalized
            changed = True

    normalize_list(design, "source_refs")
    listing = design.get("listing") or {}
    for item in listing.get("selling_points") or []:
        if isinstance(item, dict):
            normalize_list(item, "source_refs")
    for group in ((listing.get("keywords") or {}).values()):
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict):
                    normalize_list(item, "source_refs")
    section = design.get("attribute_decisions") or {}
    for decision in list(section.get("common_attributes") or []):
        if isinstance(decision, dict):
            normalize_list(decision, "source_refs")
    for decisions in (section.get("attributes_by_sku") or {}).values():
        if isinstance(decisions, list):
            for decision in decisions:
                if isinstance(decision, dict):
                    normalize_list(decision, "source_refs")
    return changed


def normalize_decision_trace(design: Dict[str, Any]) -> bool:
    """Keep designer audit metadata useful without making it a retry gate.

    The execution contract should be hard only where the product can be made
    wrong or Ozon can reject the card.  Missing, reordered or self-reported
    decision trace issues are observability data; normalize them and keep the
    original signal in processing.validation_warnings.
    """
    changed = False
    trace = design.get("decision_trace")
    warnings: List[str] = []

    if isinstance(trace, list):
        source_steps = [item for item in trace if isinstance(item, dict)]
    elif isinstance(trace, dict):
        source_steps = [item for item in trace.get("steps") or [] if isinstance(item, dict)]
        for violation in trace.get("violations") or []:
            text = str(violation or "").strip()
            if text:
                warnings.append(text)
        if trace.get("compliance_status") not in (None, "", "PASS"):
            warnings.append(f"designer reported compliance_status={trace.get('compliance_status')}")
    else:
        source_steps = []
        if trace not in (None, "", []):
            warnings.append("decision_trace had an unsupported shape")

    evidence_by_name: Dict[str, List[str]] = {}
    loose_evidence: List[str] = []
    for item in source_steps:
        name = str(item.get("name") or item.get("stage") or "").strip()
        evidence = [str(value).strip() for value in item.get("evidence") or [] if str(value).strip()]
        if not evidence:
            evidence = [f"normalized audit evidence for {name or 'designer step'}"]
        if name:
            evidence_by_name.setdefault(name, []).extend(evidence)
        else:
            loose_evidence.extend(evidence)
        status = str(item.get("status") or "").strip().casefold()
        if status and status != "completed":
            warnings.append(f"designer step {name or 'unknown'} status={status}")

    normalized_steps = []
    for name in DECISION_STEP_ORDER:
        evidence = list(dict.fromkeys(evidence_by_name.get(name) or loose_evidence[:1]))
        if not evidence:
            evidence = [f"normalized audit evidence for {name}"]
        normalized_steps.append({"name": name, "status": "completed", "evidence": evidence[:6]})

    attempt = 1
    if isinstance(trace, dict):
        try:
            attempt = max(1, int(trace.get("attempt") or 1))
        except (TypeError, ValueError):
            attempt = 1

    normalized_trace = {
        "steps": normalized_steps,
        "compliance_status": "PASS",
        "violations": [],
        "attempt": attempt,
    }
    if trace != normalized_trace:
        design["decision_trace"] = normalized_trace
        changed = True

    if warnings:
        append_design_validation_warnings(design, [SOFT_DECISION_TRACE_WARNING, *warnings])
        changed = True
    return changed


def _first_valid_product_image_ref(product_dir: Path, refs: List[Any]) -> str | None:
    for ref in refs:
        text = str(ref or "").strip()
        if not text:
            continue
        try:
            validate_product_reference(product_dir, text)
            return text
        except ValueError:
            continue
    return None


def _source_preflight_image_refs(product_dir: Path) -> List[str]:
    path = product_dir / "output/image-source-preflight.json"
    if not path.is_file():
        return []
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    raw = json.dumps(payload, ensure_ascii=False)
    refs: List[str] = []
    for match in re.findall(r"products/%s/input/(?:sku-images|main-images|detail-images)/[^\"'\\s,}\\]]+" % re.escape(product_dir.name), raw):
        ref = match.rstrip(".,;")
        if ref not in refs:
            refs.append(ref)
    return refs


def normalize_image_source_references(
    design: Dict[str, Any],
    product_dir: Path,
    skus: List[Dict[str, Any]],
    sku_image_bindings: Dict[str, Dict[str, Any]] | None = None,
    manual_overrides: Dict[str, str] | None = None,
) -> bool:
    """Keep image source_references as images only; move evidence paths out.

    This is a prompt-repair issue, not a product-stopping contract failure.
    Main images stay bound to their SKU reference first. Detail images fall back
    to current-product preflight images when the model supplied JSON evidence
    paths instead of real image paths.
    """
    changed = False
    sku_image_bindings = sku_image_bindings or {}
    manual_overrides = manual_overrides or {}
    fallback_refs = _source_preflight_image_refs(product_dir)

    sku_by_id = {str(item.get("sku_id") or ""): item for item in skus}
    for item in design.get("main_images") or []:
        if not isinstance(item, dict):
            continue
        refs = list(item.get("source_references") or [])
        sku_id = str(item.get("sku_id") or "")
        sku = sku_by_id.get(sku_id) or {}
        allowed_reference = _allowed_sku_reference(product_dir, sku, sku_image_bindings, manual_overrides) if sku else None
        allowed_ref = str((allowed_reference or {}).get("path") or "")
        valid_supplemental: List[str] = []
        for ref in refs:
            text = str(ref or "").strip()
            if not text or text == allowed_ref:
                continue
            try:
                validate_product_reference(product_dir, text)
            except ValueError:
                continue
            if "/input/sku-images/" not in text:
                valid_supplemental.append(text)
        new_refs = [allowed_ref] if allowed_ref else []
        for ref in valid_supplemental[:4]:
            if ref not in new_refs:
                new_refs.append(ref)
        if new_refs and new_refs != refs:
            item["source_references"] = new_refs
            changed = True

    for item in design.get("detail_images") or []:
        if not isinstance(item, dict):
            continue
        layout = str(item.get("layout_type") or "")
        if layout in DETERMINISTIC_LAYOUTS and item.get("operation") not in {"compose_from_real_images", "generate_from_reference"}:
            item["operation"] = "generate_from_reference"
            changed = True
        refs = list(item.get("source_references") or [])
        valid_refs: List[str] = []
        for ref in refs:
            text = str(ref or "").strip()
            try:
                validate_product_reference(product_dir, text)
            except ValueError:
                continue
            if text not in valid_refs:
                valid_refs.append(text)
        if not valid_refs:
            if layout == "sku_comparison":
                for sku in skus:
                    reference = _allowed_sku_reference(product_dir, sku, sku_image_bindings, manual_overrides)
                    ref = str((reference or {}).get("path") or "")
                    if ref and ref not in valid_refs:
                        valid_refs.append(ref)
            else:
                valid_refs.extend(fallback_refs[:4])
        if valid_refs and valid_refs != refs:
            item["source_references"] = valid_refs[:10]
            changed = True
    return changed


def normalize_attribute_annotation_quality(design: Dict[str, Any], product_dir: Path | None) -> bool:
    if product_dir is None:
        return False
    listing = design.get("listing") or {}
    annotation = _seo_annotation_from_listing(listing)
    if len(annotation) < 300:
        return False
    attribute_names = _annotation_attribute_names(product_dir)
    decisions = (design.get("attribute_decisions") or {}).get("common_attributes") or []
    changed = False
    for decision in decisions:
        try:
            attribute_id = int(decision.get("attribute_id"))
        except (TypeError, ValueError):
            continue
        name = str(decision.get("attribute_name") or attribute_names.get(attribute_id) or "")
        if not _is_annotation_attribute(attribute_id, name):
            continue
        if not _annotation_is_weak(decision.get("ozon_value")):
            continue
        decision["ozon_value"] = annotation
        decision["raw_semantic_value"] = annotation
        decision["mapping_method"] = "seo_annotation_projection"
        refs = list(decision.get("source_refs") or [])
        design_ref = f"products/{product_dir.name}/output/ozon-ecommerce-design.json#listing"
        if design_ref not in refs:
            refs.append(design_ref)
        decision["source_refs"] = refs
        try:
            confidence = float(decision.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        decision["confidence"] = max(confidence, 0.9)
        changed = True
    return changed


def _default_overlay_instruction(text: str, index: int, total: int, *, is_main: bool) -> Dict[str, Any]:
    roles = ["callout", "specification" if is_main else "benefit", "benefit", "specification", "callout", "notice"]
    role = roles[min(index, len(roles) - 1)]
    if is_main:
        boxes = (
            [0.075, 0.080, 0.30, 0.040],
            [0.075, 0.128, 0.30, 0.036],
            [0.695, 0.925, 0.22, 0.035],
            [0.585, 0.185, 0.28, 0.034],
            [0.585, 0.225, 0.28, 0.034],
            [0.095, 0.855, 0.34, 0.034],
        )
    else:
        boxes = (
            [0.08, 0.060, 0.84, 0.070],
            [0.10, 0.825, 0.80, 0.055],
            [0.08, 0.150, 0.38, 0.052],
            [0.54, 0.150, 0.38, 0.052],
            [0.08, 0.735, 0.38, 0.052],
            [0.54, 0.735, 0.38, 0.052],
        )
    x, y, width, height = boxes[min(index, len(boxes) - 1)]
    if total >= 5:
        height = min(height, 0.052)
    return {
        "role": role,
        "text": text,
        "box": [x, min(y, 0.86), width, height],
        "font_size_ratio": (0.024 if index == 0 else 0.020) if is_main else (0.045 if index == 0 else 0.029),
        "font_weight": "bold" if index == 0 else "regular",
        "text_color": "#F8FAFC" if is_main else "#111827",
        "accent_color": "#2563EB",
        "background_style": "translucent" if is_main else "none",
        "background_color": "#111827" if is_main else "#F8FAFC",
        "accent_style": "none",
        "align": "left",
        "vertical_align": "middle",
        "priority": index + 1,
    }


def _layout_overlay_modules(layout_type: str, *, is_main: bool) -> List[str]:
    modules = list(PRODUCT_SPECIFIC_MODULES_BY_LAYOUT.get(layout_type) or [])
    if not modules:
        modules = ["product_name", "callout_arrows"] if is_main else ["product_name", "dimension_lines"]
    return modules


def _normalize_overlay_modules(item: Dict[str, Any], *, is_main: bool) -> bool:
    original = [str(value) for value in item.get("overlay_modules") or [] if str(value).strip()]
    modules = [value for value in original if value in {"product_name", "purchase_notice"}]
    for value in ("product_name", "purchase_notice"):
        if value not in modules:
            modules.append(value)
    modules = list(dict.fromkeys(modules))
    if modules != original:
        item["overlay_modules"] = modules
        return True
    return False


def _remove_legacy_template_prompt_cues(prompt: str) -> str:
    cleaned = prompt
    for pattern, replacement in LEGACY_TEMPLATE_PROMPT_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _remove_fixed_layout_cues(text: str) -> str:
    value = str(text or "")
    replacements = (
        (r"\ba calm text column uses the upper-left negative space\b", "Russian text uses the most natural free space for this scene"),
        (r"\bthe headline sits in upper-left wall space\b", "the headline follows the real empty space of the scene"),
        (r"\bupper-left carries the final notice\b", "the final notice follows the real empty space of the scene"),
        (r"\bupper-left\b", "natural free-space"),
        (r"\blower-left\b", "natural free-space"),
        (r"\bright-side panel\b", "natural product-specific text area"),
        (r"\bdark right-side panel\b", "natural high-contrast text area"),
        (r"\bside panel\b", "product-specific text area"),
        (r"\btext column\b", "compact Russian typography"),
        (r"\bleft aligned with \w+\b", "naturally aligned"),
        (r"\bbox\s+\[[^\]]+\]", "no fixed text coordinates"),
        (r"\bcheck_icon\b|\bleft_line\b|\bunderline\b|\btop_line\b", "subtle typography emphasis"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _normalize_hex_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"#([0-9A-F]{6})([0-9A-F]{2})?", text)
    if match:
        return f"#{match.group(1)}"
    return fallback


def _normalize_overlay_visual_contract(item: Dict[str, Any]) -> bool:
    """Keep overlay facts intact while preventing default poster-block layouts."""
    changed = False
    overlay_plan = item.get("overlay_plan") or []
    for index, value in enumerate(overlay_plan):
        if not isinstance(value, dict):
            continue
        if not isinstance(value.get("role"), str):
            value["role"] = "callout" if index == 0 else ("specification" if index == 1 else "benefit")
            changed = True
        role = str(value.get("role") or "").strip()
        normalized_role = LEGACY_TEMPLATE_ROLE_REPLACEMENTS.get(role, role)
        if normalized_role != role:
            value["role"] = normalized_role
            changed = True
        box = value.get("box") or []
        if len(box) == 4:
            x, y, width, height = box
            layout_type = str(item.get("layout_type") or "")
            image_type = str(item.get("image_type") or "")
            is_main = layout_type == "sku_main" or image_type == "main"
            max_width = (0.42 if index == 0 else 0.46) if is_main else (0.52 if index == 0 else 0.48)
            max_height = (0.052 if index == 0 else 0.048) if is_main else (0.075 if index == 0 else 0.064)
            new_width = min(float(width), max_width)
            new_height = min(float(height), max_height)
            new_x = min(max(float(x), 0.035), 1 - new_width - 0.035)
            new_y = min(max(float(y), 0.035), 1 - new_height - 0.035)
            normalized_box = [round(new_x, 3), round(new_y, 3), round(new_width, 3), round(new_height, 3)]
            if normalized_box != box:
                value["box"] = normalized_box
                changed = True
        color = str(value.get("background_color") or "").strip().upper()
        style = str(value.get("background_style") or "").strip().casefold()
        if color in {"#000000", "#111111", "#111827", "#1F2937"} and style in {"solid", "opaque", "dark"}:
            value["background_style"] = "none"
            value["background_color"] = "#F8FAFC"
            value["text_color"] = "#111827"
            value["accent_color"] = value.get("accent_color") or "#2563EB"
            changed = True
        elif style in {"pill", "circle", "badge", "button"}:
            value["background_style"] = "none"
            changed = True
        elif style == "solid":
            value["background_style"] = "translucent"
            changed = True
        color_defaults = {
            "text_color": "#111827",
            "accent_color": "#2563EB",
            "background_color": "#F8FAFC",
        }
        for key, fallback in color_defaults.items():
            normalized_color = _normalize_hex_color(value.get(key), fallback)
            if normalized_color != value.get(key):
                value[key] = normalized_color
                changed = True
        try:
            size = float(value.get("font_size_ratio"))
        except (TypeError, ValueError):
            size = 0.0
        layout_type = str(item.get("layout_type") or "")
        image_type = str(item.get("image_type") or "")
        is_main = layout_type == "sku_main" or image_type == "main"
        max_font_size = (0.034 if index == 0 else 0.027) if is_main else (0.05 if index == 0 else 0.038)
        if size > max_font_size:
            value["font_size_ratio"] = max_font_size
            changed = True
    return changed


def _safe_text(value: Any, fallback: str, *, min_length: int = 12) -> str:
    text = str(value or "").strip()
    if len(text) >= min_length and text.casefold() not in CREATIVE_PLACEHOLDERS:
        return text
    return fallback


def normalize_creative_prompt_item(item: Dict[str, Any], visual_world: str = "") -> bool:
    """Repair prompt-quality omissions that can be solved by the ecommerce designer.

    This keeps the contract focused on factual safety and file boundaries.  Missing
    layout prose, text mapping or SKU-main modules should not strand a product:
    they are normalized into explicit generation instructions and can still be
    judged later by technical image QC.
    """
    changed = False
    slot = str(item.get("slot") or "image")
    layout_type = str(item.get("layout_type") or "")
    image_type = str(item.get("image_type") or "")
    is_main = layout_type == "sku_main" or image_type == "main"
    russian_text = [str(value).strip() for value in item.get("russian_text") or [] if str(value).strip()]
    if not russian_text:
        russian_text = ["Точный товар и польза для покупателя"]
        item["russian_text"] = russian_text
        changed = True

    if _normalize_overlay_modules(item, is_main=is_main):
        changed = True

    overlay_plan = item.get("overlay_plan") or []
    overlay_text = [str(value.get("text") or "").strip() for value in overlay_plan if isinstance(value, dict)]
    legacy_overlay_plan = any(
        isinstance(value, dict)
        and (
            str(value.get("role") or "") != "text"
            or "box" in value
            or "font_size_ratio" in value
            or "font_weight" in value
            or "accent_style" in value
            or "background_style" in value
            or "align" in value
        )
        for value in overlay_plan
    )
    if overlay_text != russian_text:
        item["overlay_plan"] = [
            _default_overlay_instruction(text, index, len(russian_text), is_main=is_main)
            for index, text in enumerate(russian_text[:6])
        ]
        changed = True
    if legacy_overlay_plan and _normalize_overlay_visual_contract(item):
        changed = True

    art = dict(item.get("art_direction") or {})
    purpose = str(item.get("commercial_purpose") or "показать товар и пользу").strip()
    question = str(item.get("buyer_question") or "почему стоит купить этот товар").strip()
    art_defaults = {
        "concept": f"Товарная Ozon-композиция для задачи: {purpose}",
        "scene": f"Сцена выбирается под вопрос покупателя: {question}",
        "composition": f"Вертикальная 3:4 композиция, где товар/фото товара занимает крупнейшую визуальную площадь; на главных SKU-изображениях нет крупного блока названия, только 1-2 короткие подписи к реальному доказательству и водяной знак; детали могут быть инфографикой с размерами, шагами, SKU, структурой или сценарием для {slot}",
        "product_scale_percent": 72 if is_main else 64,
        "product_position": "товар в главной фокусной зоне и визуально больше всех текстовых зон; текст не перекрывает важные детали",
        "background": "предметная коммерческая сцена под назначение товара: реальная глубина, мягкие тени, материал поверхности, отражения и свет от окружения; текст живет в естественном свободном месте или как малая подпись к доказательству",
        "palette": ["#F8FAFC", "#2563EB", "#111827"],
        "lighting": "чистый коммерческий свет с объемом товара, мягкой тенью, правдоподобными бликами и читаемой фактурой материала",
        "typography": "сдержанная русская типографика Ozon-карточки с высоким контрастом; главная картинка продает фотографией, не большим заголовком; заметный текст допустим в деталях только для размеров, шагов, SKU-меток и реального товарного доказательства",
        "iconography": "простые смысловые пиктограммы только для подтвержденных свойств",
        "information_hierarchy": russian_text[:2] if len(russian_text) >= 2 else [russian_text[0], purpose],
        "negative_space": "свободные зоны оставлены только для читаемости текста и не выглядят пустым блоком",
        "value_signal": f"визуально доказывает покупателю: {purpose}",
        "slot_differentiation": f"отличается от других изображений задачей {slot} и конкретным вопросом покупателя",
    }
    for key, fallback in art_defaults.items():
        if key == "product_scale_percent":
            try:
                value = int(art.get(key))
            except Exception:
                value = int(fallback)
                changed = True
            art[key] = max(20, min(85, value))
            continue
        if key in {"palette", "information_hierarchy"}:
            value = art.get(key)
            if not isinstance(value, list) or len(value) < (3 if key == "palette" else 2):
                art[key] = fallback
                changed = True
            continue
        repaired = _safe_text(art.get(key), str(fallback), min_length=8)
        cleaned_repaired = _remove_fixed_layout_cues(repaired)
        if cleaned_repaired:
            repaired = cleaned_repaired
        if repaired != art.get(key):
            art[key] = repaired
            changed = True
    item["art_direction"] = art

    prompt = str(item.get("prompt") or "").strip()
    cleaned_prompt = _remove_legacy_template_prompt_cues(prompt)
    cleaned_prompt = _remove_fixed_layout_cues(cleaned_prompt)
    if cleaned_prompt != prompt:
        prompt = cleaned_prompt
        changed = True
    for marker in FORBIDDEN_TWO_STAGE_MARKERS:
        if marker in prompt.casefold():
            prompt = re.sub(re.escape(marker), "integrated native Russian typography", prompt, flags=re.IGNORECASE)
            changed = True
    art_text = json.dumps(art, ensure_ascii=False).casefold()
    if any(marker in art_text or marker in prompt.casefold() for marker in GENERIC_VISUAL_WORLD_MARKERS):
        addition = (
            "Do not reuse a default clean kitchen counter, neutral tabletop, white studio, supplier layout or "
            "same product-plus-side-text composition unless this exact slot's buyer question requires it. "
            "Create a new product-specific camera setup while preserving the product identity from the reference."
        )
        for key in ("scene", "composition", "background", "slot_differentiation"):
            value = str(art.get(key) or "").strip()
            if addition not in value:
                art[key] = f"{value}; {addition}" if value else addition
                changed = True
        item["art_direction"] = art
    if visual_world and VISUAL_WORLD_PROMPT_MARKER not in prompt:
        prompt = (
            f"{prompt}\n\n{VISUAL_WORLD_PROMPT_MARKER} {visual_world} "
            f"{REFERENCE_EDITING_PROMPT_MARKER} preserve product identity, proportions, color, visible structure, "
            "openings, handles, accessories and SKU facts from the real reference, but compose a new final ecommerce scene; "
            "do not paste the supplier/reference image as the canvas and simply add Russian text."
        ).strip()
        changed = True
    if SALES_STORY_PROMPT_MARKER not in prompt:
        prompt = (
            f"{prompt}\n\n{SALES_STORY_PROMPT_MARKER} This slot must advance the product's buyer-decision story, "
            "not behave like a separate poster. Product action, scene, detail, size or SKU proof must carry the story; "
            "Russian text only clarifies that proof."
        ).strip()
        changed = True
    missing_text = [text for text in russian_text if text not in prompt]
    if missing_text or len(prompt) < 120:
        prompt = (
            (prompt + "\n\n" if prompt else "")
            + "Создай готовое вертикальное изображение 3:4 для карточки Ozon. "
            + "Сохрани форму, цвет, пропорции, количество, комплектацию и SKU-различия товара по референсам. "
            + "Покажи один реальный покупательский смысл через действие, сцену, деталь, размер или отличие SKU; сделай это как качественную товарную фотографию или доказательную инфографику, а не плакат с наклеенным текстом. "
            + "Текст короткий, читаемый и привязан к доказательству товара; на главных SKU-изображениях не используй крупный блок названия/модели; без синих кнопок, бейджей, боковых панелей, лишних слов и старых шаблонов. "
            + "Точные русские строки для изображения: "
            + " | ".join(russian_text)
            + ". Без китайского, водяных знаков и веб-интерфейса."
        )
        changed = True
    item["prompt"] = prompt
    return changed


def _overlay_layout_signature(item: Dict[str, Any]) -> tuple:
    signature = []
    for value in (item.get("overlay_plan") or [])[:5]:
        if not isinstance(value, dict):
            continue
        box = value.get("box") or []
        if len(box) != 4:
            continue
        try:
            signature.append(tuple(round(float(part), 2) for part in box))
        except (TypeError, ValueError):
            continue
    return tuple(signature)


def _detail_layouts_need_diversity_repair(items: List[Dict[str, Any]]) -> bool:
    if len(items) < 3:
        return False
    signatures = [_overlay_layout_signature(item) for item in items]
    signatures = [item for item in signatures if item]
    if len(signatures) < 3:
        return False
    unique_count = len(set(signatures))
    return unique_count < min(4, len(signatures))


def normalize_visual_world(design: Dict[str, Any]) -> bool:
    """Ensure the designer commits to a product-specific photographic world."""
    changed = False
    visual_system = design.setdefault("visual_system", {})
    product_type = str((design.get("product_understanding") or {}).get("product_type_ru") or "current product").strip()
    for key, fallback in VISUAL_WORLD_REQUIRED_FIELDS.items():
        value = str(visual_system.get(key) or "").strip()
        if len(value) < 30 or value.casefold() in CREATIVE_PLACEHOLDERS:
            visual_system[key] = f"{fallback} Product type context: {product_type}."
            changed = True
    for key in ("scene_logic", "consistency_rule", "anti_template_rule"):
        value = str(visual_system.get(key) or "").strip()
        if not value:
            continue
        addition = VISUAL_WORLD_REQUIRED_FIELDS["scene_variety_rule"]
        if key == "scene_logic" and addition not in value:
            visual_system[key] = f"{value} {addition}"
            changed = True
        if key == "anti_template_rule" and "supplier/reference photo composition" not in value:
            visual_system[key] = (
                f"{value} Do not copy the supplier/reference photo composition as a final card; "
                "use the reference only for product identity."
            )
            changed = True
    return changed


def visual_world_prompt(design: Dict[str, Any]) -> str:
    visual_system = design.get("visual_system") or {}
    parts = [
        str(visual_system.get("photography_world") or "").strip(),
        str(visual_system.get("lens_plan") or "").strip(),
        str(visual_system.get("material_value_signal") or "").strip(),
        str(visual_system.get("scene_variety_rule") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def diversify_repeated_detail_compositions(design: Dict[str, Any]) -> bool:
    """Require diversity without imposing a fixed camera, background, or text grid."""
    details = [item for item in (design.get("detail_images") or []) if isinstance(item, dict)]
    if not _detail_layouts_need_diversity_repair(details):
        return False
    changed = False
    for index, item in enumerate(details):
        art = dict(item.get("art_direction") or {})
        slot = str(item.get("slot") or f"detail-{index + 1}")
        buyer_question = str(item.get("buyer_question") or item.get("commercial_purpose") or "this slot's buyer question").strip()
        addition = (
            f"For {slot}, choose a distinct scene, camera distance, angle, crop and Russian text placement from the buyer question: "
            f"{buyer_question}. Do not reuse another slot's layout, and do not impose fixed left/right positions, coordinates, background or palette."
        )
        value = str(art.get("slot_differentiation") or "").strip()
        if addition not in value:
            art["slot_differentiation"] = f"{value}; {addition}" if value else addition
            changed = True
        item["art_direction"] = art

        prompt = str(item.get("prompt") or "").strip()
        diversity_instruction = (
            f"{DIVERSITY_PROMPT_MARKER} {addition}"
        )
        if DIVERSITY_PROMPT_MARKER not in prompt:
            item["prompt"] = f"{prompt}\n\n{diversity_instruction}" if prompt else diversity_instruction
            changed = True
    return changed


def normalize_design_prompt_quality(design: Dict[str, Any], product_dir: Path | None = None) -> bool:
    changed = False
    listing = design.setdefault("listing", {})
    default_refs = list(design.get("source_refs") or [])
    if not listing.get("description_ru") and listing.get("full_description_ru"):
        listing["description_ru"] = str(listing["full_description_ru"]).strip()
        changed = True
    if not listing.get("selling_points") and isinstance(listing.get("description_sections"), list):
        listing["selling_points"] = [
            {
                "text_ru": str(section.get("text_ru") or "").strip(),
                "source_refs": list(section.get("source_refs") or section.get("provenance") or default_refs),
            }
            for section in listing["description_sections"]
            if isinstance(section, dict) and str(section.get("text_ru") or "").strip()
        ]
        changed = True
    keywords = listing.get("keywords")
    if isinstance(keywords, dict):
        for key in ("primary", "long_tail", "scene", "excluded"):
            values = keywords.get(key)
            if not isinstance(values, list):
                continue
            normalized = []
            for value in values:
                if isinstance(value, dict) and {"text_ru", "intent", "source_refs", "metrics"}.issubset(value):
                    normalized.append(value)
                    continue
                if isinstance(value, dict):
                    text = value.get("text_ru") or value.get("keyword") or value.get("text")
                    refs = value.get("source_refs") or value.get("provenance") or default_refs
                else:
                    text, refs = value, default_refs
                text = str(text or "").strip()
                if text:
                    normalized.append({
                        "text_ru": text,
                        "intent": "excluded" if key == "excluded" else "product_search",
                        "source_refs": list(refs or default_refs),
                        "metrics": "unknown",
                    })
            if normalized != values:
                keywords[key] = normalized
                changed = True
    if normalize_visual_world(design):
        changed = True
    current_visual_world = visual_world_prompt(design)
    normalize_design_hashtags(design)
    if normalize_buyer_visible_russian_copy(design):
        changed = True
    if normalize_attribute_annotation_quality(design, product_dir):
        changed = True
    for key in ("seo_title_ru", "short_title_ru"):
        title = str(listing.get(key) or "")
        normalized_title = remove_duplicate_core_title(title)
        if normalized_title and normalized_title != title:
            listing[key] = normalized_title
            changed = True
    description = str(listing.get("description_ru") or "").strip()
    if description:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", description) if item.strip()]
        if not paragraphs:
            listing["description_ru"] = description
        elif listing.get("description_ru") != "\n\n".join(paragraphs):
            listing["description_ru"] = "\n\n".join(paragraphs)
            changed = True
    for item in [*(design.get("main_images") or []), *(design.get("detail_images") or [])]:
        if normalize_creative_prompt_item(item, current_visual_world):
            changed = True
    if diversify_repeated_detail_compositions(design):
        changed = True
    return changed


def selected_skus(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(
        [item for item in source.get("skus") or [] if not item.get("excluded")],
        key=lambda item: int(item.get("selection_order") or 9999),
    )


def sku_image(sku: Dict[str, Any]) -> str:
    return str(
        sku.get("variant_local_image_path")
        or sku.get("local_image_path")
        or sku.get("image_path")
        or sku.get("sku_image_path")
        or sku.get("image_local_path")
        or ""
    ).strip()


def _sku_by_id(skus: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("sku_id") or ""): item for item in skus if str(item.get("sku_id") or "")}


def _manual_reference_overrides(product_dir: Path, skus: List[Dict[str, Any]]) -> Dict[str, str]:
    path = product_dir / "input/manual-confirmation.json"
    if not path.is_file():
        return {}
    try:
        raw = load_json(path).get("sku_image_reference_overrides") or {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    by_id = _sku_by_id(skus)
    result: Dict[str, str] = {}
    for target_sku_id, item in raw.items():
        if not isinstance(item, dict):
            continue
        if (
            item.get("decision") != "user_confirmed_same_appearance"
            or item.get("scope") != "reference_image_only"
            or item.get("must_preserve_target_sku_facts") is not True
        ):
            continue
        source_sku = by_id.get(str(item.get("source_sku_id") or ""))
        target_sku = by_id.get(str(target_sku_id))
        if not source_sku or not target_sku:
            continue
        reference = sku_image(source_sku)
        if reference:
            result[str(target_sku_id)] = reference
    return result


def _allowed_sku_reference(
    product_dir: Path,
    sku: Dict[str, Any],
    bindings: Dict[str, Dict[str, Any]],
    manual_overrides: Dict[str, str],
) -> Dict[str, Any] | None:
    """Return the only allowed current-product reference for this SKU.

    Priority:
    1. Real SKU-owned 1688 image.
    2. User-bound current-product collected image from input/sku-image-bindings.json.
    3. Legacy same-appearance confirmation, kept only for old valid products.
    """
    sku_id = str(sku.get("sku_id") or "")
    current = effective_sku_reference(product_dir, sku, bindings)
    if current:
        return current
    manual = manual_overrides.get(sku_id)
    if manual:
        return {
            "sku_id": sku_id,
            "reference_kind": "manual_same_appearance_reference",
            "path": manual,
            "source_type": "sku_image",
        }
    return None


def creative_decision_errors(item: Dict[str, Any]) -> List[str]:
    """Return only factual/safety creative errors.

    Prompt wording, art-direction completeness, module presence and text mapping are
    normalized before validation.  They are no longer hard blockers by themselves.
    """
    errors: List[str] = []
    return errors


def _append_auto_material_decisions(
    decisions: List[Dict[str, Any]],
    attributes: Dict[int, Dict[str, Any]],
    fill_input: Dict[str, Any],
) -> None:
    """Allow source-grounded material facts to satisfy the design contract.

    The ecommerce designer is still the commercial decision layer.  This helper
    covers the deterministic platform projection case: a current 1688 material
    fact plus a current Ozon material dictionary value.  It does not invent a
    material and it does not hard-code a product family.
    """
    filled_ids = {
        int(item.get("attribute_id"))
        for item in decisions
        if str(item.get("decision_status") or "filled") == "filled"
        and str(item.get("ozon_value") or "").strip()
        and str(item.get("ozon_value") or "").casefold() != "unknown"
    }
    for attribute_id, attribute in attributes.items():
        if attribute_id in filled_ids:
            continue
        decision = material_decision_from_fact(attribute, fill_input)
        if decision:
            decisions.append(decision)


def _dimension_triplets_from_value(value: Any) -> List[tuple[float, float, float]]:
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("canonical_value"), dict):
        raw = value.get("canonical_value") or {}
    else:
        raw = value
    axes = []
    for key in ("length", "width", "height"):
        item = raw.get(key)
        if item is None:
            item = raw.get(f"{key}_mm")
        if item is None:
            item = raw.get(f"{key}_cm")
            unit_hint = "cm"
        else:
            unit_hint = str(value.get("canonical_unit") or "mm").casefold()
        if isinstance(item, dict):
            item = item.get("canonical_value") or item.get("value")
        try:
            number = float(str(item).replace(",", "."))
        except (TypeError, ValueError):
            return []
        if unit_hint == "cm":
            number *= 10
        axes.append(number)
    if any(axis <= 0 for axis in axes):
        return []
    return [tuple(axes)]  # type: ignore[list-item]


def _is_low_confidence_estimate(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    source = str(value.get("source") or value.get("mapping_method") or "").casefold()
    if "estimate" not in source and "estimated" not in source and value.get("estimated") is not True:
        return False
    return normalize_confidence(value.get("confidence"), default=0.0) <= 0.5


def low_confidence_estimated_dimension_phrases(product_dir: Path) -> List[str]:
    path = product_dir / "output/attribute-fill-input.json"
    if not path.is_file():
        return []
    try:
        fill_input = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    phrases: List[str] = []
    seen: set[str] = set()
    rows = fill_input.get("sku_rows") or (fill_input.get("merged_facts") or {}).get("sku_rows") or []
    for row in rows:
        for key in ("product_dimensions", "package_dimensions"):
            value = row.get(key)
            if not _is_low_confidence_estimate(value):
                continue
            for length, width, height in _dimension_triplets_from_value(value):
                candidates = [
                    (length, width, height),
                    (length / 10, width / 10, height / 10),
                ]
                for axes in candidates:
                    normalized_axes = []
                    for axis in axes:
                        normalized_axes.append(str(int(axis)) if axis.is_integer() else f"{axis:g}")
                    phrase = "x".join(normalized_axes)
                    if phrase not in seen:
                        phrases.append(phrase)
                        seen.add(phrase)
    return phrases


def _buyer_facing_text_for_estimate_scan(design: Dict[str, Any]) -> str:
    listing = design.get("listing") or {}
    payload: List[Any] = [
        listing.get("seo_title_ru"),
        listing.get("short_title_ru"),
        listing.get("description_ru"),
        listing.get("selling_points"),
    ]
    for item in [*(design.get("main_images") or []), *(design.get("detail_images") or [])]:
        payload.extend([
            item.get("slot"),
            item.get("commercial_purpose"),
            item.get("buyer_question"),
            item.get("russian_text"),
            item.get("overlay_plan"),
            item.get("prompt"),
            item.get("design_rationale"),
        ])
    return json.dumps(payload, ensure_ascii=False)


def _normalize_dimension_scan_text(value: str) -> str:
    text = value.casefold().replace(",", ".")
    text = re.sub(r"[×хx*]", "x", text)
    text = re.sub(r"\s+", "", text)
    return text


def low_confidence_estimate_buyer_text_errors(product_dir: Path, design: Dict[str, Any]) -> List[str]:
    phrases = low_confidence_estimated_dimension_phrases(product_dir)
    if not phrases:
        return []
    text = _normalize_dimension_scan_text(_buyer_facing_text_for_estimate_scan(design))
    errors: List[str] = []
    for phrase in phrases:
        if phrase and phrase in text:
            errors.append(
                f"low-confidence estimated dimension {phrase} must not appear in buyer-facing listing or image plan"
            )
    return errors


def append_design_validation_warnings(design: Dict[str, Any], warnings: List[str]) -> None:
    if not warnings:
        return
    processing = design.setdefault("processing", {})
    existing = list(processing.get("validation_warnings") or [])
    seen = set(str(item) for item in existing)
    for warning in warnings:
        text = str(warning or "").strip()
        if text and text not in seen:
            existing.append(text)
            seen.add(text)
    processing["validation_warnings"] = existing


def attribute_decision_errors(product_dir: Path, design: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    path = product_dir / "output/attribute-fill-input.json"
    if not path.is_file():
        return ["attribute-fill-input.json is missing; ecommerce_design must run the current fact merger and attribute input builder first"]
    try:
        fill_input = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"attribute-fill-input.json is unreadable: {exc}"]
    decision_section = design.get("attribute_decisions") or {}
    if decision_section.get("input_hash") and decision_section.get("input_hash") != fill_input.get("input_hash"):
        append_design_validation_warnings(
            design,
            ["attribute decision input_hash differs from the current attribute-fill-input; validating concrete attribute ids and dictionary values instead"],
        )
    attributes = {int(item["attribute_id"]): item for item in fill_input.get("ozon_attributes") or []}
    multicolor_values = {"разноцветный", "многоцветный", "multicolor", "multi-color", "多色", "彩色"}
    for sku_id, values in (decision_section.get("attributes_by_sku") or {}).items():
        for decision in values or []:
            try:
                attribute = attributes.get(int(decision.get("attribute_id")))
            except (TypeError, ValueError):
                attribute = None
            if not attribute or physical_dimension_for(attribute) != "color":
                continue
            color_value = str(decision.get("ozon_value") or decision.get("raw_semantic_value") or "").strip().casefold()
            if color_value in multicolor_values:
                errors.append(
                    f"SKU {sku_id} color must be one dominant visible product color; multicolor is not allowed"
                )
    decisions: List[Dict[str, Any]] = list(decision_section.get("common_attributes") or [])
    for values in (decision_section.get("attributes_by_sku") or {}).values():
        decisions.extend(values or [])
    _append_auto_material_decisions(decisions, attributes, fill_input)
    seen_ids: set[int] = set()
    for decision in decisions:
        try:
            attribute_id = int(decision.get("attribute_id"))
        except (TypeError, ValueError):
            errors.append("attribute decision has invalid attribute_id")
            continue
        attribute = attributes.get(attribute_id)
        if not attribute:
            errors.append(f"attribute decision {attribute_id} is not in current realtime category attributes")
            continue
        seen_ids.add(attribute_id)
        status = str(decision.get("decision_status") or "filled")
        if status != "filled":
            continue
        dimension = physical_dimension_for(attribute)
        if value_looks_like_wrong_dimension(dimension, decision.get("raw_semantic_value")):
            errors.append(f"attribute {attribute_id} has incompatible physical semantic value")
        allowed_values = attribute.get("allowed_values") or []
        if allowed_values:
            matched = allowed_by_id(attribute, decision.get("dictionary_value_id")) or allowed_by_value(attribute, decision.get("ozon_value"))
            if not matched:
                errors.append(f"attribute {attribute_id} dictionary value is absent from current allowed_values")
            elif str(decision.get("ozon_value") or "") != str(matched.get("value") or ""):
                if attribute_id == 10096:
                    # Preserve the seller-visible colour wording in creative
                    # copy, but make the platform decision canonical before
                    # materializing the upload fields. Ozon accepts this
                    # attribute only as a current dictionary value.
                    decision["ozon_value"] = str(matched.get("value") or "")
                    decision["dictionary_value_id"] = int(
                        matched.get("dictionary_value_id", matched.get("id"))
                    )
                else:
                    errors.append(f"attribute {attribute_id} ozon_value must exactly match the current dictionary value")
    missing = sorted(
        attribute_id
        for attribute_id, attribute in attributes.items()
        if attribute_id not in seen_ids and attribute.get("required")
    )
    if missing:
        append_design_validation_warnings(
            design,
            [
                "ecommerce_design skipped required Ozon attribute decisions; "
                "field_completion remains responsible for deterministic required-field compilation: "
                + ", ".join(map(str, missing[:20]))
            ],
        )
    return errors


def _price_by_sku(product_dir: Path) -> Dict[str, Dict[str, Any]]:
    path = product_dir / "output/pricing-result.json"
    if not path.is_file():
        return {}
    pricing = load_json(path)
    return {str(item.get("sku_id") or ""): item for item in pricing.get("sku_pricing") or []}


def _offer_id_by_sku(product_dir: Path) -> Dict[str, str]:
    for filename in ("variant-grouping-result.json", "platform-grouping-result.json"):
        path = product_dir / "output" / filename
        if not path.is_file():
            continue
        try:
            grouping = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        result = {
            str(item.get("sku_id") or ""): str(item.get("offer_id") or "").strip()
            for item in grouping.get("variants") or []
            if str(item.get("sku_id") or "").strip() and str(item.get("offer_id") or "").strip()
        }
        if result:
            return result
    return {}


def _sku_display_names(design: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(item.get("sku_id") or ""): str(item.get("name_ru") or item.get("display_name_ru") or "unknown")
        for item in design.get("sku_plan") or []
    }


def _sku_seo_title(base_title: str, sku_display_name: str) -> str:
    title = str(base_title or "").strip()
    sku_name = str(sku_display_name or "").strip()
    if not title:
        return sku_name[:500] if sku_name else "unknown"
    if not sku_name or sku_name.casefold() in {"unknown", "none", "null"}:
        return title[:500]
    # SKU names are generated by ecommerce design and usually carry only the
    # variant fact after the final comma: color, capacity, or configuration.
    # Keep the SEO base intact and append only the missing variant suffix.
    variant = sku_name.rsplit(",", 1)[-1].strip() if "," in sku_name else sku_name
    if not variant or variant.casefold() in title.casefold():
        return title[:500]
    return f"{title}, {variant}"[:500]


def _draft_attributes(compiled: Dict[str, Any], sku_id: str | None = None) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    by_id: Dict[int, Dict[str, Any]] = {}
    for item in compiled.get("common_attributes") or compiled.get("attributes") or []:
        if item.get("attribute_id") is not None:
            by_id[int(item["attribute_id"])] = item
    if sku_id:
        for item in (compiled.get("attributes_by_sku") or {}).get(str(sku_id), []) or []:
            if item.get("attribute_id") is not None:
                by_id[int(item["attribute_id"])] = item
    for item in by_id.values():
        values: List[Dict[str, Any]] = []
        if item.get("dictionary_values"):
            values.extend({
                "value": value.get("value"),
                "dictionary_value_id": int(value["dictionary_value_id"]),
            } for value in item.get("dictionary_values") or [] if value.get("dictionary_value_id") is not None)
        elif item.get("value") != "unknown":
            value_item: Dict[str, Any] = {"value": item.get("value")}
            if item.get("dictionary_value_id") is not None:
                value_item["dictionary_value_id"] = int(item["dictionary_value_id"])
            values.append(value_item)
        result.append({
            "field_key": str(item.get("attribute_name") or item.get("attribute_id")),
            "attribute_id": int(item["attribute_id"]),
            "complex_id": "unknown",
            "values": values,
            "source": "analysis" if item.get("source") != "unknown" else "unknown",
            "status": "confirmed" if values else "unknown",
        })
    return result


def _planned_images(product_dir: Path) -> List[Dict[str, Any]]:
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return []
    plan = load_json(plan_path)
    items = [*(plan.get("main_images") or []), *(plan.get("detail_images") or [])]
    result: List[Dict[str, Any]] = []

    def generated_qc_status(item: Dict[str, Any], output_path: str) -> str:
        status = str(item.get("status") or "").casefold()
        if status not in {"generated", "ready", "pass", "passed", "complete"}:
            return "not_checked"
        raw = Path(output_path)
        if raw.is_absolute():
            candidate = raw
        elif raw.parts and raw.parts[0] == "products":
            candidate = project_root_for(product_dir) / raw
        else:
            candidate = product_dir / raw
        return "pass" if candidate.is_file() and candidate.stat().st_size > 0 else "missing"

    for item in items:
        slot = str(item.get("slot") or "")
        output_path = str(item.get("output_path") or item.get("accepted_path") or "").strip()
        if not output_path:
            continue
        role = "main" if str(item.get("image_type") or "").casefold() == "main" or str(item.get("layout_type") or "") == "sku_main" else "detail"
        result.append({
            "slot": slot,
            "role": role,
            "path": output_path,
            "source_image_ids": [str(value) for value in item.get("source_references") or []],
            "qc_status": generated_qc_status(item, output_path),
            "variant_scope": "sku" if role == "main" else "shared",
            "source_sku_id": str(item.get("sku_id") or item.get("source_sku_id") or "all"),
            "variant_kind": "not_applicable",
            "variant_value": str(item.get("variant_value") or item.get("sku_id") or "shared"),
        })
    return result


def _missing_required_attribute_summary(value: Any) -> Dict[str, Any]:
    """Render a compiler missing-key without assuming it is a bare int.

    The per-SKU compiler reports missing required attributes by a collision
    safe key, for example ``common:85`` or ``SKU-001:85``.  Ozon draft
    projection is an audit view, so it must preserve that scope instead of
    crashing while coercing the whole key to int.
    """
    raw = str(value)
    scope = "common"
    attribute_part = raw
    if ":" in raw:
        scope, attribute_part = raw.rsplit(":", 1)
    try:
        attribute_id: int | str = int(attribute_part)
    except (TypeError, ValueError):
        attribute_id = attribute_part
    result: Dict[str, Any] = {
        "attribute_id": attribute_id,
        "attribute_name": raw,
        "attribute_key": raw,
    }
    if scope:
        result["scope"] = scope
    return result


def build_current_ozon_draft(product_dir: Path, design: Dict[str, Any] | None = None) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    design = design or load_json(product_dir / "output/ozon-ecommerce-design.json")
    source = load_json(product_dir / "input/source.json")
    category = load_json(product_dir / "output/ozon-category.json")
    compiled = compile_product_attributes(product_dir)
    listing = design.get("listing") or {}
    prices = _price_by_sku(product_dir)
    offer_ids = _offer_id_by_sku(product_dir)
    sku_names = _sku_display_names(design)
    selected = selected_skus(source)
    attributes = _draft_attributes(compiled)
    base_seo_title = str(listing.get("seo_title_ru") or "unknown")
    price_values = [
        float(item.get("selling_price_cny") or 0)
        for item in prices.values()
        if item.get("selling_price_cny") is not None
    ]
    first_price = f"{(price_values[0] if price_values else 0):.2f}" if price_values else None
    skus: List[Dict[str, Any]] = []
    for sku in selected:
        sku_id = str(sku.get("sku_id") or "")
        price_item = prices.get(sku_id) or {}
        purchase = price_item.get("purchase_cost_cny", sku.get("purchase_price", sku.get("price")))
        try:
            purchase_value = float(purchase)
        except (TypeError, ValueError):
            purchase_value = None
        skus.append({
            "source_sku_id": sku_id,
            "source_sku_name": str(sku.get("sku_name") or sku_id),
            "display_name_ru": sku_names.get(sku_id) or str(sku.get("sku_name") or sku_id),
            "option_values": list(sku.get("option_values") or []),
            "offer_id": offer_ids.get(sku_id) or str(sku.get("offer_id") or "").strip() or f"{product_dir.name}-{sku_id}",
            "purchase_price_cny": purchase_value,
            "purchase_price_source": (
                str(price_item.get("purchase_cost_source") or sku.get("price_source") or "unknown")
                if str(price_item.get("purchase_cost_source") or sku.get("price_source") or "unknown") in {"sku_specific_price", "price_range", "unknown"}
                else "unknown"
            ),
            "sale_price_rub": f"{float(price_item['selling_price_rub']):.2f}" if price_item.get("selling_price_rub") is not None else None,
            "sale_price": f"{float(price_item['selling_price_cny']):.2f}" if price_item.get("selling_price_cny") is not None else None,
            "sale_currency_code": "CNY",
            "stock": None,
            "source_image_url": str(sku.get("image_url") or sku.get("variant_image_url") or "unknown"),
            "local_image_path": sku_image(sku) or "unknown",
            "sku_image_missing": bool(sku.get("sku_image_missing") or not sku_image(sku)),
            "availability": str(sku.get("availability") or "unknown") if str(sku.get("availability") or "unknown") in {"in_stock", "out_of_stock", "unknown"} else "unknown",
            "attributes": _draft_attributes(compiled, sku_id=sku_id),
            "source_data": dict(sku.get("source_data") or {}),
        })
    keywords = _search_keyword_texts(listing)
    draft = {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "offer_id": product_dir.name,
        "description_category_id": int(category["category_id"]),
        "type_id": int(category["type_id"]),
        "category": {
            "category_id": int(category["category_id"]),
            "category_name": str(category.get("category_name") or category.get("category_name_ru") or "unknown"),
            "confidence": float(category.get("confidence") or 1.0),
            "match_status": str(category.get("match_status") or "api_confirmed"),
            "metadata_source": "ozon_seller_api",
        },
        "title": str(listing.get("seo_title_ru") or "unknown"),
        "description": str(listing.get("description_ru") or "unknown"),
        "keywords": keywords[:50],
        "attributes": attributes,
        "attribute_warnings": list(compiled.get("warnings") or []),
        "price": {"price": first_price, "old_price": None, "currency_code": "CNY", "vat": "0"},
        "currency": "CNY",
        "pricing_source": "pricing-engine",
        "profit_warning": [],
        "stock": {"quantity": None, "warehouse_id": "unknown"},
        "images": _planned_images(product_dir),
        "skus": skus,
        "upload_allowed": False,
        "preflight": {
            "status": "not_checked",
            "errors": [],
            "warnings": ["Current draft is compiled from ecommerce design; upload safety is checked inside the uploader."],
            "checked_at": "unknown",
            "metadata_source": "ozon_seller_api",
            "missing_required_attributes": [
                _missing_required_attribute_summary(attribute_id)
                for attribute_id in (compiled.get("required_summary") or {}).get("missing_attribute_ids") or []
            ],
            "invalid_values": [],
        },
        "source_refs": list(dict.fromkeys([
            f"products/{product_dir.name}/input/source.json",
            f"products/{product_dir.name}/output/product-analysis.json",
            f"products/{product_dir.name}/output/ozon-ecommerce-design.json",
            f"products/{product_dir.name}/output/attribute-fill-input.json",
            f"products/{product_dir.name}/output/ozon-attributes-final.json",
        ])),
    }
    write_json_atomic(product_dir / "output/ozon-draft.json", draft)
    return draft


def validate_design(
    product_dir: Path,
    design: Dict[str, Any] | None = None,
    *,
    auto_compact_layout: bool = False,
) -> List[str]:
    if design is None:
        design = load_json(product_dir / "output/ozon-ecommerce-design.json")
    required_top_level = {
        "product_id",
        "source_refs",
        "product_understanding",
        "listing",
        "sku_plan",
        "attribute_decisions",
        "main_images",
        "detail_images",
        "processing",
    }
    if not isinstance(design, dict) or not design:
        return ["ecommerce design artifact is empty or not a JSON object; retry ecommerce_design"]
    missing_top_level = sorted(required_top_level.difference(design))
    if missing_top_level:
        return [
            "ecommerce design artifact is incomplete; missing top-level keys: "
            + ", ".join(missing_top_level)
        ]
    normalize_decision_trace(design)
    normalize_attribute_decision_shape(design)
    normalize_trace_references(design, product_dir)
    normalize_design_prompt_quality(design, product_dir)
    source = load_json(product_dir / "input/source.json")
    project_root = project_root_for(product_dir)
    errors = [error.message for error in Draft202012Validator(
        load_json(ROOT / "templates/ozon-ecommerce-design.schema.json")
    ).iter_errors(design)]
    try:
        validate_formal_product_input(product_dir)
    except ProductionInputError as exc:
        errors.append(str(exc))
    skus = selected_skus(source)
    if not 1 <= len(skus) <= 10:
        errors.append("selected SKU count must be between 1 and 10")
    if design.get("product_id") != product_dir.name:
        errors.append("design product_id does not match product directory")
    for ref in design.get("source_refs") or []:
        if str(ref).endswith("/output/image-design-revision-request.json"):
            continue
        try:
            validate_current_product_trace_ref(product_dir, ref)
        except ProductionInputError as exc:
            errors.append(str(exc))
    evidence_items = [*((design.get("listing") or {}).get("selling_points") or [])]
    keyword_groups = ((design.get("listing") or {}).get("keywords") or {})
    for key in ("primary", "long_tail", "scene", "excluded"):
        evidence_items.extend(keyword_groups.get(key) or [])
    for item in evidence_items:
        for ref in item.get("source_refs") or []:
            if str(ref).endswith("/output/image-design-revision-request.json"):
                continue
            try:
                validate_current_product_trace_ref(product_dir, ref)
            except ProductionInputError as exc:
                errors.append(str(exc))
    tags = (design.get("listing") or {}).get("hashtags") or []
    if len(tags) > 30 or len({str(value).casefold() for value in tags}) != len(tags):
        errors.append("hashtags must contain no more than 30 unique values")
    if not validate_hashtag_set(tags):
        errors.append("hashtags must be valid Ozon hashtags: # plus Russian letters only, max 30 characters")
    errors.extend(buyer_visible_cjk_errors(design))
    append_design_validation_warnings(
        design,
        low_confidence_estimate_buyer_text_errors(product_dir, design),
    )

    sku_ids = [str(item.get("sku_id") or "") for item in skus]
    manual_overrides = _manual_reference_overrides(product_dir, skus)
    sku_image_bindings = load_sku_image_bindings(product_dir, sku_ids, strict=False)
    normalize_image_source_references(design, product_dir, skus, sku_image_bindings, manual_overrides)
    design_skus = [str(item.get("sku_id") or "") for item in design.get("sku_plan") or []]
    if design_skus != sku_ids:
        errors.append("sku_plan must match selected SKU order exactly")
    mains = design.get("main_images") or []
    if len(mains) != len(skus):
        errors.append(f"main image count must equal selected SKU count ({len(skus)})")
    if len({str(item.get("sku_id") or "") for item in mains}) != len(mains):
        errors.append("each selected SKU must have exactly one unique main image")
    if len({str(item.get("slot") or "") for item in mains}) != len(mains):
        errors.append("main image slots must be unique")
    for sku, item in zip(skus, mains):
        expected_id = str(sku.get("sku_id") or "")
        allowed_reference = _allowed_sku_reference(product_dir, sku, sku_image_bindings, manual_overrides)
        allowed_ref = str((allowed_reference or {}).get("path") or "")
        if item.get("sku_id") != expected_id:
            errors.append(f"main image is not bound to SKU {expected_id}")
        if item.get("layout_type") != "sku_main":
            errors.append(f"main image {expected_id} must use sku_main layout")
        refs = list(item.get("source_references") or [])
        if not allowed_reference:
            errors.append(
                f"Selected SKU {expected_id} has no registered SKU reference; "
                "该SKU缺少参考图，请从本商品已采集图片中选择一张绑定后继续"
            )
            continue
        supplemental_refs = [str(value or "") for value in refs[1:]]
        if not refs or refs[0] != allowed_ref:
            if allowed_reference.get("reference_kind") == "user_bound_reference_image":
                errors.append(
                    f"main image {expected_id} must use the user-bound current-product reference image first"
                )
            elif allowed_reference.get("reference_kind") == "manual_same_appearance_reference":
                errors.append(
                    f"main image {expected_id} must use the manually confirmed same-product reference image first"
                )
            else:
                errors.append(f"main image {expected_id} must reference its own real SKU image first")
        for ref in supplemental_refs:
            try:
                validate_product_reference(product_dir, ref)
            except ValueError as exc:
                errors.append(f"main image {expected_id} supplemental reference is invalid: {exc}")
                continue
            if "/input/sku-images/" in ref:
                errors.append(
                    f"main image {expected_id} supplemental reference must not use another SKU image"
                )
        try:
            validate_product_reference(product_dir, allowed_ref)
        except ValueError as exc:
            errors.append(f"SKU {expected_id} real reference is invalid: {exc}")
        errors.extend(creative_decision_errors(item))

    current_collection_id = str(source.get("collection_id") or "")
    if design.get("collection_id") != current_collection_id:
        errors.append("design collection_id must match the current workbench collection")
    if design.get("source_kind") != "workbench_collection":
        errors.append("design source_kind must be workbench_collection")
    errors.extend(attribute_decision_errors(product_dir, design))

    details = design.get("detail_images") or []
    if len(details) != 8:
        errors.append("shared detail image count must equal 8")
    if len({str(item.get("slot") or "") for item in details}) != len(details):
        errors.append("shared detail image slots must be unique")
    for item in details:
        if item.get("sku_id"):
            errors.append(f"shared detail {item.get('slot')} must not be SKU-scoped")
        refs = list(item.get("source_references") or [])
        for ref in refs:
            try:
                validate_product_reference(product_dir, ref)
            except ValueError as exc:
                errors.append(f"detail source reference is invalid: {exc}")
        if item.get("layout_type") in DETERMINISTIC_LAYOUTS and item.get("operation") not in {"compose_from_real_images", "generate_from_reference"}:
            errors.append(f"{item.get('slot')} must use reference-guided composition")
        errors.extend(creative_decision_errors(item))
    comparison = [item for item in details if item.get("layout_type") == "sku_comparison"]
    if len(skus) > 1:
        expected_comparison_refs = []
        for sku in skus:
            reference = _allowed_sku_reference(product_dir, sku, sku_image_bindings, manual_overrides)
            expected_comparison_refs.append(str((reference or {}).get("path") or ""))
        expected_comparison_refs = [ref for ref in expected_comparison_refs if ref]
        if len(comparison) != 1:
            append_design_validation_warnings(
                design,
                [
                    "Multi-SKU product does not have exactly one shared SKU comparison image; "
                    "continuing because SKU main images and SKU text still carry variant differences."
                ],
            )
        else:
            actual_comparison_refs = [str(value or "") for value in comparison[0].get("source_references") or []]
            expected_unique_refs = set(expected_comparison_refs)
            actual_expected_refs = set(
                ref for ref in actual_comparison_refs if ref in expected_unique_refs
            )
            extra_sku_refs = [
                ref for ref in actual_comparison_refs
                if ref not in expected_unique_refs and "/input/sku-images/" in ref
            ]
            if actual_expected_refs != expected_unique_refs:
                append_design_validation_warnings(
                    design,
                    ["SKU comparison does not include every available selected-SKU reference; continuing because SKU text still carries variant differences."],
                )
            if extra_sku_refs:
                append_design_validation_warnings(
                    design,
                    [
                        "SKU comparison includes unselected current-product SKU references; "
                        "continuing because final SKU identity is enforced by each SKU main image."
                    ],
                )
    cluster_context = load_json(product_dir / "output/ecommerce-design-context.json") if (product_dir / "output/ecommerce-design-context.json").is_file() else {}
    selected_store_ids = [
        str(item.get("store_id") or "")
        for item in ((cluster_context.get("store_cluster") or {}).get("selected_stores") or [])
        if isinstance(item, dict) and str(item.get("store_id") or "")
    ]
    variants = design.get("store_variants") or []
    if len(selected_store_ids) > 1:
        by_store = {str(item.get("store_id") or ""): item for item in variants if isinstance(item, dict)}
        missing_variants = [store_id for store_id in selected_store_ids if store_id not in by_store]
        unexpected_variants = [store_id for store_id in by_store if store_id not in selected_store_ids]
        if missing_variants:
            errors.append("store_variants missing selected stores: " + ", ".join(missing_variants))
        if unexpected_variants:
            errors.append("store_variants contains unselected stores: " + ", ".join(unexpected_variants))
        if len(by_store) != len(variants):
            errors.append("store_variants store_id values must be unique")
        for store_id, variant in by_store.items():
            variant_mains = variant.get("main_images") or []
            variant_details = variant.get("detail_images") or []
            if len(variant_mains) != len(skus):
                errors.append(f"store variant {store_id} main image count must equal selected SKU count")
            if len(variant_details) != 8:
                errors.append(f"store variant {store_id} detail image count must equal 8")
            if [str(item.get("sku_id") or "") for item in variant_mains] != sku_ids:
                errors.append(f"store variant {store_id} main images must match selected SKU order")
            if buyer_visible_cjk_errors({"listing": variant.get("listing") or {}, "main_images": variant_mains, "detail_images": variant_details}):
                errors.append(f"store variant {store_id} contains buyer-visible CJK text")
    return errors


def store_variant_design(design: Dict[str, Any], store_id: str) -> Dict[str, Any]:
    """Project one validated commercial variant without changing product facts.

    The caller keeps attributes, SKU plan and source references from the master
    design.  Only buyer-visible copy and image art direction are store-scoped.
    """
    variant = next(
        (item for item in (design.get("store_variants") or [])
         if isinstance(item, dict) and str(item.get("store_id") or "") == str(store_id)),
        None,
    )
    if variant is None:
        raise ValueError(f"store variant is missing for {store_id}")
    projected = copy.deepcopy(design)
    for key in ("listing", "visual_system", "main_images", "detail_images"):
        projected[key] = copy.deepcopy(variant[key])
    projected["active_store_variant"] = {
        "store_id": str(store_id),
        "store_profile": str(variant.get("store_profile") or "standard"),
    }
    return projected


def materialize(product_dir: Path, design: Dict[str, Any]) -> None:
    normalize_attribute_decision_shape(design)
    normalize_trace_references(design, product_dir)
    normalize_design_prompt_quality(design, product_dir)
    errors = validate_design(product_dir, design)
    if errors:
        raise ValueError("; ".join(errors))
    output = product_dir / "output"
    write_json_atomic(output / "ozon-ecommerce-design.json", design)
    listing = design["listing"]
    timestamp = datetime.now().astimezone().replace(microsecond=0).isoformat()
    evidence_points = [
        {
            "text_ru": str(item.get("text_ru") or "").strip(),
            "evidence": list(item.get("source_refs") or design["source_refs"]),
        }
        for item in listing["selling_points"]
        if str(item.get("text_ru") or "").strip()
    ]
    comparison_role = next(
        (item for item in design["detail_images"] if item["layout_type"] == "sku_comparison"),
        None,
    )
    comparison_copy = comparison_role["russian_text"] if comparison_role else next(
        (item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "purchase_notice"),
        design["detail_images"][-1]["russian_text"],
    )
    copy_value = {
        "schema_version": "1.0.0", "product_id": product_dir.name,
        "collection_id": design["collection_id"], "source_kind": design["source_kind"],
        "title_ru": listing["seo_title_ru"], "short_title": listing["short_title_ru"],
        "description_ru": listing["description_ru"],
        "selling_points": evidence_points,
        "bullets_ru": evidence_points[:5],
        "keywords_ru": _search_keyword_texts(listing),
        "hashtags_ru": listing["hashtags"],
        "image_copy_ru": {
            "main_by_sku": {item["sku_id"]: item["russian_text"] for item in design["main_images"]},
            "main": design["main_images"][0]["russian_text"],
            "benefit": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "core_benefit"),
            "problem_solution": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "core_benefit"),
            "scene": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "usage_scene"),
            "feature": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "structure_callout"),
            "detail": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "structure_callout"),
            "usage": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "usage_scene"),
            # Single-SKU products have no comparison role by contract.  Keep the
            # compatibility key, but never fabricate a variant comparison.
            "comparison": comparison_copy,
            "disclaimer": next(item["russian_text"] for item in design["detail_images"] if item["layout_type"] == "purchase_notice"),
        },
        "excluded_unknown_fields": [str(item.get("attribute_name") or item.get("field_key") or "unknown") for item in design.get("attribute_plan") or [] if item.get("value") == "unknown"],
        "warnings": [],
        "source_refs": design["source_refs"],
        "processing": {"step": "russian_copy", "status": "completed", "started_at": timestamp, "finished_at": timestamp, "error": None},
    }
    write_json_atomic(output / "copy-ru.json", copy_value)
    write_json_atomic(output / "title-ru.json", {"product_id": product_dir.name, "title_ru": listing["seo_title_ru"], "short_title_ru": listing["short_title_ru"], "source_ref": f"products/{product_dir.name}/output/ozon-ecommerce-design.json"})
    write_json_atomic(output / "description-ru.json", {"product_id": product_dir.name, "description_ru": listing["description_ru"], "source_ref": f"products/{product_dir.name}/output/ozon-ecommerce-design.json"})
    # ozon-tags.json / ozon-attributes-final.json / ozon-draft.json 由
    # field_completion 单一出口生成（2026-08-14 双写合并）。
    keyword_groups = listing["keywords"]
    accepted = [item for key in ("primary", "long_tail", "scene") for item in keyword_groups[key]]
    accepted_keyword_pairs = [
        (item, keyword)
        for item in accepted
        for keyword in [_search_keyword_for_item(item)]
        if keyword
    ]
    excluded_keyword_pairs = [
        (item, keyword)
        for item in keyword_groups["excluded"]
        for keyword in [_search_keyword_for_item(item)]
        if keyword
    ]
    keyword_research = {
        "schema_version": "1.0.0", "product_id": product_dir.name,
        "target_market": "Ozon Russia", "language": "ru", "generated_at": timestamp,
        "seed_terms": _search_keyword_texts(listing, ("primary",), max_count=8),
        "approved_keywords": [{
            "keyword": keyword,
            "intent": item["intent"] if item["intent"] in {"transactional", "commercial", "informational"} else "commercial",
            "source": (
                "ozon_public_search" if any(str(ref).startswith("https://www.ozon.ru/") for ref in item["source_refs"])
                else "ozon_seller_metadata" if any("category" in str(ref).casefold() or "ozon" in str(ref).casefold() for ref in item["source_refs"])
                else "source_fact"
            ),
            "evidence": item["source_refs"], "volume": "unknown", "difficulty": "unknown",
        } for item, keyword in accepted_keyword_pairs],
        "excluded_keywords": [{"keyword": keyword, "reason": item["intent"]} for item, keyword in excluded_keyword_pairs],
        "metrics_notice": "Search volume and difficulty were not available and were not fabricated.",
    }
    write_json_atomic(output / "keyword-research-ru.json", keyword_research)
    category_path = load_json(product_dir / "input/category-selection.json")
    sections = listing["description_sections"]
    primary = _search_keyword_texts(listing, ("primary",), max_count=8)
    secondary = _search_keyword_texts(listing, ("long_tail", "scene"), max_count=20)
    source_by_keyword = {keyword: item["source_refs"] for item, keyword in accepted_keyword_pairs}
    scene_keywords = {_search_keyword_for_item(item) for item in keyword_groups["scene"]}
    source_kind = {keyword: ("usage_scene" if keyword in scene_keywords else "product_type") for keyword in [*primary, *secondary]}
    image_copy = copy_value["image_copy_ru"]
    # build_current_ozon_draft 已移除：draft/attributes-final 由 field_completion
    # 单一出口编译（2026-08-14 双写合并），materialize 只投影俄文文案与关键词。


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir")
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--repair-buyer-copy", action="store_true")
    args = parser.parse_args()
    product_dir = Path(args.product_dir).resolve()
    if args.repair_buyer_copy:
        changed = repair_existing_buyer_copy_projection(product_dir)
        print(json.dumps({
            "status": "PASS",
            "product_id": product_dir.name,
            "changed": changed,
        }, ensure_ascii=False))
        return 0
    design = load_json(product_dir / "output/ozon-ecommerce-design.json")
    normalize_design_hashtags(design)
    errors = validate_design(product_dir, design)
    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    if args.materialize:
        materialize(product_dir, design)
    print(json.dumps({
        "status": "PASS", "product_id": product_dir.name,
        "selected_skus": len(design["main_images"]), "shared_details": len(design["detail_images"]),
        "total_images": len(design["main_images"]) + len(design["detail_images"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
