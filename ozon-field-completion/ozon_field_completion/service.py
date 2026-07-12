"""Generate traceable Ozon tags, attributes, rich content, and color variants."""

from __future__ import annotations

import json
import re
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
    "final-upload-check.json": TEMPLATES / "final-upload-check.schema.json",
}

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
    analysis = load_json(product_dir / "output/product-analysis.json")
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


def _keyword_hashtags(product_dir: Path) -> List[str]:
    values: List[str] = []
    for name in ("keywords-ru.json", "copy-ru.json"):
        path = product_dir / "output" / name
        if not path.is_file():
            continue
        data = load_json(path)
        for key in ("primary_keywords", "secondary_keywords", "keywords", "keywords_ru", "usage_scenarios"):
            raw = data.get(key) or []
            if isinstance(raw, list):
                values.extend(str(item) for item in raw)
    tags = []
    for value in values:
        normalized = "#" + re.sub(r"[^А-Яа-яЁё0-9]", "", value)
        if 1 < len(normalized) <= 30 and normalized not in tags:
            tags.append(normalized)
    return tags


def _derived_truthful_hashtags(product_dir: Path) -> List[str]:
    """Derive extra tags only from already approved Russian copy tokens."""
    phrases: List[str] = []
    for name in ("copy-ru.json", "marketplace-content-input.json"):
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
        words = re.findall(r"[А-Яа-яЁё0-9]+", phrase)
        sequences = [words]
        sequences.extend([words[index:index + 2] for index in range(max(0, len(words) - 1))])
        sequences.extend([[word] for word in words if word.casefold() not in stopwords])
        for sequence in sequences:
            normalized = "#" + "".join(sequence)
            if 1 < len(normalized) <= 30 and normalized not in result:
                result.append(normalized)
    return result


