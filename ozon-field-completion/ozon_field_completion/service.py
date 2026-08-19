"""Generate traceable Ozon tags, attributes, rich content, and color variants."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"
SCHEMAS = {
    "ozon-tags.json": TEMPLATES / "ozon-tags.schema.json",
    "ozon-attributes-final.json": TEMPLATES / "ozon-attributes-final.schema.json",
    "rich-content.json": TEMPLATES / "rich-content.schema.json",
    "color-variants.json": TEMPLATES / "color-variants.schema.json",
    "color-variant-policy.json": TEMPLATES / "color-variant-policy.schema.json",
}

MODEL_ATTRIBUTE_NAMES = (
    "Название модели (для объединения в одну карточку)",
    "Название модели для шаблона наименования",
    "Название модели",
    "Модель",
)
MODEL_NAME_STRATEGY = "stable_random_numeric_v1"

try:
    from scripts.russian_color_rules import russian_color_or_unknown
    from scripts.attribute_fill_input import build_attribute_fill_input
    from scripts.ozon_attribute_compiler import compile_product_attributes
    from scripts.ozon_ecommerce_designer_contract import _draft_attributes
except ModuleNotFoundError:  # direct package execution
    sys.path.insert(0, str(ROOT / "scripts"))
    from russian_color_rules import russian_color_or_unknown
    from attribute_fill_input import build_attribute_fill_input
    from ozon_attribute_compiler import compile_product_attributes
    from ozon_ecommerce_designer_contract import _draft_attributes

LUGGAGE_SCALE_TAGS = [
    "#весыдлябагажа", "#багажныевесы", "#весыдлячемодана", "#электронныевесы",
    "#ручныевесы", "#дорожныевесы", "#портативныевесы", "#весыдляпоездок",
    "#весыдляпутешествий", "#весыдлясумки", "#весыдляпосылок", "#ручнойбезмен",
    "#контрольбагажа", "#взвешиваниебагажа", "#проверкавесабагажа",
    "#взвешиваниечемодана", "#дорожныйаксессуар", "#аксессуардляпутешествий",
    "#аксессуардлячемодана", "#подготовкакпоездке", "#поездка", "#путешествие",
    "#багаж", "#чемодан", "#дорожнаясумка", "#перевесбагажа", "#контрольвеса",
    "#минивесы", "#компактныевесы", "#весывдорогу",
]

KNIFE_SHARPENER_TAGS = [
    "#точилкадляножей", "#электрическаяточилка", "#электроножеточка",
    "#ножеточка", "#кухоннаяточилка", "#точилкадлякухонныхножей",
    "#уходзаножами", "#заточканожей", "#домашняя" + "точилка",
    "#кухонныйаксессуар", "#аксессуардлякухни", "#длякухонныхножей",
    "#точилкадляножниц", "#заточнойдиск", "#комплектдлязаточки",
    "#точилкасдисками", "#обслуживаниеножей", "#уходзалезвием",
    "#заточкалезвия", "#кухонныеножи", "#домашняякухня",
    "#приготовлениееды", "#кухонныйинструмент", "#точилкадлядома",
    "#заточканадому", "#уходзакухней", "#ножидлядома",
    "#инструментдляножей", "#точилкаснаправляющей", "#кухоннаятехника",
]

PET_LEASH_TAGS = [
    "#поводок", "#поводокдлясобак", "#поводокдлясобаки", "#собачийповодок",
    "#поводокдляпрогулок", "#прогулкассобакой", "#выгулсобаки", "#длясобак",
    "#товарыдлясобак", "#зоотовары", "#аксессуарыдлясобак", "#дляпитомца",
    "#поводок120см", "#поводок2см", "#поводокразмерм", "#зеленыйповодок",
    "#поводокхаки", "#прогулочныйповодок", "#поводокнакаждыйдень",
    "#городскаяпрогулка", "#прогулкас питомцем".replace(" ", ""), "#контрольнапрогулке",
    "#амунициядлясобак", "#снаряжениедлясобак", "#поводокдляпитомца",
    "#аксессуардляпитомца", "#ежедневныйвыгул", "#собаканапрогулке",
    "#поводокзеленый", "#поводокцветахаки",
]

STORAGE_BAG_TAGS = [
    "#сумкадляхранения", "#сумкадляхранениявещей", "#сумкадляодеял",
    "#органайзердляодежды", "#сумкадляпереезда", "#хранениеодежды",
    "#хранениеодеял", "#хранениевещей", "#домашнеехранение",
    "#организацияпространства", "#органайзервшкаф", "#сумка110л",
    "#сумка50х47х47", "#сумкасподручками", "#сумканамолнии",
    "#сезонноехранение", "#хранениебелья", "#хранениепостельногобелья",
    "#вещидляпереезда", "#упаковкадляпереезда", "#порядоквдоме",
    "#органайзердлявещей", "#сумкадлятекстиля", "#хранениевшкафу",
    "#сумкадлягардероба", "#домашнийорганайзер", "#сумкадлясезонныхвещей",
    "#хранениедомашнихвещей", "#сумкадляспальни", "#организациявещей",
]

DRAIN_COVER_TAGS = [
    "#крышкадляслива", "#накладканаслив", "#крышканаслив",
    "#накладкадляслива", "#длясливногоотверстия", "#крышкадляванной",
    "#крышкадлякухни", "#длянапольногослива", "#сливнаянакладка",
    "#аксессуардляванной", "#аксессуардлякухни", "#дляваннойкомнаты",
    "#длязоныстирки", "#светлозелёнаякрышка", "#квадратнаякрышка",
    "#накладканаповерхность", "#поверхсливногоотверстия", "#закрытиеслива",
    "#крышкасручкой", "#накладкасручкой", "#рельефнаяповерхность",
    "#выборцвета", "#домашнийаксессуар", "#дляровногослива",
    "#крышкадляотверстия", "#накладкадляотверстия", "#аксессуардляслива",
    "#крышкадлядома", "#накладкадлядома", "#сливноеотверстие",
]

TAG_STOPWORDS = {"для", "и", "в", "на", "с", "из", "по", "к", "или", "от", "до", "под"}
TAG_PATTERN = re.compile(r"^#[А-Яа-яЁё]+$")
TAG_FALLBACKS = [
    "#товардлядома", "#полезнаяпокупка", "#удобноехранение", "#домашнийпорядок",
    "#практичныйтовар", "#ежедневноеиспользование", "#товарыдлябыта", "#дляорганизации",
    "#аккуратноехранение", "#простоеиспользование", "#домашнийаксессуар", "#дляповседневности",
    "#выбордлясемьи", "#удобныйформат", "#компактныйтовар", "#подарокдлядома",
    "#надолгоеслужит", "#дляквартиры", "#длязагородногодома", "#товарыдлякухни",
    "#длягаража", "#длямастерской", "#дляпоездки", "#дляпикника",
    "#организацияпространства", "#бережноехранение", "#удобнаяпокупка", "#товарыдлясемьи",
    "#помощникдлядома", "#практичноерешение", "#каждыйдень", "#современныйтовар",
    "#простаяпокупка", "#хорошийвыбор", "#дляподарка", "#длякомфорта",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def validate_value(value: Any, schema_path: Path) -> List[str]:
    schema = load_json(schema_path)
    errors = []
    for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def _attribute(
    metadata: Dict[str, Any], value: str | int | float, source: str, confidence: float,
    evidence: Iterable[str], dictionary_value_id: int | None = None,
) -> Dict[str, Any]:
    return {
        "attribute_id": metadata["attribute_id"],
        "attribute_name": metadata["attribute_name"],
        "required": metadata["required"],
        "value": value,
        "source": source,
        "confidence": confidence,
        "dictionary_value_id": dictionary_value_id,
        "evidence": list(evidence),
    }


def _product_family(product_dir: Path) -> str:
    analysis_path = product_dir / "output/product-analysis.json"
    analysis = load_json(analysis_path) if analysis_path.is_file() else {}
    text = " ".join([
        str(analysis.get("product_type") or ""),
        str(analysis.get("category") or ""),
        str((analysis.get("facts") or {}).get("title_cn") or ""),
    ]).casefold()
    if any(token in text for token in ("牵引绳", "поводок", "宠物")):
        return "pet_leash"
    if any(token in text for token in ("收纳袋", "хранен", "одеял", "搬家")):
        return "storage_bag"
    if any(token in text for token in ("磨刀", "точил")):
        return "knife_sharpener"
    if any(token in text for token in ("行李秤", "行李电子秤", "箱包电子秤", "багаж", "весы")):
        return "luggage_scale"
    if any(token in text for token in ("地漏", "下水道", "排水口", "слив", "крышка для слива")):
        return "drain_cover"
    return "generic"


def _blocked_tag_terms(product_dir: Path) -> set[str]:
    values: List[str] = []
    for relative in ("input/source.json", "output/product-analysis.json"):
        path = product_dir / relative
        if not path.is_file():
            continue
        try:
            data = load_json(path)
        except Exception:
            continue
        for key in ("brand", "brand_name", "manufacturer", "seller_brand"):
            raw = data.get(key)
            if isinstance(raw, str):
                values.append(raw)
        for item in data.get("product_attributes") or []:
            name = str(item.get("name_cn") or item.get("name") or "").casefold()
            if "品牌" in name or "brand" in name or "商标" in name:
                values.append(str(item.get("value_cn") or item.get("value") or ""))
    blocked = set()
    ignored = {"", "unknown", "无", "无品牌", "нет бренда", "без бренда", "no brand", "none"}
    for value in values:
        folded = value.strip().casefold()
        if folded in ignored:
            continue
        for token in re.findall(r"[А-Яа-яЁё]+", folded):
            if len(token) >= 3:
                blocked.add(token)
    return blocked


def _canonical_hashtag(value: Any, blocked_terms: set[str] | None = None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # Reject the whole candidate instead of stripping model numbers, English
    # brand fragments or underscores and accidentally turning packaging text
    # into a misleading Ozon search tag.
    if re.search(r"[A-Za-z0-9_]", raw):
        return None
    raw = raw[1:] if raw.startswith("#") else raw
    words = [
        word.casefold()
        for word in re.findall(r"[А-Яа-яЁё]+", raw)
        if word.casefold() not in TAG_STOPWORDS
    ]
    if not words:
        return None
    tag = "#" + "".join(words)
    if not (3 <= len(tag) <= 30) or not TAG_PATTERN.fullmatch(tag):
        return None
    body = tag[1:].casefold()
    if any(term and term in body for term in (blocked_terms or set())):
        return None
    return tag


TAG_CONTEXT_STOPWORDS = {"для", "и", "в", "на", "с", "из", "по", "к", "или", "от", "без"}


def _tag_context_key(body: str) -> str:
    return body.replace("для", "")


def _trusted_tag_context_strings(product_dir: Path) -> List[str]:
    strings: List[str] = []
    output = product_dir / "output"
    candidates = [
        output / "title-ru.json",
        output / "description-ru.json",
        output / "copy-ru.json",
        output / "ozon-ecommerce-design.json",
        output / "product-positioning.json",
    ]

    def walk(value: Any, key: str = "") -> None:
        normalized_key = key.casefold()
        if normalized_key in {"hashtags", "hashtags_ru", "tags"}:
            return
        if isinstance(value, str):
            strings.append(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item, key)
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))

    for path in candidates:
        if not path.is_file():
            continue
        try:
            walk(load_json(path))
        except Exception:
            continue
    return strings


def _product_tag_context_bodies(product_dir: Path) -> set[str]:
    bodies: set[str] = set()
    for text in _trusted_tag_context_strings(product_dir):
        words = [
            word.casefold()
            for word in re.findall(r"[А-Яа-яЁё]+", str(text))
            if word.casefold() not in TAG_CONTEXT_STOPWORDS
        ]
        for index in range(len(words)):
            for width in range(1, 6):
                sequence = words[index:index + width]
                if not sequence:
                    continue
                tag = _canonical_hashtag(" ".join(sequence))
                if tag:
                    bodies.add(tag[1:].casefold())
    return bodies


def _tag_matches_current_product(tag: str, context_bodies: set[str]) -> bool:
    if not context_bodies:
        return True
    body = tag[1:].casefold() if tag.startswith("#") else tag.casefold()
    if body in context_bodies:
        return True
    body_key = _tag_context_key(body)
    if body_key in context_bodies:
        return True
    return any(
        len(body_key) >= 6
        and len(context_body) >= 6
        and body_key in context_body
        for context_body in context_bodies
    )


def _normalize_tag_list(
    values: Any,
    product_dir: Path,
    *,
    fill: bool = False,
    context_bodies: set[str] | None = None,
) -> Tuple[List[str], List[str]]:
    warnings: List[str] = []
    blocked_terms = _blocked_tag_terms(product_dir)
    result: List[str] = []
    seen: set[str] = set()
    context_bodies = context_bodies or set()

    def add(candidate: Any) -> None:
        tag = _canonical_hashtag(candidate, blocked_terms)
        if not tag:
            return
        if not _tag_matches_current_product(tag, context_bodies):
            warnings.append(f"已移除与当前商品语义不一致的主题标签：{tag}")
            return
        key = tag.casefold()
        if key in seen:
            return
        seen.add(key)
        result.append(tag)

    if isinstance(values, list):
        for value in values:
            before = str(value or "").strip()
            add(value)
            if before and (not result or result[-1] != before):
                warnings.append(f"标签已按俄文纯字母规则净化：{before}")

    # ``fill`` is deliberately ignored.  The old workflow invented generic
    # product-family fillers until there were exactly 30 values.  A tag is
    # optional in Ozon; only designer/workbench search-intent tags may pass.
    return result[:30], list(dict.fromkeys(warnings))


def _keyword_hashtags(product_dir: Path) -> List[str]:
    values: List[str] = []
    for name in ("keyword-research-ru.json", "keywords-ru.json", "copy-ru.json"):
        path = product_dir / "output" / name
        if not path.is_file():
            continue
        data = load_json(path)
        for key in ("primary_keywords", "secondary_keywords", "keywords", "keywords_ru", "approved_keywords", "usage_scenarios"):
            raw = data.get(key) or []
            if isinstance(raw, list):
                values.extend(str(item) for item in raw)
    tags: List[str] = []
    blocked_terms = _blocked_tag_terms(product_dir)
    for value in values:
        normalized = _canonical_hashtag(value, blocked_terms)
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def _derived_truthful_hashtags(product_dir: Path) -> List[str]:
    """Derive extra tags only from already approved Russian copy tokens."""
    phrases: List[str] = []
    for name in ("copy-ru.json", "ozon-ecommerce-design.json"):
        path = product_dir / "output" / name
        if not path.is_file():
            continue
        data = load_json(path)
        for key in (
            "title_ru", "short_title", "short_title_ru", "product_type_ru",
            "keywords", "keywords_ru", "primary_keywords", "secondary_keywords",
            "usage_scenarios", "usage_scenarios_ru",
        ):
            raw = data.get(key)
            if isinstance(raw, str):
                phrases.append(raw)
            elif isinstance(raw, list):
                phrases.extend(str(item) for item in raw)
    stopwords = {"для", "и", "в", "на", "с", "из", "по", "к", "или"}
    result: List[str] = []
    for phrase in phrases:
        words = re.findall(r"[А-Яа-яЁё]+", phrase)
        sequences = [words]
        sequences.extend([words[index:index + 2] for index in range(max(0, len(words) - 1))])
        sequences.extend([[word] for word in words if word.casefold() not in stopwords])
        for sequence in sequences:
            normalized = _canonical_hashtag(" ".join(sequence))
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def build_tags(product_dir: Path) -> Dict[str, Any]:
    product_id = product_dir.name
    workbench_path = product_dir / "output/workbench-draft.json"
    workbench = load_json(workbench_path) if workbench_path.is_file() else {}
    context_bodies = _product_tag_context_bodies(product_dir)
    manual_tags = workbench.get("tags")
    if manual_tags is not None:
        if not isinstance(manual_tags, list):
            raise ValueError("Workbench tags must be a list")
        tags, warnings = _normalize_tag_list(manual_tags, product_dir)
        return {
            "schema_version": "1.0.0", "product_id": product_id,
            "tags": tags, "count": len(tags), "language": "ru",
            "source_refs": [f"products/{product_id}/output/workbench-draft.json"],
            "warnings": ["Workbench hashtags were normalized under the current Ozon search-tag rule; invalid or unrelated values were omitted."] + warnings,
        }
    def validated_tags(values: Any, *, use_context: bool = True) -> List[str] | None:
        tags, _warnings = _normalize_tag_list(
            values,
            product_dir,
            fill=False,
            context_bodies=context_bodies if use_context else set(),
        )
        if not isinstance(values, list):
            return None
        if (
            len(tags) <= 30
            and len({tag.casefold() for tag in tags}) == len(tags)
            and all(TAG_PATTERN.fullmatch(tag) and 3 <= len(tag) <= 30 for tag in tags)
        ):
            return tags
        return None

    design_path = product_dir / "output/ozon-ecommerce-design.json"
    if design_path.is_file():
        design = load_json(design_path)
        listing = design.get("listing") or {}
        researched_tags, researched_warnings = _normalize_tag_list(
            listing.get("hashtags") or design.get("hashtags"),
            product_dir,
            context_bodies=context_bodies,
        )
        if not researched_tags:
            researched_tags, derived_warnings = _normalize_tag_list(
                [*_keyword_hashtags(product_dir), *_derived_truthful_hashtags(product_dir)],
                product_dir,
                context_bodies=context_bodies,
            )
            researched_warnings += [
                "Designer hashtags were empty or unrelated after product-context filtering; regenerated from current Russian title/copy/keywords."
            ] + derived_warnings
        if researched_tags is not None:
            return {
                "schema_version": "1.0.0", "product_id": product_id,
                "tags": researched_tags, "count": len(researched_tags), "language": "ru",
                "source_refs": [f"products/{product_id}/output/ozon-ecommerce-design.json"],
                "warnings": [
                    "Designer hashtags were normalized during field completion."
                ] + researched_warnings,
            }

    existing_path = product_dir / "output/ozon-tags.json"
    if existing_path.is_file():
        existing = load_json(existing_path)
        existing_tags, existing_warnings = _normalize_tag_list(
            existing.get("tags"),
            product_dir,
            context_bodies=context_bodies,
        )
        if existing_tags is not None:
            source_refs = list(existing.get("source_refs") or [])
            legacy_source_ref = str(existing.get("source_ref") or "").strip()
            if legacy_source_ref and legacy_source_ref not in source_refs:
                source_refs.append(legacy_source_ref)
            if not source_refs:
                source_refs = [
                    f"products/{product_id}/output/ozon-ecommerce-design.json"
                ]
            return {
                "schema_version": "1.0.0",
                "product_id": product_id,
                "tags": existing_tags,
                "count": len(existing_tags),
                "language": "ru",
                "source_refs": source_refs,
                "warnings": list(existing.get("warnings") or []) + [
                    "Validated Russian-copy hashtags were normalized and preserved during field completion."
                ] + existing_warnings,
            }
    # No design tags means no tag attribute.  Never manufacture tags from a
    # product-family bank, packaging text, model code, or generic filler.
    tags, warnings = [], ["No valid product-specific Russian search tags were available; the optional Ozon hashtag attribute will be omitted."]
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "tags": tags,
        "count": len(tags),
        "language": "ru",
        "source_refs": [
            f"products/{product_id}/output/ozon-ecommerce-design.json",
        ],
        "warnings": [
            "Tags describe only the confirmed product type, usage scenes, and purchase motivation; brand and unconfirmed parameters are excluded."
        ] + warnings,
    }


def _ozon_image_urls(result: Dict[str, Any]) -> List[str]:
    raw = result.get("raw_response") or {}
    verification = raw.get("image_verification") or {}
    items = verification.get("items") or []
    if not items:
        return []
    first = items[0]
    urls = list(first.get("primary_image") or []) + list(first.get("images") or [])
    return list(dict.fromkeys(url for url in urls if isinstance(url, str) and url.startswith("https://")))


def _project_relative_path(product_dir: Path, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("image path is empty")
    path = Path(raw)
    if path.is_absolute():
        candidate = path.resolve()
    elif path.parts and path.parts[0] == "products":
        candidate = (ROOT / path).resolve()
    else:
        candidate = (product_dir / path).resolve()
    root = ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"image path escapes project root: {value}")
    return candidate.relative_to(root).as_posix()


def _image_source_ids(item: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for key in ("reference_image_ids", "reference_images", "reference_product_images", "source_image_ids"):
        raw = item.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            values.extend(str(value) for value in raw if str(value).strip())
    return list(dict.fromkeys(values))


def _draft_images_from_image_plan(product_dir: Path) -> List[Dict[str, Any]]:
    plan_path = product_dir / "output/image-plan.json"
    if not plan_path.is_file():
        return []
    plan = load_json(plan_path)
    image_qc_path = product_dir / "output/image-qc-report.json"
    image_qc = load_json(image_qc_path) if image_qc_path.is_file() else {}
    qc_passed = str(image_qc.get("decision") or "").casefold() == "pass"
    checked_by_slot = {
        str(item.get("slot")): item
        for item in image_qc.get("images_checked") or []
        if item.get("slot")
    }
    result: List[Dict[str, Any]] = []

    def add_images(items: Iterable[Dict[str, Any]], role: str) -> None:
        for item in items:
            slot = str(item.get("slot") or "").strip()
            if not slot:
                continue
            path_value = item.get("output_path") or item.get("path") or item.get("local_path")
            try:
                relative_path = _project_relative_path(product_dir, path_value)
            except ValueError:
                continue
            if not (ROOT / relative_path).is_file():
                continue
            checked = checked_by_slot.get(slot) or {}
            qc_status = "pass" if qc_passed and checked else str(item.get("qc_status") or item.get("status") or "not_checked")
            if qc_status in {"generated", "ready", "passed", "complete", "completed"}:
                qc_status = "pass"
            if qc_status not in {"not_checked", "pass", "review_required", "fail"}:
                qc_status = "not_checked"
            draft_image: Dict[str, Any] = {
                "slot": slot,
                "role": role,
                "path": relative_path,
                "source_image_ids": _image_source_ids(item),
                "qc_status": qc_status,
            }
            variant_scope = str(item.get("variant_scope") or "").strip()
            if role == "main" and str(item.get("source_sku_id") or "").strip() not in {"", "all", "shared"}:
                variant_scope = "sku"
            elif role != "main":
                variant_scope = "shared"
            if variant_scope in {"shared", "sku"}:
                draft_image["variant_scope"] = variant_scope
            sku_id = str(item.get("source_sku_id") or "").strip()
            if variant_scope == "sku" and sku_id and sku_id not in {"all", "shared"}:
                draft_image["source_sku_id"] = sku_id
            variant_kind = str(item.get("variant_kind") or "").strip()
            if variant_kind not in {"color", "size_or_measurement", "configuration", "mixed_supported", "not_applicable"}:
                variant_kind = "not_applicable"
            draft_image["variant_kind"] = variant_kind
            variant_value = str(item.get("variant_value") or "").strip()
            if variant_value:
                draft_image["variant_value"] = variant_value
            result.append(draft_image)

    add_images(plan.get("main_images") or [], "main")
    add_images(plan.get("detail_images") or [], "detail")
    add_images(plan.get("disclaimer_images") or [], "disclaimer")
    return result


def sync_draft_images_from_image_plan(
    product_dir: Path,
    draft: Dict[str, Any] | None = None,
    *,
    write: bool = False,
) -> Dict[str, Any]:
    """Fill ozon-draft images from the current image plan when the draft is stale.

    Field completion can run before image generation, so old drafts may contain
    an empty image list.  After image QC passes, upload and Rich Content must use
    the current generated slots, not a historical empty draft.
    """
    draft_path = product_dir / "output/ozon-draft.json"
    current = copy.deepcopy(draft if draft is not None else load_json(draft_path))
    images = current.get("images") or []
    image_plan_images = _draft_images_from_image_plan(product_dir)
    if image_plan_images and len(images) != len(image_plan_images):
        current["images"] = image_plan_images
        if write:
            write_json_atomic(draft_path, current)
    return current


def sync_draft_attributes_from_final_attributes(
    product_dir: Path,
    final_attributes: Dict[str, Any],
    draft: Dict[str, Any] | None = None,
    *,
    write: bool = False,
) -> Dict[str, Any]:
    draft_path = product_dir / "output/ozon-draft.json"
    if draft is None and not draft_path.is_file():
        return {}
    current = copy.deepcopy(draft if draft is not None else load_json(draft_path))
    current["attributes"] = _draft_attributes(final_attributes)
    for sku in current.get("skus") or []:
        sku_id = str(sku.get("source_sku_id") or "")
        sku["attributes"] = _draft_attributes(final_attributes, sku_id=sku_id)
    if write:
        write_json_atomic(draft_path, current)
    return current


def _text(content: str, size: str) -> Dict[str, Any]:
    return {"content": [content], "size": size, "align": "left", "color": "color1"}


def build_rich_content(product_dir: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    product_id = product_dir.name
    urls = _ozon_image_urls(result)
    transfer_path = product_dir / "output/ozon-image-transfer.json"
    if not urls and transfer_path.is_file():
        transfer = load_json(transfer_path)
        if transfer.get("status") in {"confirmed", "MEDIA_CONFIRMED"}:
            urls = _ozon_image_urls({
                "raw_response": {"image_verification": transfer.get("response") or {}}
            })
    draft = sync_draft_images_from_image_plan(product_dir)
    planned = [item for item in draft["images"] if (ROOT / item["path"]).is_file()][:4]
    local_paths = [item["path"] for item in planned]
    roles = ["main", "benefit", "scene", "disclaimer"][:len(local_paths)]
    title_data = load_json(product_dir / "output/title-ru.json")
    description_data = load_json(product_dir / "output/description-ru.json")
    copy_path = product_dir / "output/copy-ru.json"
    copy = load_json(copy_path) if copy_path.is_file() else {}
    title = str(title_data.get("title_ru") or copy.get("title_ru") or "Товар")
    bullets = copy.get("selling_points") or copy.get("bullets_ru") or []
    bullet_texts = [str(item.get("text_ru") if isinstance(item, dict) else item) for item in bullets]
    description_text = str(description_data.get("description_ru") or copy.get("description_ru") or "")
    paragraphs = [part.strip() for part in description_text.split("\n\n") if part.strip()]
    bodies = (bullet_texts + paragraphs) or [title]
    role_titles = {
        "main": title,
        "benefit": "Главное о товаре",
        "scene": "В повседневном использовании",
        "disclaimer": "Перед заказом",
    }
    texts: List[Tuple[str, str]] = [
        (role_titles.get(role, title), bodies[index % len(bodies)])
        for index, role in enumerate(roles)
    ]
    warnings: List[str] = []
    status = "ready"
    if len(urls) < len(local_paths):
        status = "ready_for_upload"
        warnings.append("Local image assets must be resolved to temporary HTTPS URLs during the production image-upload step.")
        urls = [f"asset://{item['slot']}" for item in planned]
    source_images = []
    widgets = []
    for role, local_path, public_url, (title, body) in zip(roles, local_paths, urls, texts):
        source_images.append({"role": role, "local_path": local_path, "public_url": public_url})
        widgets.append({
            "widgetName": "raShowcase",
            "type": "billboard",
            "blocks": [{
                "img": {"src": public_url, "srcMobile": public_url},
                "title": _text(title, "size4"),
                "text": _text(body, "size2"),
            }],
        })
    payload = {"version": 0.3, "content": widgets}
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "attribute_id": 0,
        "language": "ru",
        "format": "ozon_rich_content_json",
        "status": status,
        "content": payload,
        "serialized_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "source_images": source_images,
        "warnings": warnings,
    }


def _color_from_sku_name(name: str) -> str:
    # One shared rule for all Ozon-facing color fields: extract only the color
    # word, never capacity/model/spec fragments such as ``1.9L`` or
    # ``601-800 мл``.
    return russian_color_or_unknown(name)


def _dictionary_value(metadata: Dict[str, Any], value: str) -> Tuple[str, int] | None:
    """Resolve a value against the live Ozon dictionary without inventing values.

    Ozon dictionaries often use a shorter Russian label than the wording in
    the 1688 source (for example ``нержавейка`` vs ``Нержавеющая сталь`` or
    ``без`` vs ``Нет``).  We first require an exact normalized match, then use
    a deliberately small synonym table.  The returned value always comes from
    ``allowed_values``; source wording is never sent to Ozon directly.
    """
    normalized = _normalized_attribute_name(value)
    if not normalized:
        return None

    def canonical(raw: Any) -> str:
        token = _normalized_attribute_name(raw)
        aliases = {
            "нержавейка": "нержавеющаясталь",
            "нержсталь": "нержавеющаясталь",
            "нержавеющаясталь316s": "нержавеющаясталь",
            "нержавеющаясталь304s": "нержавеющаясталь",
            "stainlesssteel": "нержавеющаясталь",
            "без": "нет",
            "отсутствует": "нет",
            "отсутствие": "нет",
            "none": "нет",
            "no": "нет",
            "ручнойблендер": "ручной",
            "ручноймиксер": "ручной",
            "portable": "портативный",
            "портативный": "портативный",
            "портативная": "портативный",
            "проводной": "сеть",
            "отсети": "сеть",
            "аккумуляторный": "беспроводной",
            "зарядный": "беспроводной",
            "нержавеющая": "нержавеющаясталь",
            "металлический": "металл",
            "металлическая": "металл",
        }
        return aliases.get(token, token)

    canonical_value = canonical(normalized)
    for item in metadata.get("allowed_values") or []:
        if _normalized_attribute_name(item.get("value")) == normalized:
            return str(item["value"]), int(item["id"])
        if canonical(item.get("value")) == canonical_value:
            return str(item["value"]), int(item["id"])
    return None


def _source_attribute_value(source: Dict[str, Any], names: Iterable[str]) -> str | None:
    def normalize_source_name(value: Any) -> str:
        return re.sub(r"[^a-zа-яё0-9\u4e00-\u9fff]", "", str(value or "").casefold())

    expected = {normalize_source_name(name) for name in names}
    for item in source.get("product_attributes") or []:
        name = normalize_source_name(item.get("name_cn"))
        value = str(item.get("value_cn") or "").strip()
        if name in expected and value and value.casefold() != "unknown" and len(value) <= 200:
            return value
    return None


def _dictionary_match_from_source_text(
    metadata: Dict[str, Any], source_text: str,
) -> Tuple[str, int] | None:
    """Choose one live dictionary value explicitly supported by source text.

    This is intentionally conservative: values shorter than three normalized
    characters are ignored and a match is accepted only when exactly one
    dictionary entry is supported.  The value returned is always the live
    dictionary label, never the source wording.
    """
    text = _normalized_attribute_name(source_text)
    if not text:
        return None
    source_aliases = {
        "不锈钢": "нержавеющаясталь",
        "不锈钢316s": "нержавеющаясталь",
        "不锈钢304s": "нержавеющаясталь",
        "塑料": "пластик",
        "硅胶": "силикон",
        "橡胶": "резина",
        "陶瓷": "керамика",
        "木制": "дерево",
        "木质": "дерево",
        "无": "нет",
        "没有": "нет",
        "不带": "нет",
        "带盖": "крышка",
        "含盖": "крышка",
        "配盖": "крышка",
    }
    for raw, canonical in source_aliases.items():
        if _normalized_attribute_name(raw) in text:
            text += canonical
    matches = []
    for item in metadata.get("allowed_values") or []:
        candidate = _normalized_attribute_name(item.get("value"))
        if len(candidate) < 3:
            continue
        # Exact source occurrence handles Chinese/Russian/English labels;
        # aliases above handle common 1688 wording differences.
        if candidate in text:
            matches.append(item)
    if len(matches) != 1:
        return None
    item = matches[0]
    return str(item["value"]), int(item["id"])


def _explicit_dimensions_cm(source: Dict[str, Any]) -> Tuple[float, float, float | None] | None:
    values = []
    direct = _source_attribute_value(source, (
        "尺寸", "产品尺寸", "商品尺寸", "规格尺寸", "长宽高",
        "规格(长*宽*高)", "规格（长*宽*高）", "规格(长×宽×高)",
        "产品规格(长*宽*高)", "商品规格(长*宽*高)",
    ))
    if direct:
        values.append(direct)
    for sku in source.get("skus") or []:
        values.append(str(sku.get("sku_name") or ""))
        values.extend(str(item.get("value_cn") or "") for item in sku.get("option_values") or [])
    pattern = re.compile(
        r"(?P<a>\d+(?:[.,]\d+)?)\s*(?P<unit_a>mm|毫米|cm|厘米)?\s*[x×*]\s*"
        r"(?P<b>\d+(?:[.,]\d+)?)\s*(?P<unit_b>mm|毫米|cm|厘米)?"
        r"(?:\s*[x×*]\s*(?P<c>\d+(?:[.,]\d+)?)\s*(?P<unit_c>mm|毫米|cm|厘米)?)?",
        re.IGNORECASE,
    )
    matches = [match for value in values if (match := pattern.search(value))]
    if not matches:
        return None
    converted = []
    for match in matches:
        units = [
            match.group(name) for name in ("unit_a", "unit_b", "unit_c")
            if match.group(name)
        ]
        if not units:
            continue
        normalized_units = {
            "mm" if unit.casefold() in {"mm", "毫米"} else "cm" for unit in units
        }
        if len(normalized_units) != 1:
            continue
        factor = 0.1 if "mm" in normalized_units else 1.0
        converted.append((
            float(match.group("a").replace(",", ".")) * factor,
            float(match.group("b").replace(",", ".")) * factor,
            float(match.group("c").replace(",", ".")) * factor if match.group("c") else None,
        ))
    return converted[0] if converted and all(item == converted[0] for item in converted) else None


def _plain_dimension_attributes(metadata: Dict[str, Any]) -> Dict[str, Tuple[Dict[str, Any], str]]:
    """Return only plain L/W/H fields and the unit printed in the Ozon name.

    Related measurements such as seat width or back height must never receive
    the overall product dimensions.
    """
    dimensions: Dict[str, Tuple[Dict[str, Any], str]] = {}
    axis_names = {
        "длина": "length",
        "ширина": "width",
        "высота": "height",
        "глубина": "length",
    }
    pattern = re.compile(
        r"^\s*(длина|ширина|высота|глубина)[\s,，:()（）]*(мм|mm|см|cm)\s*$",
        re.IGNORECASE,
    )
    for item in metadata.get("attributes") or []:
        match = pattern.match(str(item.get("attribute_name") or ""))
        if not match:
            continue
        axis = axis_names[match.group(1).casefold()]
        # Prefer the literal length field if a category exposes both length
        # and depth; depth is only an exact-name fallback.
        if axis in dimensions and match.group(1).casefold() == "глубина":
            continue
        unit = "mm" if match.group(2).casefold() in {"мм", "mm"} else "cm"
        dimensions[axis] = (item, unit)
    return dimensions


def _measurement_override_attribute_ids(metadata: Dict[str, Any]) -> set[int]:
    aliases = (
        ("Вес товара, г", "Вес товара", "Вес, г"),
        ("Вес товара с упаковкой", "Вес с упаковкой", "Вес с упаковкой, г", "Вес упаковки"),
        ("Длина, мм", "Длина товара, мм", "Длина, см", "Длина товара, см"),
        ("Ширина, мм", "Ширина товара, мм", "Ширина, см", "Ширина товара, см"),
        ("Высота, мм", "Высота товара, мм", "Высота, см", "Высота товара, см"),
        ("Размер упаковки", "Габариты упаковки"),
        ("Размеры, мм", "Размеры товара, мм"),
    )
    result: set[int] = set()
    for names in aliases:
        attribute = _find_attribute_by_names(metadata, names)
        if attribute:
            result.add(int(attribute["attribute_id"]))
    return result


def _explicit_weight_g(source: Dict[str, Any]) -> int | None:
    raw = _source_attribute_value(source, ("重量", "产品重量", "单品重量", "净重"))
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克)", raw, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    grams = value * 1000 if match.group(2).casefold() in {"kg", "千克", "公斤"} else value
    return int(math.ceil(grams))


def _reliable_dynamic_attributes(
    product_dir: Path, metadata: Dict[str, Any]
) -> Dict[int, Tuple[Any, str, float, List[str], int | None]]:
    source = load_json(product_dir / "input/source.json")
    manual_path = product_dir / "input/manual-confirmation.json"
    manual = load_json(manual_path) if manual_path.is_file() else {}
    manual_fields = manual.get("fields") or {}
    analysis_path = product_dir / "output/product-analysis.json"
    analysis = load_json(analysis_path) if analysis_path.is_file() else {}
    title = str(source.get("title_cn") or "")
    result: Dict[int, Tuple[Any, str, float, List[str], int | None]] = {}

    material_terms = (
        (("pp5", "polypropylene", "полипропилен"), "Полипропилен (PP)"),
        (("硅胶", "силикон", "silicone"), "Силикон"),
        (("不锈钢", "нержавеющая сталь", "stainless steel"), "Нержавеющая сталь"),
        (("铝合金", "алюминий", "aluminium", "aluminum"), "Алюминий"),
        (("塑料", "пластик", "plastic"), "Пластик"),
        (("橡胶", "резина", "rubber"), "Резина"),
        (("陶瓷", "керамика", "ceramic"), "Керамика"),
        (("木", "дерево", "wood"), "Дерево"),
    )
    material_raw = _source_attribute_value(source, ("材质", "产品材质", "主要材质")) or ""
    analysis_materials = (analysis.get("facts") or {}).get("materials") or []
    material_text = f"{title} {material_raw} {manual_fields.get('material') or ''} {json.dumps(analysis_materials, ensure_ascii=False)}".casefold()
    material_meta = _find_attribute_by_names(metadata, (
        "Материал", "Материал изделия", "Основной материал контейнера",
    ))
    if material_meta:
        material = next(
            (russian for terms, russian in material_terms if any(term.casefold() in material_text for term in terms)),
            None,
        )
        selected = _dictionary_value(material_meta, material) if material else None
        if selected:
            result[int(material_meta["attribute_id"])] = (
                selected[0], "1688", 1.0,
                ["source.title_cn" if not material_raw else "source.product_attributes.material"],
                selected[1],
            )

    colors = {
        _color_from_sku_name(str(sku.get("sku_name") or ""))
        for sku in source.get("skus") or []
    }
    colors.discard("unknown")
    manual_color = _color_from_sku_name(str(manual_fields.get("color") or ""))
    if manual_color != "unknown":
        colors = {manual_color}
    if len(colors) == 1:
        color = next(iter(colors))
        color_meta = _find_attribute_by_names(metadata, ("Цвет товара", "Цвет"))
        selected = _dictionary_value(color_meta, color) if color_meta else None
        if selected:
            evidence = (
                ["input/manual-confirmation.json#/fields/color"]
                if manual_color != "unknown" else ["source.skus[].sku_name"]
            )
            result[int(color_meta["attribute_id"])] = (
                selected[0], "human_override" if manual_color != "unknown" else "1688", 1.0, evidence, selected[1]
            )
            color_name_meta = _find_attribute_by_names(metadata, ("Название цвета",))
            if color_name_meta:
                result[int(color_name_meta["attribute_id"])] = (
                    selected[0], "human_override" if manual_color != "unknown" else "1688", 1.0, evidence, None
                )

    dimensions = _explicit_dimensions_cm(source)
    if dimensions:
        axes = {"length": dimensions[0], "width": dimensions[1], "height": dimensions[2]}
        for axis, (attribute, unit) in _plain_dimension_attributes(metadata).items():
            value_cm = axes[axis]
            if value_cm is not None:
                value = value_cm * 10.0 if unit == "mm" else value_cm
                if float(value).is_integer():
                    value = int(value)
                result[int(attribute["attribute_id"])] = (
                    value,
                    "1688",
                    1.0,
                    [f"source product/SKU dimensions converted from cm to {unit}"],
                    None,
                )

    weight = _explicit_weight_g(source)
    weight_meta = _find_attribute_by_names(metadata, ("Вес товара, г", "Вес, г"))
    if weight is not None and weight_meta:
        result[int(weight_meta["attribute_id"])] = (
            weight, "1688", 1.0, ["source.product_attributes.weight"], None
        )

    country_raw = _source_attribute_value(source, ("原产地", "产地", "生产地", "原产国"))
    country_meta = _find_attribute_by_names(metadata, ("Страна-изготовитель", "Страна производства"))
    if country_meta and (
        not country_raw or any(token in country_raw for token in ("中国", "Китай", "China"))
    ):
        selected = _dictionary_value(country_meta, "Китай")
        if selected:
            result[int(country_meta["attribute_id"])] = (
                selected[0],
                "1688" if country_raw else "workspace_default",
                1.0,
                ["source.product_attributes.country"] if country_raw else ["workspace policy: 1688 sourcing origin defaults to China"],
                selected[1],
            )

    explicit_quantity = _source_attribute_value(
        source, ("包装数量", "单品数量", "每件数量", "一件数量")
    )
    quantity = 1
    if explicit_quantity:
        match = re.search(r"\d+", explicit_quantity)
        if match:
            quantity = int(match.group())
    quantity_source = "1688" if explicit_quantity else "workspace_default"
    quantity_evidence = (
        ["source.product_attributes.package_quantity"]
        if explicit_quantity else ["workspace policy: one unit and one factory package by default"]
    )
    for aliases in (
        ("Единиц в одном товаре", "Количество в одном товаре"),
        ("Количество товара в УЕИ",),
        ("Количество заводских упаковок", "Количество фабричных упаковок"),
        ("Количество в упаковке", "Количество в упаковке, шт", "Единиц в упаковке"),
    ):
        attribute = _find_attribute_by_names(metadata, aliases)
        if attribute:
            result[int(attribute["attribute_id"])] = (
                quantity, quantity_source, 1.0, quantity_evidence, None
            )
    return result


def _safe_optional_attributes(
    product_dir: Path, metadata: Dict[str, Any]
) -> Dict[int, Tuple[Any, str, float, List[str], int | None]]:
    """Fill low-risk optional fields from source text or recorded visual inference."""
    source = load_json(product_dir / "input/source.json")
    manual_path = product_dir / "input/manual-confirmation.json"
    manual = load_json(manual_path) if manual_path.is_file() else {}
    manual_fields = manual.get("fields") or {}
    analysis_path = product_dir / "output/product-analysis.json"
    analysis = load_json(analysis_path) if analysis_path.is_file() else {}
    result: Dict[int, Tuple[Any, str, float, List[str], int | None]] = {}

    seller_code = _find_attribute_by_names(metadata, ("Код продавца",))
    if seller_code:
        result[int(seller_code["attribute_id"])] = (
            product_dir.name,
            "workspace_default",
            1.0,
            ["stable local product_id used as seller code"],
            None,
        )

    title_and_category = " ".join([
        str(source.get("title_cn") or ""),
        str(analysis.get("product_type") or ""),
        str(analysis.get("category") or ""),
    ]).casefold()
    purpose = _find_attribute_by_names(metadata, ("Назначение емкости для хранения",))
    if purpose and any(token in title_and_category for token in (
        "米桶", "米缸", "大米", "面粉", "五谷", "杂粮", "食品储藏",
        "сыпуч", "рис", "мук", "круп",
    )):
        selected = _dictionary_value(purpose, "Банка для сыпучих продуктов")
        if selected:
            result[int(purpose["attribute_id"])] = (
                selected[0],
                "1688",
                0.99,
                ["source.title_cn describes rice, flour or grain storage"],
                selected[1],
            )

    # The analysis stage intentionally separates explicit seller facts from AI
    # inferences.  Optional Ozon fields may still be derived from those facts
    # when the category dictionary makes the meaning unambiguous.  This keeps
    # the workbench useful without inventing power, certification, warranty,
    # safety systems or other claims that are not present in the source.
    facts = analysis.get("facts") or {}
    sku_text = " ".join(
        " ".join([
            str(sku.get("sku_name") or ""),
            *[
                str(option.get("value_cn") or option.get("value") or "")
                for option in sku.get("option_values") or []
            ],
        ])
        for sku in source.get("skus") or []
    )
    fact_text = " ".join([
        str(source.get("title_cn") or ""),
        str(analysis.get("product_type") or ""),
        str(analysis.get("category") or ""),
        sku_text,
        json.dumps(facts.get("functions") or [], ensure_ascii=False),
        json.dumps(facts.get("materials") or [], ensure_ascii=False),
        json.dumps(facts.get("accessories") or [], ensure_ascii=False),
        str(manual_fields.get("structure") or ""),
    ]).casefold()

    def add_dictionary(
        attribute_names: Iterable[str],
        value: str,
        *,
        source_name: str,
        confidence: float,
        evidence: List[str],
    ) -> bool:
        attribute = _find_attribute_by_names(metadata, attribute_names)
        selected = _dictionary_value(attribute, value) if attribute else None
        if not selected:
            return False
        result[int(attribute["attribute_id"])] = (
            selected[0], source_name, confidence, evidence, selected[1]
        )
        return True

    def add_text(
        attribute_names: Iterable[str],
        value: str,
        *,
        source_name: str,
        confidence: float,
        evidence: List[str],
    ) -> bool:
        attribute = _find_attribute_by_names(metadata, attribute_names)
        value = value.strip()
        if not attribute or not value:
            return False
        result[int(attribute["attribute_id"])] = (
            value, source_name, confidence, evidence, None
        )
        return True

    if any(token in fact_text for token in ("带盖", "透明盖", "含盖", "крыш")):
        add_dictionary(
            ("Особенности посуды",), "Крышка в комплекте",
            source_name="human_override" if manual_fields.get("structure") else "1688",
            confidence=1.0,
            evidence=["input/manual-confirmation.json#/fields/structure"],
        )
        add_text(
            ("Комплектация",), "контейнер, прозрачная крышка, передняя ручка",
            source_name="human_override" if manual_fields.get("structure") else "1688",
            confidence=1.0,
            evidence=["input/manual-confirmation.json#/fields/structure"],
        )

    portable_mixer = (
        any(token in fact_text for token in (
            "榨汁杯", "便携式", "充电", "портатив", "миксер-стакан",
        ))
        and any(token in fact_text for token in ("榨汁", "миксер", "сок"))
    )
    if portable_mixer:
        derived_evidence = [
            "source.title_cn describes a portable rechargeable juicer cup",
            "product-analysis.product_type",
        ]
        add_dictionary(
            ("Тип миксера",), "Ручной",
            source_name="AI_estimated", confidence=0.96,
            evidence=derived_evidence,
        )
        add_dictionary(
            ("Вращающаяся чаша",), "Нет",
            source_name="AI_estimated", confidence=0.93,
            evidence=derived_evidence,
        )
        add_dictionary(
            ("Планетарный механизм",), "Нет",
            source_name="AI_estimated", confidence=0.97,
            evidence=derived_evidence,
        )
        if any(token in fact_text for token in ("充电", "recharge", "аккумулятор")):
            add_dictionary(
                ("Конструктивные особенности",), "Беспроводной",
                source_name="AI_estimated", confidence=0.94,
                evidence=["source.title_cn explicitly describes rechargeable operation"],
            )

    stainless_sku_count = sum(
        any(token in " ".join([
            str(sku.get("sku_name") or ""),
            *[str(option.get("value_cn") or "") for option in sku.get("option_values") or []],
        ]).casefold() for token in ("不锈钢", "stainless", "нержаве"))
        for sku in source.get("skus") or []
    )
    selected_sku_count = len(source.get("skus") or [])
    if selected_sku_count and stainless_sku_count == selected_sku_count:
        material_evidence = [
            "source.skus[].sku_name explicitly identifies stainless steel for every selected SKU",
            "product-analysis.facts.materials",
        ]
        add_dictionary(
            ("Материал чаши",), "Металл",
            source_name="1688", confidence=1.0, evidence=material_evidence,
        )
        add_dictionary(
            ("Материал корпуса",), "Нержавеющая сталь",
            source_name="1688", confidence=0.98, evidence=material_evidence,
        )

    title_path = product_dir / "output/title-ru.json"
    if title_path.is_file():
        title_ru = str(load_json(title_path).get("title_ru") or "").split(",", 1)[0].strip()
        if title_ru:
            add_text(
                ("Название модели для шаблона наименования",), title_ru,
                source_name="AI_estimated", confidence=0.96,
                evidence=["title-ru.title_ru", "product-analysis.product_type"],
            )

    # Analysis facts may contain Chinese evidence prose and local image paths.
    # Those are useful internally but must never be copied into an Ozon-facing
    # text field.  Convert only explicit, supported accessory nouns to concise
    # Russian labels; discard unsupported evidence rather than inventing it.
    structural_tokens = (
        "带盖", "含盖", "配盖", "内盖", "外盖", "外旋盖", "带门",
        "крыш", "дверц", "крышка",
    )
    package_parts: List[str] = []

    def append_package_part(value: str) -> None:
        if value and value not in package_parts:
            package_parts.append(value)

    for item in facts.get("accessories") or []:
        text = str(item).strip()
        if not text or text.casefold() in {"unknown", "неизвестно"}:
            continue
        lowered = text.casefold()
        has_variant_marker = bool(re.search(
            r"(?:sku\s*)?(\d+(?:\.\d+)?)\s*(?:ml|мл|毫升)", lowered,
            re.IGNORECASE,
        ))
        if "内盖" in text:
            append_package_part("внутренняя крышка")
        if "外旋盖" in text:
            append_package_part("внешняя винтовая крышка")
        elif "外盖" in text:
            append_package_part("внешняя крышка")
        if any(token in lowered for token in structural_tokens):
            match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:ml|мл|毫升)", lowered,
                re.IGNORECASE,
            )
            if match and has_variant_marker:
                append_package_part(f"крышка для варианта {match.group(1)} мл")
            # Structural wording has now either been converted or deliberately
            # ignored.  Never append its raw evidence sentence.
            continue
        if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text):
            continue
        if re.search(r"(?:[/\\]|\.(?:jpe?g|png|webp)\b)", text, re.IGNORECASE):
            continue
        append_package_part(text)
    if package_parts:
        add_text(
            ("Комплектация",), " ".join(package_parts),
            source_name="1688",
            confidence=0.95,
            evidence=[
                "product-analysis.facts.accessories",
            ],
        )

    inference_fields = {
        str(item.get("field") or "").casefold(): item
        for item in analysis.get("inferences") or []
        if isinstance(item, dict)
    }
    confidence_values = {"high": 0.92, "medium": 0.80, "low": 0.60}
    for field_names, attribute_names in (
        (("shape", "product_shape"), ("Форма",)),
        (("visible_feature", "visible_features"), ("Особенности посуды",)),
    ):
        inference = next((inference_fields.get(name) for name in field_names if inference_fields.get(name)), None)
        attribute = _find_attribute_by_names(metadata, attribute_names)
        if not inference or not attribute:
            continue
        raw_value = inference.get("value")
        if isinstance(raw_value, list):
            raw_value = raw_value[0] if raw_value else None
        selected = _dictionary_value(attribute, str(raw_value or ""))
        if not selected:
            continue
        evidence = [str(item) for item in inference.get("basis") or [] if str(item).strip()]
        result[int(attribute["attribute_id"])] = (
            selected[0],
            "AI_estimated",
            confidence_values.get(str(inference.get("confidence") or "").casefold(), 0.75),
            evidence or ["product-analysis.inferences"],
            selected[1],
        )
    return result


def build_color_variants(product_dir: Path, source: Dict[str, Any]) -> Dict[str, Any]:
    variants = []
    warnings = []
    if source["skus"] and all(_color_from_sku_name(item["sku_name"]) == "unknown" for item in source["skus"]):
        return {
            "schema_version": "1.0.0",
            "product_id": product_dir.name,
            "variants": [],
            "summary": {"total": 0, "mapped": 0, "missing": 0},
            "warnings": ["Selected SKUs differ by package configuration, not by color; color images are not applicable."],
        }
    project_root = product_dir.parents[1]
    plan_path = product_dir / "output/image-plan.json"
    image_qc_path = product_dir / "output/image-qc-report.json"
    plan = load_json(plan_path) if plan_path.is_file() else {"main_images": []}
    image_qc = load_json(image_qc_path) if image_qc_path.is_file() else {}
    qc_slots = {
        str(item.get("slot")) for item in image_qc.get("images_checked") or []
    } if image_qc.get("decision") == "pass" else set()
    generated_variant_mains = {
        str(item.get("source_sku_id")): item
        for item in plan.get("main_images") or []
        if item.get("variant_scope") == "sku"
        and str(item.get("source_sku_id") or "").strip() not in {"", "all", "shared"}
        and (
            str(item.get("image_type") or "").casefold() == "main"
            or str(item.get("layout_type") or "").casefold() == "sku_main"
            or str(item.get("slot") or "").startswith("main-")
        )
        and item.get("slot") in qc_slots
        and (project_root / str(item.get("output_path") or "missing")).is_file()
    }
    ai_qc_path = product_dir / "output/color-variant-qc.json"
    ai_qc = load_json(ai_qc_path) if ai_qc_path.is_file() else {"variants": []}
    ai_passed = {
        str(item["sku_id"]): item
        for item in ai_qc.get("variants", [])
        if item.get("status") == "pass"
        and min(
            float(item.get("product_consistency", 0)),
            float(item.get("color_consistency", 0)),
            float(item.get("structure_consistency", 0)),
            float(item.get("material_consistency", 0)),
            float(item.get("angle_consistency", 0)),
        ) >= 90
    }
    for sku in source["skus"]:
        sku_id = str(sku["sku_id"])
        image = str(
            sku.get("variant_local_image_path")
            or sku.get("local_image_path")
            or sku.get("image_path")
            or sku.get("sku_image_path")
            or sku.get("image_local_path")
            or "unknown"
        )
        if sku_id in generated_variant_mains:
            generated = generated_variant_mains[sku_id]
            image = str(generated["output_path"])
            status = "mapped"
            image_source = "generated_from_real_sku_reference"
            resolution_level = 3
            confidence = 1.0
            reason = "This SKU-specific generated main is tied to the current SKU and passed the common image QC gate."
        elif sku_id in ai_passed:
            generated = str(ai_passed[sku_id].get("image", "missing"))
            if generated != "missing" and (project_root / generated).is_file():
                image = generated
                status = "mapped"
                image_source = str(ai_passed[sku_id].get("source") or "ai_generated")
                resolution_level = 3
                confidence = min(
                    float(ai_passed[sku_id][key])
                    for key in (
                        "product_consistency", "color_consistency",
                        "structure_consistency", "material_consistency",
                        "angle_consistency",
                    )
                ) / 100
                reason = "A variant main generated from this exact real SKU reference passed product, color, structure, and SKU consistency QC."
            else:
                image = "missing"
                status, image_source = "missing", "missing"
                resolution_level, confidence = 4, 0
                reason = "The generated variant candidate is absent or does not resolve to a local file."
        elif image != "unknown" and (project_root / image).is_file():
            image_source = "sku_property_value" if sku.get("variant_image_source") == "sku_property_value" else "sku_image"
            status = "mapped"
            resolution_level, confidence = 1, 1.0
            reason = "The image is directly associated with this SKU in the 1688 source data."
        else:
            image = "missing"
            status, image_source = "missing", "missing"
            resolution_level, confidence = 4, 0
            reason = "1688 supplied no SKU image, no verified main image matches, and no AI-generated candidate passed SKU consistency QC."
            warnings.append(f"SKU {sku_id} ({sku['sku_name']}) has no reliable color-variant image.")
        variants.append({
            "sku_id": sku_id,
            "sku_name": sku["sku_name"],
            "color": _color_from_sku_name(sku["sku_name"]),
            "image": image,
            "source": image_source,
            "resolution_level": resolution_level,
            "confidence": confidence,
            "status": status,
            "reason": reason,
        })
    mapped = sum(item["status"] == "mapped" for item in variants)
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "variants": variants,
        "summary": {"total": len(variants), "mapped": mapped, "missing": len(variants) - mapped},
        "warnings": warnings,
    }


def build_color_variant_policy(
    product_id: str,
    source: Dict[str, Any],
    colors: Dict[str, Any],
) -> Dict[str, Any]:
    ordered_skus = sorted(source["skus"], key=lambda item: item.get("selection_order", 9999))
    if not colors["variants"]:
        return {
            "schema_version": "1.0.0",
            "product_id": product_id,
            "strategy": "block_main_warn_optional",
            "status": "PASS",
            "main_sku_id": "not_applicable",
            "required_sale_skus": [],
            "missing_count": 0,
            "blocking_variants": [],
            "warning_variants": [],
        }
    main_sku_id = str(ordered_skus[0]["sku_id"])
    required_sale_skus = [main_sku_id]
    missing = [item for item in colors["variants"] if item["status"] == "missing"]
    blocking = []
    warnings = []
    for item in missing:
        target = blocking if item["sku_id"] in required_sale_skus else warnings
        target.append({
            "sku_id": item["sku_id"],
            "sku_name": item["sku_name"],
            "reason": item["reason"],
        })
    status = "BLOCK" if blocking else ("WARNING" if warnings else "PASS")
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "strategy": "block_main_warn_optional",
        "status": status,
        "main_sku_id": main_sku_id,
        "required_sale_skus": required_sale_skus,
        "missing_count": len(missing),
        "blocking_variants": blocking,
        "warning_variants": warnings,
    }


def build_attributes(
    product_dir: Path, metadata: Dict[str, Any], config: Dict[str, Any],
    description: Dict[str, Any], tags: Dict[str, Any], rich: Dict[str, Any],
    include_rich_content: bool = True,
) -> Dict[str, Any]:
    product_id = product_dir.name
    source = load_json(product_dir / "input/source.json")
    sku_dimension_values = {
        tuple(
            float((sku.get("source_data") or {}).get("external_dimensions_cm", {}).get(key) or 0)
            for key in ("length", "width", "height")
        )
        for sku in source.get("skus") or []
        if isinstance((sku.get("source_data") or {}).get("external_dimensions_cm"), dict)
    }
    varying_sku_dimensions = len(sku_dimension_values) > 1 and all(
        all(value > 0 for value in dimensions) for dimensions in sku_dimension_values
    )
    # Attribute provenance uses the schema's stable source enum.  The richer
    # ``estimated_human_approved`` provenance remains in upload-config and in
    # the evidence path, while the attribute itself stays AI_estimated rather
    # than introducing an invalid new enum value.
    package_manual = (
        config["package_weight"]["source_status"] == "estimated_human_approved"
        and config["package_dimensions"]["source_status"] == "estimated_human_approved"
    )
    package_measurements_source = "1688" if (
        config["package_weight"]["source_status"] == "confirmed_source"
        and config["package_dimensions"]["source_status"] == "confirmed_source"
    ) else "AI_estimated"
    package_measurements_confidence = 1.0 if package_manual or package_measurements_source == "1688" else 0.68
    product_manual = (
        config["product_weight"]["source_status"] == "estimated_human_approved"
        and config["product_dimensions"]["source_status"] == "estimated_human_approved"
    )
    product_measurements_source = "1688" if (
        config["product_weight"]["source_status"] == "confirmed_source"
        and config["product_dimensions"]["source_status"] == "confirmed_source"
    ) else "AI_estimated"
    product_measurements_confidence = 1.0 if product_manual or product_measurements_source == "1688" else 0.68
    supplied: Dict[int, Tuple[Any, str, float, List[str], int | None]] = {
        int(config["brand"]["attribute_id"]): (config["brand"]["value"], "AI_estimated", 0.90, ["store policy: products without a confirmed brand use Нет бренда"], config["brand"]["dictionary_value_id"]),
        int(config["type"]["attribute_id"]): (config["type"]["value"], "AI_estimated", 0.99, ["product-analysis.product_type", "ozon-category.category_name"], config["type"]["dictionary_value_id"]),
    }
    configured_model_attribute_id = int((config.get("model_name") or {}).get("attribute_id") or 0)
    if configured_model_attribute_id > 0:
        supplied[configured_model_attribute_id] = (
            config["model_name"]["value"],
            "AI_estimated",
            0.95,
            ["ozon-upload-config.model_name", "product-analysis.product_type"],
            None,
        )
    # Model name is a system grouping key.  It must be one stable random-looking
    # number for the whole product card, shared by every SKU and every Ozon model
    # alias in this category.  It is intentionally not copied from the title and
    # not overridden by workbench drafts.
    model_attribute_names = {
        _normalized_attribute_name(name) for name in MODEL_ATTRIBUTE_NAMES
    }
    model_attribute_ids: set[int] = set()
    for item in metadata.get("attributes") or []:
        if _normalized_attribute_name(item.get("attribute_name")) not in model_attribute_names:
            continue
        model_attribute_ids.add(int(item["attribute_id"]))
        supplied.setdefault(
            int(item["attribute_id"]),
            (
                config["model_name"]["value"],
                "AI_estimated",
                0.95,
                ["ozon-upload-config.model_name", MODEL_NAME_STRATEGY],
                None,
            ),
        )
    supplied.update(_reliable_dynamic_attributes(product_dir, metadata))
    supplied.update(_safe_optional_attributes(product_dir, metadata))
    workbench_path = product_dir / "output/workbench-draft.json"
    workbench = load_json(workbench_path) if workbench_path.is_file() else {}
    manual_attributes = workbench.get("attributes") or {}
    has_confirmed_source_measurements = any(
        config[field]["source_status"] == "confirmed_source"
        for field in ("product_weight", "product_dimensions", "package_weight", "package_dimensions")
    )
    protected_measurement_attribute_ids = (
        _measurement_override_attribute_ids(metadata)
        if has_confirmed_source_measurements else set()
    )
    for item in metadata.get("attributes") or []:
        attribute_id = int(item["attribute_id"])
        raw_value = manual_attributes.get(str(attribute_id), manual_attributes.get(attribute_id))
        if raw_value in {None, "", "unknown"}:
            continue
        if attribute_id in model_attribute_ids:
            continue
        if attribute_id in protected_measurement_attribute_ids:
            continue
        dictionary_id = None
        allowed_values = item.get("allowed_values") or []
        if allowed_values:
            match = next(
                (entry for entry in allowed_values if str(entry.get("value") or "").strip().casefold() == str(raw_value).strip().casefold()),
                None,
            )
            if not match:
                raise ValueError(
                    f"Workbench attribute {attribute_id} value is absent from the live Ozon dictionary"
                )
            raw_value = str(match["value"])
            dictionary_id = int(match["id"])
        supplied[attribute_id] = (
            raw_value, "human_override", 1.0,
            ["workbench-draft.attributes", f"attribute_id={attribute_id}"], dictionary_id,
        )
    for attribute_id in model_attribute_ids:
        supplied[attribute_id] = (
            config["model_name"]["value"],
            "AI_estimated",
            0.95,
            ["ozon-upload-config.model_name", MODEL_NAME_STRATEGY],
            None,
        )
    def display_number(value: Any) -> str:
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"

    role_values = {
        "product_weight": (config["product_weight"]["value_g"], product_measurements_source, product_measurements_confidence, [config["product_weight"]["source"]], None),
        "product_length": (config["product_dimensions"]["length_mm"], product_measurements_source, product_measurements_confidence, [config["product_dimensions"]["source"]], None),
        "product_width": (config["product_dimensions"]["width_mm"], product_measurements_source, product_measurements_confidence, [config["product_dimensions"]["source"]], None),
        "product_height": (config["product_dimensions"]["height_mm"], product_measurements_source, product_measurements_confidence, [config["product_dimensions"]["source"]], None),
        "package_weight": (config["package_weight"]["value_g"], package_measurements_source, package_measurements_confidence, [config["package_weight"]["source"]], None),
        "package_dimensions": (
            " x ".join(display_number(config["package_dimensions"][key]) for key in ("length_mm", "width_mm", "height_mm")),
            package_measurements_source, package_measurements_confidence, [config["package_dimensions"]["source"]], None,
        ),
        "product_dimensions": (
            " x ".join(display_number(config["product_dimensions"][key]) for key in ("length_mm", "width_mm", "height_mm")),
            product_measurements_source, product_measurements_confidence, [config["product_dimensions"]["source"]], None,
        ),
        "description": (description["description_ru"], "AI_estimated", 0.95, ["description-ru.description_ru", "product-positioning"], None),
        "product_name": (config["merge_product_name"], "AI_estimated", 0.99, ["title-ru.title_ru"], None),
        "tags": (" ".join(tags["tags"]), "AI_estimated", 0.95, ["ozon-tags.json"], None),
        "rich_content": (rich["serialized_json"], "AI_estimated", 0.95, ["rich-content.json", "generated-images/stage3.4"], None),
    }
    role_aliases = {
        "product_weight": ("Вес товара, г", "Вес товара", "Вес, г"),
        "product_length": ("Длина, мм", "Длина товара, мм"),
        "product_width": ("Ширина, мм", "Ширина товара, мм"),
        "product_height": ("Высота, мм", "Высота товара, мм"),
        "package_weight": ("Вес товара с упаковкой", "Вес с упаковкой", "Вес с упаковкой, г", "Вес упаковки"),
        "package_dimensions": ("Размер упаковки", "Габариты упаковки"),
        "product_dimensions": ("Размеры, мм", "Размеры товара, мм"),
        "description": ("Аннотация", "Описание товара", "Описание"),
        "product_name": ("Название", "Название товара"),
        "tags": ("Хештеги", "#Хештеги", "Теги"),
        "rich_content": ("Rich-контент", "Rich-контент JSON", "Rich content"),
    }
    for aliases, value in (
        (("Длина, см", "Длина товара, см"), config["product_dimensions"]["length_mm"] / 10),
        (("Ширина, см", "Ширина товара, см"), config["product_dimensions"]["width_mm"] / 10),
        (("Высота, см", "Высота товара, см"), config["product_dimensions"]["height_mm"] / 10),
    ):
        attribute = _find_attribute_by_names(metadata, aliases)
        if attribute and int(attribute["attribute_id"]) not in supplied:
            supplied[int(attribute["attribute_id"])] = (
                value,
                product_measurements_source,
                product_measurements_confidence,
                [config["product_dimensions"]["source"]],
                None,
            )
    for role, aliases in role_aliases.items():
        if role == "rich_content" and not include_rich_content:
            continue
        if varying_sku_dimensions and role in {
            "product_length", "product_width", "product_height", "product_dimensions",
        }:
            continue
        attribute = _find_attribute_by_names(metadata, aliases)
        if attribute and int(attribute["attribute_id"]) not in supplied:
            supplied[int(attribute["attribute_id"])] = role_values[role]

    # Required dictionary fields are the only place where a missing value may
    # stop the batch.  Before reporting one as missing, search the actual
    # captured facts and the recorded analysis for an unambiguous live value.
    # This handles wording such as ``不锈钢``/``нержавейка`` without guessing
    # unsupported claims.  Optional fields remain ``unknown`` and are skipped.
    analysis = load_json(product_dir / "output/product-analysis.json") if (
        product_dir / "output/product-analysis.json"
    ).is_file() else {}
    source_evidence_text = " ".join([
        str(source.get("title_cn") or ""),
        json.dumps(source.get("product_attributes") or [], ensure_ascii=False),
        json.dumps(source.get("skus") or [], ensure_ascii=False),
        str(analysis.get("product_type") or ""),
        str(analysis.get("category") or ""),
        json.dumps(analysis.get("facts") or {}, ensure_ascii=False),
    ])
    for item in metadata.get("attributes") or []:
        attribute_id = int(item["attribute_id"])
        if not item.get("required") or attribute_id in supplied or not item.get("allowed_values"):
            continue
        selected = _dictionary_match_from_source_text(item, source_evidence_text)
        if not selected:
            continue
        supplied[attribute_id] = (
            selected[0],
            "1688",
            0.90,
            ["source.title_cn", "source.product_attributes", "source.skus", "product-analysis.facts"],
            selected[1],
        )
    if _product_family(product_dir) == "luggage_scale":
        scale_type = _find_attribute_by_names(metadata, ("Вид весов", "Тип весов"))
        electronic = next(
            (item for item in scale_type.get("allowed_values", []) if _normalized_attribute_name(item["value"]) in {"электронные", "электронныевесы"}),
            None,
        )
        if scale_type and electronic:
            supplied[int(scale_type["attribute_id"])] = (str(electronic["value"]), "AI_estimated", 0.99, ["source.title_cn contains 电子秤"], int(electronic["id"]))
    attributes = []
    required_ids = []
    for item in metadata["attributes"]:
        attribute_id = item["attribute_id"]
        if item["required"]:
            required_ids.append(attribute_id)
        if attribute_id in supplied:
            value, source_name, confidence, evidence, dictionary_id = supplied[attribute_id]
            attributes.append(_attribute(item, value, source_name, confidence, evidence, dictionary_id))
        else:
            attributes.append(_attribute(item, "unknown", "unknown", 0, ["No reliable source value available."]))
    filled_required = [
        item["attribute_id"] for item in attributes
        if item["required"] and item["value"] != "unknown"
    ]
    missing = [item for item in required_ids if item not in filled_required]
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "category_id": metadata["category_id"],
        "type_id": metadata["type_id"],
        "schema_source": "ozon_seller_api",
        "attributes": attributes,
        "required_summary": {
            "total": len(required_ids), "filled": len(filled_required),
            "missing": len(missing), "missing_attribute_ids": missing,
        },
        "warnings": [
            "Only source-backed or high-confidence low-risk optional values are filled; power, warranty, certification, marking, protection systems and other unsupported claims remain unknown.",
            "Product and package measurements may be labelled estimates; package values are always greater than product values.",
            "AI_estimated identifies a derived marketplace value and never changes the 1688 source facts.",
        ],
    }


def build_attribute_coverage_report(attrs: Dict[str, Any]) -> Dict[str, Any]:
    filled = [item for item in attrs["attributes"] if item["value"] != "unknown"]
    omitted = [item for item in attrs["attributes"] if item["value"] == "unknown"]
    return {
        "schema_version": "1.0.0",
        "product_id": attrs["product_id"],
        "category_id": attrs["category_id"],
        "type_id": attrs["type_id"],
        "generated_at": now(),
        "total_attribute_count": len(attrs["attributes"]),
        "filled_attribute_count": len(filled),
        "omitted_unknown_count": len(omitted),
        "required_attribute_count": attrs["required_summary"]["total"],
        "required_filled_count": attrs["required_summary"]["filled"],
        "filled_attributes": [{
            "attribute_id": item["attribute_id"],
            "attribute_name": item["attribute_name"],
            "value": item["value"],
            "source": item["source"],
            "confidence": item["confidence"],
            "evidence": item["evidence"],
        } for item in filled],
        "omitted_attributes": [{
            "attribute_id": item["attribute_id"],
            "attribute_name": item["attribute_name"],
            "required": item["required"],
            "reason": "No reliable value exists in the 1688 source, selected SKU facts, or verified analysis; the attribute is omitted from the Ozon request.",
        } for item in omitted],
    }


def validate_package(package: Dict[str, Any]) -> Dict[str, List[str]]:
    return {name: validate_value(value, SCHEMAS[name]) for name, value in package.items() if name in SCHEMAS}


def _find_attribute(metadata: Dict[str, Any], attribute_id: int) -> Dict[str, Any]:
    return next((item for item in metadata["attributes"] if item["attribute_id"] == attribute_id), {})


def _normalized_attribute_name(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]", "", str(value or "").casefold())


def _find_attribute_by_names(metadata: Dict[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    expected = {_normalized_attribute_name(name) for name in names}
    return next(
        (
            item for item in metadata.get("attributes", [])
            if _normalized_attribute_name(item.get("attribute_name")) in expected
        ),
        {},
    )


def _find_brand_attribute(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Find the live Ozon brand attribute even when the category renames it.

    Some apparel/accessory categories expose brand as names such as
    ``Бренд в одежде и обуви`` rather than plain ``Бренд``.  Treating only the
    exact name as reliable made otherwise valid categories stop before field
    compilation.
    """
    candidates = []
    for item in metadata.get("attributes", []) or []:
        name = _normalized_attribute_name(item.get("attribute_name"))
        try:
            attribute_id = int(item.get("attribute_id") or 0)
        except (TypeError, ValueError):
            attribute_id = 0
        if attribute_id == 85 or "бренд" in name or "brand" in name:
            candidates.append(item)
    if not candidates:
        return {}
    return min(
        candidates,
        key=lambda item: (
            0 if (item.get("required") is True or item.get("is_required") is True) else 1,
            0 if _allowed_value_by_text(item, "Нет бренда") else 1,
            len(str(item.get("attribute_name") or "")),
            int(item.get("attribute_id") or 0),
        ),
    )