def build_tags(product_dir: Path) -> Dict[str, Any]:
    product_id = product_dir.name
    workbench_path = product_dir / "output/workbench-draft.json"
    workbench = load_json(workbench_path) if workbench_path.is_file() else {}
    manual_tags = workbench.get("tags")
    if manual_tags is not None:
        if not isinstance(manual_tags, list) or len(manual_tags) != 30:
            raise ValueError("Workbench tags must contain exactly 30 entries before upload")
        tags = []
        for value in manual_tags:
            tag = str(value).strip()
            if not re.fullmatch(r"#[А-Яа-яЁё0-9_]+", tag) or len(tag) > 30:
                raise ValueError(f"Invalid workbench Ozon hashtag: {tag}")
            if tag in tags:
                raise ValueError(f"Duplicate workbench Ozon hashtag: {tag}")
            tags.append(tag)
        return {
            "schema_version": "1.0.0", "product_id": product_id,
            "tags": tags, "count": 30, "language": "ru",
            "source_refs": [f"products/{product_id}/output/workbench-draft.json"],
            "warnings": ["Hashtags were manually edited and locked in the workbench."],
        }
    family = _product_family(product_dir)
    bank = {
        "luggage_scale": LUGGAGE_SCALE_TAGS,
        "knife_sharpener": KNIFE_SHARPENER_TAGS,
        "pet_leash": PET_LEASH_TAGS,
        "storage_bag": STORAGE_BAG_TAGS,
        "drain_cover": DRAIN_COVER_TAGS,
    }.get(family, [])
    tags = list(dict.fromkeys(
        _keyword_hashtags(product_dir) + list(bank) + _derived_truthful_hashtags(product_dir)
    ))[:30]
    if len(tags) != 30:
        raise ValueError(f"Cannot generate 30 truthful Russian tags for product family {family}")
    return {
        "schema_version": "1.0.0",
        "product_id": product_id,
        "tags": tags,
        "count": len(tags),
        "language": "ru",
        "source_refs": [
            f"products/{product_id}/input/source.json",
            f"products/{product_id}/output/product-analysis.json",
            f"products/{product_id}/output/product-positioning.json",
            f"products/{product_id}/output/keywords-ru.json",
        ],
        "warnings": [
            "Tags describe only the confirmed product type, usage scenes, and purchase motivation; brand and unconfirmed parameters are excluded."
        ],
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
    draft = load_json(product_dir / "output/ozon-draft.json")
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
    if any(token in name for token in ("浅绿", "淡绿", " светло-зелен")):
        return "светло-зеленый"
    if any(token in name for token in ("深绿", "墨绿", " темно-зелен")):
        return "темно-зеленый"
    if any(token in name for token in ("浅蓝", "淡蓝", " светло-син")):
        return "светло-синий"
    if any(token in name for token in ("深蓝", "藏青", " темно-син")):
        return "темно-синий"
    if "银色" in name:
        return "серебристый"
    if "黑色" in name:
        return "черный"
    if any(token in name for token in ("绿色", " зел")):
        return "зеленый"
    if "卡其色" in name:
        return "хаки"
    if "白色" in name:
        return "белый"
    if "灰色" in name:
        return "серый"
    if "红色" in name:
        return "красный"
    if "粉色" in name or "粉红" in name:
        return "розовый"
    if "蓝色" in name:
        return "синий"
    if "黄色" in name:
        return "желтый"
    if "橙色" in name or "橙黄" in name:
        return "оранжевый"
    if "透明" in name:
        return "прозрачный"
    if "米色" in name or "米白" in name:
        return "бежевый"
    if "棕色" in name or "咖啡色" in name:
        return "коричневый"
    return "unknown"


def _dictionary_value(metadata: Dict[str, Any], value: str) -> Tuple[str, int] | None:
    normalized = _normalized_attribute_name(value)
    for item in metadata.get("allowed_values") or []:
        if _normalized_attribute_name(item.get("value")) == normalized:
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


def _explicit_dimensions_mm(source: Dict[str, Any]) -> Tuple[float, float, float | None] | None:
    values = []
    direct = _source_attribute_value(source, ("尺寸", "产品尺寸", "规格尺寸", "长宽高"))
    if direct:
        values.append(direct)
    for sku in source.get("skus") or []:
        values.append(str(sku.get("sku_name") or ""))
        values.extend(str(item.get("value_cn") or "") for item in sku.get("option_values") or [])
    pattern = re.compile(
        r"(?P<a>\d+(?:\.\d+)?)\s*[x×*]\s*(?P<b>\d+(?:\.\d+)?)"
        r"(?:\s*[x×*]\s*(?P<c>\d+(?:\.\d+)?))?\s*(?P<unit>mm|毫米|cm|厘米)",
        re.IGNORECASE,
    )
    matches = [match for value in values if (match := pattern.search(value))]
    if not matches:
        return None
    converted = []
    for match in matches:
        factor = 10.0 if match.group("unit").casefold() in {"cm", "厘米"} else 1.0
        converted.append((
            float(match.group("a")) * factor,
            float(match.group("b")) * factor,
            float(match.group("c")) * factor if match.group("c") else None,
        ))
    return converted[0] if all(item == converted[0] for item in converted) else None


def _explicit_weight_g(source: Dict[str, Any]) -> float | None:
    raw = _source_attribute_value(source, ("重量", "产品重量", "单品重量", "净重"))
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克)", raw, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return value * 1000 if match.group(2).casefold() in {"kg", "千克", "公斤"} else value