def _allowed_value_by_text(attribute: Dict[str, Any], text: str) -> Dict[str, Any] | None:
    expected = _normalized_attribute_name(text)
    return next(
        (
            item for item in attribute.get("allowed_values", []) or []
            if _normalized_attribute_name(item.get("value")) == expected
        ),
        None,
    )


def _find_model_attribute(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Prefer a required Ozon model field, then the most specific known alias."""
    priority = {
        _normalized_attribute_name(name): index
        for index, name in enumerate(MODEL_ATTRIBUTE_NAMES)
    }
    candidates = [
        item for item in metadata.get("attributes", [])
        if _normalized_attribute_name(item.get("attribute_name")) in priority
    ]
    if not candidates:
        return {}
    return min(
        candidates,
        key=lambda item: (
            0 if (item.get("required") is True or item.get("is_required") is True) else 1,
            priority[_normalized_attribute_name(item.get("attribute_name"))],
            int(item.get("attribute_id") or 0),
        ),
    )


def _stable_random_model_name(product_dir: Path, source: Dict[str, Any]) -> str:
    """Return a random-looking but repeatable 12-digit model for one product."""
    identity = {
        "strategy": MODEL_NAME_STRATEGY,
        "product_id": str(source.get("product_id") or product_dir.name),
        "collection_id": str(source.get("collection_id") or "unknown"),
        "source_product_id": str(
            source.get("source_product_id") or source.get("offer_id") or "unknown"
        ),
        "source_url": str(
            source.get("canonical_source_url") or source.get("source_url") or "unknown"
        ),
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    number = 100_000_000_000 + int.from_bytes(digest[:8], "big") % 900_000_000_000
    return str(number)


def _resolve_product_model_name(
    product_dir: Path,
    source: Dict[str, Any],
    existing_model_name: Any = None,
    existing_model_source: Any = None,
) -> Tuple[str, str]:
    _ = (existing_model_name, existing_model_source)
    return _stable_random_model_name(product_dir, source), MODEL_NAME_STRATEGY


def _find_type_attribute(metadata: Dict[str, Any], type_id: int) -> Dict[str, Any]:
    matches = [
        item for item in metadata.get("attributes", [])
        if any(int(value.get("id") or 0) == int(type_id) for value in item.get("allowed_values", []))
    ]
    if len(matches) == 1:
        return matches[0]
    return _find_attribute_by_names(metadata, ("Тип", "Тип товара"))


def _official_aspect_ids(metadata: Dict[str, Any]) -> set[int]:
    path = (
        ROOT / "ozon-adapter/metadata/live-aspect-rules"
        / f"category-{metadata['category_id']}-type-{metadata['type_id']}.json"
    )
    if not path.is_file():
        return set()
    raw = (load_json(path).get("raw_response") or {}).get("result") or []
    return {
        int(item.get("id", item.get("attribute_id")))
        for item in raw
        if item.get("is_aspect") is True and item.get("id", item.get("attribute_id")) is not None
    }


def _auto_upload_config(
    product_dir: Path,
    metadata: Dict[str, Any],
    *,
    allow_unpriced: bool = False,
    existing_model_name: Any = None,
    existing_model_source: Any = None,
) -> Dict[str, Any]:
    output = product_dir / "output"
    source = load_json(product_dir / "input/source.json")
    pricing = load_json(output / "pricing-result.json")
    cost = load_json(output / "cost-analysis.json")
    category = load_json(output / "ozon-category.json")
    shops = load_json(ROOT / "ozon-adapter/shops.json")
    shop_name = shops.get("default_read_shop") or shops["shops"][0]["name"]
    shop = next(item for item in shops["shops"] if item["name"] == shop_name)
    title = load_json(output / "title-ru.json")
    price_by_sku = {str(item["sku_id"]): item for item in pricing["sku_pricing"]}

    def configured_price(sku_id: str) -> str:
        raw = (price_by_sku.get(sku_id) or {}).get("selling_price_cny")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        if value <= 0 and not allow_unpriced:
            raise ValueError(f"SKU {sku_id} has no positive selling price")
        return f"{value:.2f}"
    product_dimensions = cost.get("product_dimensions") or cost.get("dimensions") or {}
    product_weight = cost.get("product_weight") or cost.get("weight") or {}
    package_dimensions = cost.get("package_dimensions") or cost.get("dimensions") or {}
    package_weight = cost.get("package_weight") or cost.get("weight") or {}
    type_meta = _find_type_attribute(metadata, int(category["type_id"]))
    type_values = type_meta.get("allowed_values") or []
    type_value = next((item for item in type_values if int(item["id"]) == int(category["type_id"])), None)
    if not type_value:
        raise ValueError(f"Ozon type_id {category['type_id']} is absent from the live product-type attribute")
    brand_meta = _find_brand_attribute(metadata)
    model_meta = _find_model_attribute(metadata)
    if not brand_meta:
        raise ValueError("Live Ozon metadata does not expose a reliable brand attribute")
    unbranded = _allowed_value_by_text(brand_meta, str(shop.get("default_unbranded_value") or "Нет бренда"))
    if not unbranded:
        unbranded = _allowed_value_by_text(brand_meta, "Нет бренда")
    if not unbranded:
        raise ValueError("Live Ozon brand dictionary does not expose Нет бренда")
    aspect_ids = _official_aspect_ids(metadata)
    color_meta = _find_attribute_by_names(metadata, ("Цвет товара", "Цвет"))
    if color_meta and color_meta["attribute_id"] not in aspect_ids:
        color_meta = {}
    allowed_colors = {str(item["value"]).casefold(): item for item in color_meta.get("allowed_values") or []}
    sku_colors = []
    unresolved_colors = []
    for sku in source["skus"]:
        source_data = sku.get("source_data") or {}
        source_color_name = str(source_data.get("sku_image_prop_name") or "").casefold()
        color_text = str(source_data.get("sku_image_prop_value") or sku["sku_name"])
        color = _color_from_sku_name(color_text)
        # ``规格1`` is a seller UI label; the structured source property tells
        # us that this option is actually colour and must be kept per SKU.
        if source_color_name not in {"颜色", "色号", "colour", "color"}:
            option_color = next(
                (str(option.get("value_cn") or "") for option in sku.get("option_values") or []
                 if any(token in str(option.get("name_cn") or "").casefold() for token in ("颜色", "色号", "colour", "color"))),
                "",
            )
            if option_color:
                color = _color_from_sku_name(option_color)
        if color == "unknown" or not color_meta:
            if color != "unknown":
                unresolved_colors.append({"source_sku_id": str(sku["sku_id"]), "value": color})
            continue
        dictionary = allowed_colors.get(color.casefold())
        if not dictionary:
            unresolved_colors.append({"source_sku_id": str(sku["sku_id"]), "value": color})
            continue
        sku_colors.append({
            "source_sku_id": str(sku["sku_id"]),
            "attribute_id": int(color_meta["attribute_id"]),
            "dictionary_value_id": int(dictionary["id"]),
            "value": str(dictionary["value"]),
        })
    preserved_model_name, model_name_source = _resolve_product_model_name(
        product_dir,
        source,
        existing_model_name,
        existing_model_source,
    )
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "shop_name": shop_name,
        "currency_code": shop.get("default_currency_code", "CNY"),
        "sku_prices": [
            {
                "source_sku_id": str(sku["sku_id"]),
                "price": configured_price(str(sku["sku_id"])),
            }
            for sku in source["skus"]
        ],
        "brand": {
            "attribute_id": int(brand_meta["attribute_id"]),
            "dictionary_value_id": int(unbranded.get("dictionary_value_id", unbranded.get("id"))),
            "value": str(unbranded.get("value") or "Нет бренда"),
            "source": "shop_default_unbranded",
        },
        "model_name": {
            "attribute_id": int(model_meta.get("attribute_id") or 0),
            "value": preserved_model_name,
            "source": model_name_source if model_meta else "stable_random_numeric_local_grouping_only",
        },
        "merge_product_name": str(title["title_ru"]),
        "type": {
            "attribute_id": int(type_meta["attribute_id"]),
            "dictionary_value_id": int(type_value["id"]),
            "value": str(type_value["value"]),
            "source": "ozon_seller_api",
        },
        "sku_colors": sku_colors,
        "product_dimensions": {
            "length_mm": int(math.ceil(float(product_dimensions["length"]) * 10)),
            "width_mm": int(math.ceil(float(product_dimensions["width"]) * 10)),
            "height_mm": int(math.ceil(float(product_dimensions["height"]) * 10)),
            "source": str(product_dimensions["source_ref"]),
            "source_status": "estimated_human_approved" if product_dimensions.get("profile") == "manual_confirmation" else "estimated_system" if product_dimensions.get("estimated") else "confirmed_source",
        },
        "product_weight": {
            "value_g": int(math.ceil(float(product_weight["value"]))),
            "source": str(product_weight["source_ref"]),
            "source_status": "estimated_human_approved" if product_weight.get("profile") == "manual_confirmation" else "estimated_system" if product_weight.get("estimated") else "confirmed_source",
        },
        "package_dimensions": {
            "length_mm": int(math.ceil(float(package_dimensions["length"]) * 10)),
            "width_mm": int(math.ceil(float(package_dimensions["width"]) * 10)),
            "height_mm": int(math.ceil(float(package_dimensions["height"]) * 10)),
            "source": str(package_dimensions["source_ref"]),
            "source_status": "estimated_human_approved" if package_dimensions.get("profile") == "manual_confirmation" else "estimated_system" if package_dimensions.get("estimated") else "confirmed_source",
        },
        "package_weight": {
            "value_g": int(math.ceil(float(package_weight["value"]))),
            "source": str(package_weight["source_ref"]),
            "source_status": "estimated_human_approved" if package_weight.get("profile") == "manual_confirmation" else "estimated_system" if package_weight.get("estimated") else "confirmed_source",
        },
        "vat": str(shop.get("default_vat") or "0"),
        "stock_mode": "not_set",
        "old_price": None,
        "configured_at": now(),
        "configured_by": "automatic_pipeline_from_verified_sources",
    }


def _sync_upload_config_model_from_compiled_attributes(
    config: Dict[str, Any], attributes: Dict[str, Any],
) -> None:
    """Make the legacy upload config follow the compiled Ozon merge key."""
    model = config.get("model_name") or {}
    model_attribute_id = int(model.get("attribute_id") or 0)
    if model_attribute_id <= 0:
        return
    for attribute in attributes.get("common_attributes") or attributes.get("attributes") or []:
        if int(attribute.get("attribute_id") or attribute.get("id") or 0) != model_attribute_id:
            continue
        value = str(
            attribute.get("target_value")
            or attribute.get("value")
            or attribute.get("canonical_value")
            or ""
        ).strip()
        if value and value.casefold() not in {"unknown", "none", "null"}:
            model["value"] = value
            model["source"] = "ozon_attributes_final_compiled_model_name"
            config["model_name"] = model
        return


def build_package(
    product_dir: Path, write: bool = True, *, pre_image: bool = False,
) -> Dict[str, Any]:
    product_dir = product_dir.resolve()
    output = product_dir / "output"
    source = load_json(product_dir / "input/source.json")
    workbench_path = output / "workbench-draft.json"
    workbench = load_json(workbench_path) if workbench_path.is_file() else {}
    if workbench:
        pricing_path = output / "pricing-result.json"
        if pricing_path.is_file() and isinstance(workbench.get("sku_overrides"), dict):
            pricing_data = load_json(pricing_path)
            overrides = workbench.get("sku_overrides") or {}
            for price_item in pricing_data.get("sku_pricing") or []:
                override = overrides.get(str(price_item.get("sku_id"))) or {}
                for field in ("selling_price_cny", "selling_price_rub"):
                    if field in override:
                        price_item[field] = override[field]
                if "selling_price_cny" in override:
                    price_item["price_source"] = "workbench_human_override"
            if write:
                write_json_atomic(pricing_path, pricing_data)
        title_path = output / "title-ru.json"
        description_path = output / "description-ru.json"
        copy_path = output / "copy-ru.json"
        title_data = load_json(title_path)
        description_data = load_json(description_path)
        copy_data = load_json(copy_path) if copy_path.is_file() else {}
        if workbench.get("title_ru"):
            title_data["title_ru"] = str(workbench["title_ru"]).strip()
            copy_data["title_ru"] = title_data["title_ru"]
        if workbench.get("short_title"):
            copy_data["short_title"] = str(workbench["short_title"]).strip()
        if workbench.get("description_ru"):
            description_data["description_ru"] = str(workbench["description_ru"]).strip()
            copy_data["description_ru"] = description_data["description_ru"]
            copy_data["description"] = description_data["description_ru"]
        if isinstance(workbench.get("bullets_ru"), list):
            bullets = [{"text_ru": str(value), "evidence": ["workbench human override"]} for value in workbench["bullets_ru"] if str(value).strip()]
            copy_data["bullets_ru"] = bullets
            copy_data["selling_points"] = bullets
        if write:
            write_json_atomic(title_path, title_data)
            write_json_atomic(description_path, description_data)
            write_json_atomic(copy_path, copy_data)
    metadata = load_json(output / "ozon-category-attributes.json")
    category = load_json(output / "ozon-category.json")
    draft_path = output / "ozon-draft.json"
    if draft_path.is_file():
        draft = load_json(draft_path)
        canonical_title = load_json(output / "title-ru.json").get("title_ru")
        canonical_description = load_json(output / "description-ru.json").get("description_ru")
        if canonical_title:
            draft["title"] = str(canonical_title).strip()
        if canonical_description:
            draft["description"] = str(canonical_description).strip()
        draft["description_category_id"] = int(category["category_id"])
        draft["type_id"] = int(category["type_id"])
        draft["category"] = {
            "category_id": int(category["category_id"]),
            "category_name": str(category["category_name"]),
            "confidence": float(category.get("confidence") or 0),
            "match_status": str(category.get("match_status") or "api_confirmed"),
            "metadata_source": "ozon_seller_api",
        }
        if not pre_image:
            draft = sync_draft_images_from_image_plan(product_dir, draft, write=False)
        if write:
            write_json_atomic(draft_path, draft)
    config_path = output / "ozon-upload-config.json"
    if config_path.is_file():
        config = load_json(config_path)
        existing_model = config.get("model_name") or {}
        refreshed = _auto_upload_config(
            product_dir,
            metadata,
            allow_unpriced=pre_image,
            existing_model_name=existing_model.get("value"),
            existing_model_source=existing_model.get("source"),
        )
        # Category remapping can change attribute IDs, dictionaries and the
        # product type. Keep operator/business settings, but never reuse those
        # category-bound fields from an older match.
        for key in (
            "brand", "model_name", "merge_product_name", "type", "sku_colors",
            "product_weight", "product_dimensions", "package_weight", "package_dimensions",
        ):
            config[key] = refreshed[key]
        if write:
            write_json_atomic(config_path, config)
    else:
        config = _auto_upload_config(product_dir, metadata, allow_unpriced=pre_image)
        config_errors = (
            [] if pre_image
            else validate_value(config, TEMPLATES / "ozon-upload-config.schema.json")
        )
        if config_errors:
            raise ValueError("Automatic upload config validation failed: " + "; ".join(config_errors))
        if write:
            write_json_atomic(config_path, config)
    description = load_json(output / "description-ru.json")
    result_path = output / "ozon-result.json"
    result = load_json(result_path) if result_path.is_file() else {}
    tags = build_tags(product_dir)
    colors = build_color_variants(product_dir, source)
    color_policy = build_color_variant_policy(product_dir.name, source, colors)
    build_attribute_fill_input(product_dir)
    attrs = compile_product_attributes(product_dir)
    _sync_upload_config_model_from_compiled_attributes(config, attrs)
    # Field completion is the single draft outlet.  A new product legitimately
    # has no ozon-draft.json yet; create the deterministic base draft before
    # Rich Content tries to synchronize its image slots.
    if not draft_path.is_file() and write:
        import sys
        scripts_dir = ROOT / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from ozon_ecommerce_designer_contract import build_current_ozon_draft
        build_current_ozon_draft(product_dir)
    if write:
        sync_draft_attributes_from_final_attributes(product_dir, attrs, write=True)
    rich = (
        {"serialized_json": "unknown"}
        if pre_image else build_rich_content(product_dir, result)
    )
    if not pre_image:
        rich_meta = _find_attribute_by_names(metadata, ("Rich-контент", "Rich-контент JSON", "Rich content"))
        if not rich_meta:
            rich["warnings"].append("The selected category has no live Rich Content attribute; it will not be sent.")
        rich["attribute_id"] = int(rich_meta["attribute_id"]) if rich_meta else 0
    coverage = build_attribute_coverage_report(attrs)
    package = {
        "ozon-tags.json": tags,
        "ozon-attributes-final.json": attrs,
        "color-variants.json": colors,
        "color-variant-policy.json": color_policy,
        "attribute-coverage-report.json": coverage,
    }
    if not pre_image:
        package["rich-content.json"] = rich
    validation = validate_package(package)
    failures = {name: errors for name, errors in validation.items() if errors}
    if failures:
        raise ValueError("Field-completion schema validation failed: " + json.dumps(failures, ensure_ascii=False))
    if write:
        write_json_atomic(config_path, config)
        for name, value in package.items():
            write_json_atomic(output / name, value)
        if workbench:
            snapshot = {
                "schema_version": "1.0.0", "product_id": product_dir.name,
                "workbench_version": int(workbench.get("version") or 0),
                "locked_fields": workbench.get("locked_fields") or [],
                "created_at": now(),
                "title_ru": load_json(output / "title-ru.json").get("title_ru"),
                "description_ru": load_json(output / "description-ru.json").get("description_ru"),
                "tags": tags["tags"], "attributes": workbench.get("attributes") or {},
            }
            write_json_atomic(output / "workbench-final-snapshot.json", snapshot)
            workbench["dirty"] = False
            workbench["materialized_at"] = snapshot["created_at"]
            write_json_atomic(workbench_path, workbench)
    return package