def _reliable_dynamic_attributes(
    product_dir: Path, metadata: Dict[str, Any]
) -> Dict[int, Tuple[Any, str, float, List[str], int | None]]:
    source = load_json(product_dir / "input/source.json")
    analysis_path = product_dir / "output/product-analysis.json"
    analysis = load_json(analysis_path) if analysis_path.is_file() else {}
    title = str(source.get("title_cn") or "")
    result: Dict[int, Tuple[Any, str, float, List[str], int | None]] = {}

    material_terms = (
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
    material_text = f"{title} {material_raw} {json.dumps(analysis_materials, ensure_ascii=False)}".casefold()
    material_meta = _find_attribute_by_names(metadata, ("Материал", "Материал изделия"))
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
    if len(colors) == 1:
        color = next(iter(colors))
        color_meta = _find_attribute_by_names(metadata, ("Цвет товара", "Цвет"))
        selected = _dictionary_value(color_meta, color) if color_meta else None
        if selected:
            evidence = ["source.skus[].sku_name"]
            result[int(color_meta["attribute_id"])] = (
                selected[0], "1688", 1.0, evidence, selected[1]
            )
            color_name_meta = _find_attribute_by_names(metadata, ("Название цвета",))
            if color_name_meta:
                result[int(color_name_meta["attribute_id"])] = (
                    selected[0], "1688", 1.0, evidence, None
                )

    dimensions = _explicit_dimensions_mm(source)
    if dimensions:
        for aliases, value in (
            (("Длина, мм", "Длина"), dimensions[0]),
            (("Ширина, мм", "Ширина"), dimensions[1]),
            (("Высота, мм", "Высота"), dimensions[2]),
        ):
            attribute = _find_attribute_by_names(metadata, aliases)
            if attribute and value is not None:
                result[int(attribute["attribute_id"])] = (
                    value, "1688", 1.0, ["source product/SKU dimensions"], None
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
    verified_fallbacks = {
        "3993658310173": {
            "image": "products/P000004/input/main-images/main-001.webp",
            "confidence": 0.92,
            "reason": "Visual analysis confirms the silver body and illuminated green display in the real main image; the image also contains the black variant and is not represented as an exclusive SKU photo.",
        },
        "3993658310175": {
            "image": "products/P000004/input/main-images/main-005.webp",
            "confidence": 0.96,
            "reason": "Visual analysis confirms a silver body with the display not illuminated in the real main image.",
        },
        "3993658310174": {
            "image": "products/P000004/input/main-images/main-002.webp",
            "confidence": 0.99,
            "reason": "Visual analysis confirms the black body and illuminated green display in the real main image.",
        },
    }
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
        and item.get("variant_kind") in {"color", "mixed_supported"}
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
        image = str(sku.get("variant_local_image_path") or sku.get("local_image_path") or "unknown")
        if sku_id in generated_variant_mains:
            generated = generated_variant_mains[sku_id]
            image = str(generated["output_path"])
            status = "mapped"
            image_source = "generated_from_real_sku_reference"
            resolution_level = 3
            confidence = 1.0
            reason = "This SKU-specific generated main uses the exact real SKU reference and passed the common image QC gate."
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
        elif product_dir.name == "P000004" and sku_id in verified_fallbacks:
            match = verified_fallbacks[sku_id]
            image = match["image"]
            status, image_source = "mapped", "main_image_match"
            resolution_level, confidence = 2, match["confidence"]
            reason = match["reason"]
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
) -> Dict[str, Any]:
    product_id = product_dir.name
    package_measurements_source = (
        "1688"
        if config["package_weight"]["source_status"] == "confirmed_source"
        and config["package_dimensions"]["source_status"] == "confirmed_source"
        else "AI_estimated"
    )
    package_measurements_confidence = 1.0 if package_measurements_source == "1688" else 0.68
    product_measurements_source = (
        "1688"
        if config["product_weight"]["source_status"] == "confirmed_source"
        and config["product_dimensions"]["source_status"] == "confirmed_source"
        else "AI_estimated"
    )
    product_measurements_confidence = 1.0 if product_measurements_source == "1688" else 0.68
    supplied: Dict[int, Tuple[Any, str, float, List[str], int | None]] = {
        int(config["model_name"]["attribute_id"]): (config["model_name"]["value"], "AI_estimated", 0.95, ["ozon-upload-config.model_name", "product-analysis.product_type"], None),
        int(config["brand"]["attribute_id"]): (config["brand"]["value"], "AI_estimated", 0.90, ["store policy: products without a confirmed brand use Нет бренда"], config["brand"]["dictionary_value_id"]),
        int(config["type"]["attribute_id"]): (config["type"]["value"], "AI_estimated", 0.99, ["product-analysis.product_type", "ozon-category.category_name"], config["type"]["dictionary_value_id"]),
    }
    supplied.update(_reliable_dynamic_attributes(product_dir, metadata))
    supplied.update(_safe_optional_attributes(product_dir, metadata))
    workbench_path = product_dir / "output/workbench-draft.json"
    workbench = load_json(workbench_path) if workbench_path.is_file() else {}
    manual_attributes = workbench.get("attributes") or {}
    for item in metadata.get("attributes") or []:
        attribute_id = int(item["attribute_id"])
        raw_value = manual_attributes.get(str(attribute_id), manual_attributes.get(attribute_id))
        if raw_value in {None, "", "unknown"}:
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
        "package_dimensions": ("Размер упаковки", "Габариты упаковки", "Размеры, мм"),
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
        attribute = _find_attribute_by_names(metadata, aliases)
        if attribute and int(attribute["attribute_id"]) not in supplied:
            supplied[int(attribute["attribute_id"])] = role_values[role]
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
            "Unconfirmed material, capacity, precision, certification, battery, functions, load capacity, and accessories remain unknown.",
            "Product and package measurements may be labelled estimates; package values are always greater than product values.",
            "AI_estimated identifies a derived marketplace value and never changes the 1688 source facts.",
        ],
    }


def build_final_check(
    product_dir: Path,
    tags: Dict[str, Any],
    attrs: Dict[str, Any],
    rich: Dict[str, Any],
    colors: Dict[str, Any],
    color_policy: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    checks = []
    errors = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            errors.append(detail)

    add("tags", tags["count"] == 30 and len(set(tags["tags"])) == 30, "Exactly 30 unique Russian hashtags are required.")
    add("required_attributes", attrs["required_summary"]["missing"] == 0, "All required live Ozon category attributes must have a traceable value.")
    add(
        "rich_content",
        rich["status"] in {"ready", "ready_for_upload"},
        "Rich Content must be valid and use either persistent HTTPS images or local assets resolvable during production upload.",
    )
    add(
        "color_variant_images",
        color_policy["status"] != "BLOCK",
        "The main SKU must have a safe color image; missing non-core variants are warnings.",
    )
    output = product_dir / "output"
    draft = load_json(output / "ozon-draft.json")
    pricing_path = output / "pricing-result.json"
    pricing = load_json(pricing_path) if pricing_path.is_file() else {}
    priced_skus = {
        str(item.get("sku_id")) for item in pricing.get("sku_pricing", [])
        if float(item.get("selling_price_cny") or 0) > 0
    }
    selected_skus = {str(item["source_sku_id"]) for item in draft["skus"]}
    add(
        "pricing",
        bool(pricing) and selected_skus == priced_skus,
        "Every selected SKU requires a positive price in pricing-result.json.",
    )
    images_ok = bool(draft.get("images")) and all(
        item.get("qc_status") == "pass" and (ROOT / item["path"]).is_file()
        for item in draft.get("images", [])
    )
    add("images_qc", images_ok, "Every planned upload image must exist and pass image QC.")
    product_weight = config.get("product_weight") or {}
    package_weight = config.get("package_weight") or {}
    product_dimensions = config.get("product_dimensions") or {}
    package_dimensions = config.get("package_dimensions") or {}
    hierarchy_ok = (
        float(package_weight.get("value_g") or 0) > float(product_weight.get("value_g") or 0) > 0
        and all(
            float(package_dimensions.get(key) or 0) > float(product_dimensions.get(key) or 0) > 0
            for key in ("length_mm", "width_mm", "height_mm")
        )
    )
    add(
        "measurement_hierarchy",
        hierarchy_ok,
        "Package weight and every package dimension must be strictly greater than product measurements.",
    )
    category = draft.get("category", {})
    add(
        "category",
        category.get("metadata_source") == "ozon_seller_api"
        and isinstance(draft.get("description_category_id"), int)
        and isinstance(draft.get("type_id"), int)
        and category.get("match_status") == "api_confirmed"
        and float(category.get("confidence") or 0) >= 0.90,
        "A live, high-confidence Ozon category_id and type_id match is required.",
    )
    tree_path = output / "ozon-category-tree.json"
    category_pair_in_tree = False
    if tree_path.is_file():
        tree = load_json(tree_path)
        category_pair_in_tree = any(
            item.get("category_id") == draft.get("description_category_id")
            and item.get("type_id") == draft.get("type_id")
            and item.get("disabled") is not True
            for item in tree.get("categories") or []
        )
    add(
        "category_type_pair",
        category_pair_in_tree,
        "The selected category_id/type_id pair must be an enabled leaf in the same fetched Ozon category tree.",
    )
    add(
        "attribute_schema_identity",
        attrs.get("category_id") == draft.get("description_category_id")
        and attrs.get("type_id") == draft.get("type_id"),
        "Ozon attributes must belong to the selected category_id/type_id pair.",
    )
    upload_allowed = all(item["passed"] for item in checks)
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "checked_at": now(),
        "status": "PASS" if upload_allowed else "FAIL",
        "upload_allowed": upload_allowed,
        "checks": checks,
        "errors": errors,
        "warnings": ["This check performs no Ozon API write and does not modify the existing online product."],
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


def _auto_upload_config(product_dir: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
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
    product_dimensions = cost.get("product_dimensions", cost["dimensions"])
    product_weight = cost.get("product_weight", cost["weight"])
    package_dimensions = cost.get("package_dimensions", cost["dimensions"])
    package_weight = cost.get("package_weight", cost["weight"])
    type_meta = _find_type_attribute(metadata, int(category["type_id"]))
    type_values = type_meta.get("allowed_values") or []
    type_value = next((item for item in type_values if int(item["id"]) == int(category["type_id"])), None)
    if not type_value:
        raise ValueError(f"Ozon type_id {category['type_id']} is absent from the live product-type attribute")
    brand_meta = _find_attribute_by_names(metadata, ("Бренд",))
    model_meta = _find_attribute_by_names(metadata, (
        "Название модели", "Название модели (для объединения в одну карточку)", "Модель",
    ))
    if not brand_meta or not model_meta:
        raise ValueError("Live Ozon metadata does not expose a reliable brand or model attribute")
    aspect_ids = _official_aspect_ids(metadata)
    color_meta = _find_attribute_by_names(metadata, ("Цвет товара", "Цвет"))
    if color_meta and color_meta["attribute_id"] not in aspect_ids:
        color_meta = {}
    allowed_colors = {str(item["value"]).casefold(): item for item in color_meta.get("allowed_values") or []}
    sku_colors = []
    for sku in source["skus"]:
        color = _color_from_sku_name(str(sku["sku_name"]))
        if color == "unknown" or not color_meta:
            continue
        dictionary = allowed_colors.get(color.casefold())
        if not dictionary:
            raise ValueError(f"Color {color} is absent from live Ozon dictionary for SKU {sku['sku_id']}")
        sku_colors.append({
            "source_sku_id": str(sku["sku_id"]),
            "attribute_id": int(color_meta["attribute_id"]),
            "dictionary_value_id": int(dictionary["id"]),
            "value": str(dictionary["value"]),
        })
    return {
        "schema_version": "1.0.0",
        "product_id": product_dir.name,
        "shop_name": shop_name,
        "currency_code": shop.get("default_currency_code", "CNY"),
        "sku_prices": [
            {
                "source_sku_id": str(sku["sku_id"]),
                "price": f"{float(price_by_sku[str(sku['sku_id'])]['selling_price_cny']):.2f}",
            }
            for sku in source["skus"]
        ],
        "brand": {
            "attribute_id": int(brand_meta["attribute_id"]),
            "dictionary_value_id": int(shop["default_unbranded_dictionary_value_id"]),
            "value": str(shop.get("default_unbranded_value") or "Нет бренда"),
            "source": "shop_default_unbranded",
        },
        "model_name": {
            "attribute_id": int(model_meta["attribute_id"]),
            "value": f"{str(title['title_ru']).split(',')[0]} {product_dir.name}",
            "source": "stable_internal_product_group",
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
            "length_mm": float(product_dimensions["length"]) * 10,
            "width_mm": float(product_dimensions["width"]) * 10,
            "height_mm": float(product_dimensions["height"]) * 10,
            "source": str(product_dimensions["source_ref"]),
            "source_status": "estimated_system" if product_dimensions.get("estimated") else "confirmed_source",
        },
        "product_weight": {
            "value_g": float(product_weight["value"]),
            "source": str(product_weight["source_ref"]),
            "source_status": "estimated_system" if product_weight.get("estimated") else "confirmed_source",
        },
        "package_dimensions": {
            "length_mm": float(package_dimensions["length"]) * 10,
            "width_mm": float(package_dimensions["width"]) * 10,
            "height_mm": float(package_dimensions["height"]) * 10,
            "source": str(package_dimensions["source_ref"]),
            "source_status": "estimated_system" if package_dimensions.get("estimated") else "confirmed_source",
        },
        "package_weight": {
            "value_g": float(package_weight["value"]),
            "source": str(package_weight["source_ref"]),
            "source_status": "estimated_system" if package_weight.get("estimated") else "confirmed_source",
        },
        "vat": str(shop.get("default_vat") or "0"),
        "stock_mode": "not_set",
        "old_price": None,
        "configured_at": now(),
        "configured_by": "automatic_pipeline_from_verified_sources",
    }


def build_package(product_dir: Path, write: bool = True) -> Dict[str, Any]:
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
        draft["description_category_id"] = int(category["category_id"])
        draft["type_id"] = int(category["type_id"])
        draft["category"] = {
            "category_id": int(category["category_id"]),
            "category_name": str(category["category_name"]),
            "confidence": float(category.get("confidence") or 0),
            "match_status": str(category.get("match_status") or "api_confirmed"),
            "metadata_source": "ozon_seller_api",
        }
        if write:
            write_json_atomic(draft_path, draft)
    config_path = output / "ozon-upload-config.json"
    if config_path.is_file():
        config = load_json(config_path)
        refreshed = _auto_upload_config(product_dir, metadata)
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
        config = _auto_upload_config(product_dir, metadata)
        config_errors = validate_value(config, TEMPLATES / "ozon-upload-config.schema.json")
        if config_errors:
            raise ValueError("Automatic upload config validation failed: " + "; ".join(config_errors))
        if write:
            write_json_atomic(config_path, config)
    description = load_json(output / "description-ru.json")
    result_path = output / "ozon-result.json"
    result = load_json(result_path) if result_path.is_file() else {}
    tags = build_tags(product_dir)
    rich = build_rich_content(product_dir, result)
    rich_meta = _find_attribute_by_names(metadata, ("Rich-контент", "Rich-контент JSON", "Rich content"))
    if not rich_meta:
        rich["warnings"].append("The selected category has no live Rich Content attribute; it will not be sent.")
    rich["attribute_id"] = int(rich_meta["attribute_id"]) if rich_meta else 0
    colors = build_color_variants(product_dir, source)
    color_policy = build_color_variant_policy(product_dir.name, source, colors)
    attrs = build_attributes(product_dir, metadata, config, description, tags, rich)
    check = build_final_check(product_dir, tags, attrs, rich, colors, color_policy, config)
    coverage = build_attribute_coverage_report(attrs)
    package = {
        "ozon-tags.json": tags,
        "ozon-attributes-final.json": attrs,
        "rich-content.json": rich,
        "color-variants.json": colors,
        "color-variant-policy.json": color_policy,
        "final-upload-check.json": check,
        "attribute-coverage-report.json": coverage,
    }
    validation = validate_package(package)
    failures = {name: errors for name, errors in validation.items() if errors}
    if failures:
        raise ValueError("Field-completion schema validation failed: " + json.dumps(failures, ensure_ascii=False))
    if write:
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
