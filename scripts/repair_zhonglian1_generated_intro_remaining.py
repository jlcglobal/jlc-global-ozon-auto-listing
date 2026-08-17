#!/usr/bin/env python3
"""One-time zhonglian1 repair for safe cards that need generated intros.

This script is intentionally separate from the weekly/default search visibility
flow.  It handles the user-approved one-time case where a card has no usable
intro to preserve, so a new factual intro may be generated from current Ozon
card facts.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "market-intelligence/reports/search-visibility-controlled-repairs/20260804-053100-zhonglian1-final-summary.json"

spec = importlib.util.spec_from_file_location(
    "zhonglian1_repair",
    ROOT / "scripts/repair_zhonglian1_search_visibility_fields.py",
)
repair = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(repair)


CONFIRMATION = "APPLY_ZHONGLIAN1_GENERATED_INTRO_ONE_TIME_20260804"

BLOCKING_REASONS = {
    "adult_product_moderation_risk",
    "prohibited_drone_risk",
    "prohibited_memorial_urn_risk",
    "ozon_readback_mismatch_or_not_accepted",
}

BRAND_OR_MODEL_FRAGMENTS = (
    "hotone",
    "ampero",
    "instax",
    "edifier",
    "ulanzi",
    "sibionics",
    "сибионикс",
    "ambeo",
    "soundbar",
    "q75",
    "mr3bt",
    "305p",
    "mkii",
    "ga2100",
    "реплика",
    "эксклюзивн",
    "премиальн",
)

INTRO_REMOVE_PATTERNS = (
    r"\bHotone\b",
    r"\bAmpero\b",
    r"\bInstax\b",
    r"\bEdifier\b",
    r"\bUlanzi\b",
    r"\bSibionics\b",
    r"\bAMBEO\b",
    r"\bSoundbar\b",
    r"\bMax\b",
    r"\bMini\b",
    r"\bOrange\b",
    r"\bMR3BT\b",
    r"\bQ75[НH]?\b",
    r"\b305P\b",
    r"\bMKII\b",
    r"\bII\b",
    r"\bstomp\b",
    r"\bGA2100\b",
    r"\bCA25B\b",
    r"\bЭксклюзивн\w*\b",
    r"\bПремиальн\w*\b",
    r"\bОфициальн\w*\b",
    r"\bОригинальн\w*\b",
    r"\bПрофессиональн\w*\b",
    r"\bДоставк\w*\b",
    r"\bСертифик\w*\b",
    r"\bГарант\w*\b",
    r"\bЛучший\b",
    r"\bPremium\b",
    r"\bOriginal\b",
)


def load_module_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def target_product_ids(summary: Mapping[str, Any]) -> List[str]:
    targets: List[str] = []
    for item in summary.get("remaining_not_exact_30_items") or []:
        if not isinstance(item, Mapping):
            continue
        errors = {str(value) for value in item.get("last_preflight_errors") or []}
        if errors & BLOCKING_REASONS:
            continue
        product_id = str(item.get("product_id") or "").strip()
        if product_id:
            targets.append(product_id)
    return sorted(set(targets), key=lambda value: int(value) if value.isdigit() else 0)


def cleaned_title(raw_title: str) -> str:
    title = repair.compact_text(raw_title)
    if not title:
        return ""
    title = re.sub(r"(\d+)\s*[xх]\s*(\d+)", r"\1 на \2", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+[xх]\s+(\d+)", r" \1", title, flags=re.IGNORECASE)
    for pattern in INTRO_REMOVE_PATTERNS:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    title = re.sub(r"\b[0-9A-Za-zА-Яа-я-]*[A-Za-z][0-9A-Za-zА-Яа-я-]*\b", "", title)
    if "термос" in title.casefold():
        title = re.sub(r",?\s*500\s*л\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*/\s*", " ", title)
    title = re.sub(r"\s{2,}", " ", title)
    title = re.sub(r"\s+([,.;:])", r"\1", title)
    title = re.sub(r"^[,.;:\-\s]+|[,.;:\-\s]+$", "", title)
    if title.casefold() == "картридж":
        return "Картридж для совместимого устройства моментальной печати"
    return title[:180].strip()


def context_and_details(title: str) -> Tuple[str, str]:
    lowered = title.casefold()
    tokens = repair.text_tokens(title)

    def has(*prefixes: str) -> bool:
        return repair.has_token_prefix(tokens, *prefixes)

    def has_all(*groups: Tuple[str, ...]) -> bool:
        return all(has(*group) for group in groups)

    if has_all(("держател", "полк"), ("кухн", "спец")):
        return "кухни и хранения мелких предметов", "Помогает держать нужные вещи под рукой и поддерживать порядок в рабочей зоне."
    if has("дефлектор", "капот"):
        return "автомобиля", "Подходит для установки на зону капота и защиты передней части автомобиля от пыли, дождя и мелких загрязнений."
    if has("мишен"):
        return "тренировок по стрельбе", "Подходит для отработки точности в спортивном или тренировочном формате."
    if has("усилител", "эффект", "гитар", "бас") or (has("педал") and has("гитар", "бас", "эффект")):
        return "музыкального оборудования", "Подходит для работы со звучанием электрогитары или бас-гитары и настройки эффектов."
    if has("картридж"):
        return "моментальной фотопечати", "Предназначен для совместимых устройств печати снимков."
    if has("термос", "термокруж"):
        return "напитков в дороге, дома или на работе", "Подходит для горячих и холодных напитков, а формат крышки или корпуса выбирается по текущей карточке."
    if has("футбольн", "сетка", "футбол"):
        return "спорта и тренировок", "Подходит для игры и тренировок с футбольным инвентарем."
    if has("прожектор", "светодиодн", "освещ"):
        return "уличного освещения", "Подходит для двора, участка, гаража или другой зоны, где нужен направленный свет."
    if has("чехол", "тент") and has("автомоб", "машин"):
        return "защиты автомобиля", "Помогает закрыть кузов от дождя, пыли и снега при сезонном хранении или стоянке."
    if has("подставк") and has("цвет", "растен", "горш"):
        return "цветов и домашнего интерьера", "Подходит для размещения горшков и аккуратной организации растений."
    if has("термопот", "водонагревател"):
        return "горячей воды на кухне, даче или в офисе", "Подходит для нагрева и поддержания температуры воды."
    if has("тепловентилятор", "обогревател") and has("автомоб", "машин", "прикуривател"):
        return "автомобиля", "Подходит для дополнительного обдува и обогрева салона от прикуривателя."
    if has("кофе", "завариван"):
        return "заваривания кофе", "Подходит для приготовления напитка дома, на работе или в поездке."
    if has("датчик", "передатчик"):
        return "совместимого электронного оборудования", "Комплект включает датчики и передатчик, поэтому подходит для работы с совместимой системой."
    if has("радиатор", "камер"):
        return "совместимой камеры", "Подходит для отвода тепла от камеры при длительной работе."
    if has("саундбар", "динамик", "акустическ", "монитор"):
        return "звукового оборудования", "Подходит для домашнего звука, студийных задач или работы с акустикой в зависимости от типа товара."
    if has("детск", "каталк", "толокар") or (has("мотоцикл") and has("детск")):
        return "детской игры и катания", "Подходит для малышей указанного возраста и сценария катания, заявленного в карточке."
    if has("корпус", "браслет", "час"):
        return "обновления совместимых часов", "Корпус и браслет используются для замены внешних деталей и обновления вида часов."
    if has("спин", "поддержк", "подушк"):
        return "поддержки спины", "Подходит для кресла, сиденья или рабочего места, где нужна дополнительная опора."
    if has("органайзер", "хранен", "кофр", "кровать"):
        return "хранения вещей дома", "Помогает аккуратно разместить одежду, обувь, белье или другие бытовые предметы."
    if "кофе" in lowered:
        return "кухни и напитков", "Подходит для повседневного использования по назначению."
    return "повседневного использования по назначению", "Подходит для дома, работы или поездки в зависимости от сценария товара."


def generated_intro(card: Mapping[str, Any]) -> str:
    title = cleaned_title(str(card.get("title") or ""))
    if not title:
        title = "Товар"
    context, detail = context_and_details(f"{title} {card.get('title') or ''}")
    text = (
        f"{title} — товар для {context}. "
        f"{detail} "
        "Формат товара помогает использовать изделие по назначению и аккуратно хранить его вместе с нужными принадлежностями."
    )
    return repair.compact_text(text)


def extra_block_reason(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    key = repair.tag_key(text)
    if any(fragment in text or fragment in key for fragment in BRAND_OR_MODEL_FRAGMENTS):
        return "brand_or_model_fragment"
    if re.search(r"[a-z]", text):
        return "latin_fragment"
    return ""


def current_tag_relevant(tag: str, card: Mapping[str, Any]) -> bool:
    if extra_block_reason(tag):
        return False
    title = repair.compact_text(card.get("title") or "").casefold().replace("ё", "е")
    tag_key = repair.tag_key(tag)
    title_tokens = [
        token for token in repair.text_tokens(title)
        if token not in repair.STOPWORDS and len(token) > 3
    ]
    if not title_tokens:
        return False
    if any(repair.stem(token) in tag_key for token in title_tokens):
        return True
    if repair.has_token_prefix(title_tokens, "кухн", "держател") and any(
        fragment in tag_key for fragment in ("полка", "спец", "органайзер")
    ):
        return True
    if repair.has_token_prefix(title_tokens, "кофе") and "кофе" in tag_key:
        return True
    if repair.has_token_prefix(title_tokens, "саундбар", "динамик", "акустик", "монитор") and any(
        fragment in tag_key for fragment in ("акустик", "динамик", "монитор", "саундбар", "зву")
    ):
        return True
    if repair.has_token_prefix(title_tokens, "термос", "термокруж") and any(
        fragment in tag_key for fragment in ("термос", "круж", "чай", "кофе", "напит")
    ):
        return True
    if repair.has_token_prefix(title_tokens, "прожектор", "светодиодн") and any(
        fragment in tag_key for fragment in ("прожектор", "свет", "освещ", "улиц", "двор")
    ):
        return True
    return False


def candidate_relevant_for_card(item: Mapping[str, Any], card: Mapping[str, Any]) -> bool:
    tag = str(item.get("tag") or "")
    phrase = str(item.get("phrase") or "")
    if current_tag_relevant(tag, card):
        return True
    if phrase and current_tag_relevant("#" + repair.canonical_hashtag(phrase).lstrip("#"), card):
        return True
    return False


def one_time_extra_phrases(card: Mapping[str, Any]) -> List[str]:
    title = repair.compact_text(f"{card.get('title') or ''} {cleaned_title(str(card.get('title') or ''))}")
    tokens = repair.text_tokens(title)

    def has(*prefixes: str) -> bool:
        return repair.has_token_prefix(tokens, *prefixes)

    if has("дефлектор", "капот"):
        return [
            "дефлектор капота",
            "накладка на капот",
            "защита капота",
            "авто дефлектор",
            "дефлектор для авто",
            "дефлектор для машины",
            "аксессуар для авто",
            "защита передней части",
            "капот авто",
            "тюнинг капота",
            "ветровик капота",
            "накладка автомобильная",
            "защитная накладка",
            "авто аксессуар",
            "деталь для автомобиля",
            "деталь кузова",
            "капот защита",
            "защита от пыли",
            "защита от дождя",
            "для автомобиля",
            "для машины",
            "для капота",
            "автомобильный аксессуар",
            "наружный аксессуар",
            "декор капота",
            "накладка защитная",
            "защита кузова",
            "авто накладка",
            "капотная накладка",
            "передний дефлектор",
            "автомобильный дефлектор",
            "дефлектор наружный",
        ]
    if has("картридж") and has("печати", "печать", "фото", "снимк", "устройств"):
        return [
            "картридж для фото",
            "картридж для печати",
            "фотокартридж",
            "моментальная печать",
            "печать снимков",
            "для фотопечати",
            "фото расходник",
            "расходник для печати",
            "кассета для фото",
            "кассета для печати",
            "картридж для снимков",
            "бумага для фото",
            "фотобумага",
            "печать фотографий",
            "снимки моментальные",
            "для моментальной печати",
            "для фотоаппарата",
            "для принтера фото",
            "комплект для печати",
            "фото кассета",
            "расходный материал",
            "фотопечать дома",
            "печать дома",
            "карточки для фото",
            "материал для снимков",
            "совместимый картридж",
            "картридж совместимый",
            "для печати фото",
            "снимки для альбома",
            "фото для альбома",
            "запасной картридж",
            "картридж печати",
        ]
    if has("кофе", "завариван"):
        return [
            "комплект для кофе",
            "набор для кофе",
            "заваривание кофе",
            "кофейный набор",
            "кофе дома",
            "кофе на работе",
            "для молотого кофе",
            "для заваривания",
            "посуда для кофе",
            "фильтр для кофе",
            "многоразовый фильтр",
            "кофейный фильтр",
            "кухонный набор",
            "кофейник для дома",
            "набор бариста",
            "ручное заваривание",
            "приготовление кофе",
            "кофе в дороге",
            "кофе для кухни",
            "кофейная посуда",
            "кофейные аксессуары",
            "нержавеющая сталь",
            "аксессуар для кофе",
            "для кофейных напитков",
            "для кухни",
            "для напитков",
            "для дома",
            "практичный набор",
            "чайник для кофе",
            "ситечко для кофе",
            "заварник для кофе",
            "набор для напитков",
        ]
    if has("студийн", "монитор", "динамик", "акустик"):
        return [
            "студийный монитор",
            "мониторный динамик",
            "активный динамик",
            "студийная акустика",
            "акустический монитор",
            "монитор для студии",
            "акустика для студии",
            "динамик для студии",
            "звук для студии",
            "аудио монитор",
            "звуковое оборудование",
            "акустическая система",
            "для домашней студии",
            "для записи звука",
            "для сведения музыки",
            "для работы со звуком",
            "для музыканта",
            "для звукорежиссера",
            "для компьютера",
            "настольная акустика",
            "аудио оборудование",
            "мониторный звук",
            "контроль звука",
            "звук для дома",
            "компактная акустика",
            "активная акустика",
            "одиночный динамик",
            "черный динамик",
            "контрольный динамик",
            "студийный звук",
            "звук для работы",
            "мониторинг звука",
            "динамик для записи",
            "динамик для сведения",
            "акустика для музыки",
            "акустика для компьютера",
            "монитор для звука",
            "настольный монитор",
            "активный монитор",
            "звуковой монитор",
            "студийное оборудование",
            "музыкальное оборудование",
            "динамик для музыки",
        ]
    if has("спин", "поддержк", "подушк"):
        return [
            "поддержка для спины",
            "опора для спины",
            "подушка для спины",
            "поддержка поясницы",
            "опора поясницы",
            "для кресла",
            "для сиденья",
            "для стула",
            "для рабочего места",
            "для офиса",
            "для дома",
            "комфорт спины",
            "подушка на стул",
            "подушка на кресло",
            "спинка для кресла",
            "эргономичная поддержка",
            "дополнительная опора",
            "мягкая поддержка",
            "аксессуар для кресла",
            "для долгого сидения",
            "для автомобиля",
            "для компьютерного кресла",
            "поддержка осанки",
            "опора на сиденье",
            "спинка сиденья",
            "домашний комфорт",
            "офисный комфорт",
            "поддержка в кресле",
            "поддержка на стул",
            "опора для поясницы",
            "подушка поясничная",
            "товар для спины",
        ]
    return []


def filtered_candidates(candidates: Sequence[Mapping[str, Any]], card: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    extra_candidates = [
        {"tag": repair.canonical_hashtag(phrase), "source": "one_time_product_type", "phrase": phrase}
        for phrase in one_time_extra_phrases(card)
    ]
    for item in [*extra_candidates, *candidates]:
        tag = str(item.get("tag") or "").strip()
        if not tag or extra_block_reason(tag) or extra_block_reason(item.get("phrase")):
            continue
        key = repair.tag_key(tag)
        if not key or key in seen:
            continue
        if not repair.candidate_allowed_for_card(item, card):
            continue
        if not candidate_relevant_for_card(item, card):
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def build_one_time_preflight() -> Tuple[Path, Dict[str, Any]]:
    summary = load_module_json(SUMMARY_PATH)
    target_ids = set(target_product_ids(summary))
    if not target_ids:
        raise RuntimeError("No safe generated-intro targets found")

    client = repair.CountingOzonClient(repair.load_credentials(repair.SHOP_ID))
    conflict = repair.active_write_conflicts()
    if conflict["active"]:
        raise RuntimeError("Active production write conflict: " + "; ".join(conflict["reasons"]))

    catalog_all = repair.list_catalog(client)
    catalog = [
        item for item in catalog_all
        if str(item.get("product_id") or "").strip() in target_ids
    ]
    product_ids = sorted({int(item["product_id"]) for item in catalog if str(item.get("product_id") or "").isdigit()})
    offer_ids = [str(item.get("offer_id") or "").strip() for item in catalog if str(item.get("offer_id") or "").strip()]
    info_rows = repair.product_info(client, product_ids)
    attribute_rows = repair.product_attributes(client, offer_ids)
    cards = repair.build_cards(catalog, info_rows, attribute_rows)
    date_from, date_to = repair.search_window()
    api_query_rows_by_sku, api_query_errors = repair.product_query_details(
        client,
        [str(card.get("sku") or "") for card in cards],
        date_from=date_from,
        date_to=date_to,
    )

    backup = {
        "schema_version": "1.0.0",
        "mode": "generated_intro_one_time_remote_backup",
        "shop_id": repair.SHOP_ID,
        "generated_at": repair.now_iso(),
        "target_product_ids": sorted(target_ids),
        "catalog_card_count": len(catalog),
        "backup_card_count": len(cards),
        "catalog": catalog,
        "product_info": info_rows,
        "product_attributes": attribute_rows,
        "api_query_date_from": date_from,
        "api_query_date_to": date_to,
        "api_top15_search_queries_by_sku": api_query_rows_by_sku,
        "api_top15_search_query_errors": api_query_errors,
        "cards": cards,
        "write_api_calls": 0,
        "read_api_calls": client.read_api_calls,
        "inventory_api_calls": client.inventory_api_calls,
    }
    backup_path = repair.save_report(f"{repair.SHOP_ID}-generated-intro-one-time-backup", backup)

    items: List[Dict[str, Any]] = []
    for card in cards:
        sku = str(card.get("sku") or "").strip()
        query_rows = api_query_rows_by_sku.get(sku, [])
        final_intro = generated_intro(card)
        tag_card = dict(card)
        tag_card["title"] = cleaned_title(str(card.get("title") or "")) or str(card.get("title") or "")
        candidates = filtered_candidates(repair.tag_candidates(tag_card, final_intro, query_rows), tag_card)
        current_safe_tags = [
            str(tag).strip() for tag in card.get("current_subject_tags") or []
            if repair.valid_existing_tag(tag) and current_tag_relevant(str(tag), card)
        ]
        merged = repair.merge_tags(current_safe_tags, candidates, query_rows, tag_card)
        final_tags = [str(tag) for tag in merged.get("final_tags") or []]
        errors = [value for value in (merged.get("error"),) if value]
        if len(final_tags) != 30:
            errors.append("final_subject_tag_count_not_30")
        if any(not repair.valid_existing_tag(tag) for tag in final_tags):
            errors.append("invalid_final_subject_tag")
        if len({repair.tag_key(tag) for tag in final_tags}) != len(final_tags):
            errors.append("duplicate_final_subject_tag")
        if not final_intro:
            errors.append("empty_generated_intro")
        if extra_block_reason(final_intro):
            errors.append("generated_intro_contains_brand_or_latin_fragment")
        intro_risk = repair.intro_moderation_risk_reason(final_intro)
        if intro_risk:
            errors.append(intro_risk)
        card_risk = repair.card_update_risk_reason({"title": "", **card}, final_intro)
        if card_risk and card_risk not in {"intro_brand_recheck_risk"}:
            errors.append(card_risk)
        tag_risk = repair.final_tag_moderation_risk_reason(final_tags)
        if tag_risk:
            errors.append(tag_risk)
        blocked_final_tags = [tag for tag in final_tags if extra_block_reason(tag)]
        if blocked_final_tags:
            errors.append("final_tags_contain_brand_or_latin_fragment")

        new_tag_details = [dict(item) for item in merged.get("new_tag_details") or [] if isinstance(item, Mapping)]
        new_api_tags = [
            item["tag"] for item in new_tag_details
            if str(item.get("source") or "") in repair.SEARCH_TAG_SOURCES
        ]
        generated_tags = [
            item["tag"] for item in new_tag_details
            if str(item.get("source") or "") not in repair.SEARCH_TAG_SOURCES
        ]
        current_tags = [str(tag) for tag in card.get("current_subject_tags") or []]
        tag_update_required = [repair.tag_key(tag) for tag in final_tags] != [
            repair.tag_key(tag) for tag in current_tags if repair.valid_existing_tag(tag)
        ]
        intro_update_required = final_intro != str(card.get("current_intro") or "").strip()
        row_status = "ready" if not errors else "skipped"
        if row_status == "ready" and not tag_update_required and not intro_update_required:
            row_status = "already_ok"
        items.append({
            "status": row_status,
            "product_id": card.get("product_id") or "",
            "offer_id": card.get("offer_id") or "",
            "sku": card.get("sku") or "",
            "title": card.get("title") or "",
            "current_subject_tags": card.get("current_subject_tags") or [],
            "current_valid_subject_tags": [
                str(tag).strip() for tag in card.get("current_subject_tags") or []
                if repair.valid_existing_tag(tag)
            ],
            "current_valid_subject_tag_count": len([
                tag for tag in card.get("current_subject_tags") or []
                if repair.valid_existing_tag(tag)
            ]),
            "invalid_current_subject_tags": [
                str(tag) for tag in card.get("current_subject_tags") or []
                if not repair.valid_existing_tag(tag) or not current_tag_relevant(str(tag), card)
            ],
            "final_subject_tags": final_tags,
            "final_subject_tag_count": len(final_tags),
            "new_subject_tags": merged.get("new_tags") or [],
            "new_tag_details": new_tag_details,
            "new_api_subject_tags": new_api_tags,
            "generated_subject_tags": generated_tags,
            "new_api_subject_tag_count": len(new_api_tags),
            "generated_subject_tag_count": len(generated_tags),
            "removed_subject_tags": merged.get("removed_tags") or [],
            "subject_tag_strategy": "one_time_generated_intro_" + str(merged.get("strategy") or ""),
            "tag_candidates_sample": candidates[:20],
            "current_intro": str(card.get("current_intro") or ""),
            "intro_source_status": "generated_from_current_ozon_facts_one_time",
            "intro_source_type": "one_time_user_authorized_generated_intro",
            "intro_source_report": str(SUMMARY_PATH.relative_to(ROOT)),
            "base_intro": "",
            "final_intro": final_intro,
            "final_intro_length": len(final_intro),
            "intro_supplement": "",
            "intro_search_term": "",
            "intro_search_count": 0,
            "intro_search_source": "",
            "tag_update_required": tag_update_required,
            "intro_update_required": intro_update_required,
            "requires_update": tag_update_required or intro_update_required,
            "api_top15_query_count": len(query_rows),
            "api_top15_queries": query_rows,
            "reliable_search_query_count": len(query_rows),
            "query_source_status": "api_top15" if query_rows else "generated_from_current_ozon_facts",
            "errors": errors,
        })

    ready_or_ok = [item for item in items if item["status"] in {"ready", "already_ok"}]
    preflight = {
        "schema_version": "1.0.0",
        "mode": "generated_intro_one_time_preflight",
        "shop_id": repair.SHOP_ID,
        "generated_at": repair.now_iso(),
        "source_final_summary_report": str(SUMMARY_PATH.relative_to(ROOT)),
        "api_query_date_from": date_from,
        "api_query_date_to": date_to,
        "api_top15_query_error_count": len(api_query_errors),
        "api_top15_query_errors": api_query_errors,
        "backup_report": repair.project_relative(backup_path),
        "current_write_conflict": conflict,
        "confirmation_required_for_apply": CONFIRMATION,
        "changes": [f"attribute_{repair.OZON_HASHTAG_ATTRIBUTE_ID}", f"attribute_{repair.OZON_ANNOTATION_ATTRIBUTE_ID}"],
        "untouched": ["title", "price", "images", "brand", "category", "sku", "stock", "warehouse", "activation"],
        "summary": {
            "total_cards": len(items),
            "backup_cards": len(cards),
            "ready_cards": sum(item["status"] == "ready" for item in items),
            "already_ok_cards": sum(item["status"] == "already_ok" for item in items),
            "skipped_cards": sum(item["status"] == "skipped" for item in items),
            "requires_update_cards": sum(bool(item["requires_update"]) and item["status"] == "ready" for item in items),
            "generated_intro_cards": sum(item["intro_source_status"] == "generated_from_current_ozon_facts_one_time" for item in items),
            "thirty_tag_ready_or_ok_cards": sum(item["final_subject_tag_count"] == 30 and item["status"] in {"ready", "already_ok"} for item in items),
            "api_top15_cards_with_queries": sum(item["api_top15_query_count"] > 0 for item in items),
            "new_api_subject_tag_total": sum(item["new_api_subject_tag_count"] for item in items),
            "generated_subject_tag_total": sum(item["generated_subject_tag_count"] for item in items),
            "coverage_ratio": round(len(ready_or_ok) / len(items), 4) if items else 0,
        },
        "items": items,
        "write_api_calls": 0,
        "read_api_calls": client.read_api_calls,
        "inventory_api_calls": client.inventory_api_calls,
    }
    preflight_path = repair.save_report(f"{repair.SHOP_ID}-generated-intro-one-time-preflight", preflight)
    preflight["preflight_report"] = repair.project_relative(preflight_path)
    preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return preflight_path, preflight


def apply_preflight(preflight_path: Path, preflight: Mapping[str, Any], *, batch_size: int, verify_seconds: int) -> Dict[str, Any]:
    client = repair.CountingOzonClient(repair.load_credentials(repair.SHOP_ID))
    return repair.apply_rows(
        client,
        preflight_path,
        preflight,
        scope="all",
        batch_size=batch_size,
        verify_seconds=verify_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preflight-report", default="")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--verify-seconds", type=int, default=300)
    args = parser.parse_args()

    if args.preflight == args.apply:
        parser.error("Use exactly one of --preflight or --apply")

    if args.preflight:
        path, preflight = build_one_time_preflight()
        print(json.dumps({
            "mode": "generated_intro_one_time_preflight",
            "shop_id": repair.SHOP_ID,
            "preflight_report": repair.project_relative(path),
            "backup_report": preflight["backup_report"],
            "summary": preflight["summary"],
            "write_api_calls": 0,
            "read_api_calls": preflight["read_api_calls"],
            "inventory_api_calls": preflight["inventory_api_calls"],
        }, ensure_ascii=False))
        return 0

    if args.confirm != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation is required: {CONFIRMATION}")
    if args.verify_seconds < 300:
        raise RuntimeError("Delayed field verification must run for at least 300 seconds")
    if not args.preflight_report:
        raise RuntimeError("--preflight-report is required")
    preflight_path, preflight = repair.load_preflight(args.preflight_report)
    receipt = apply_preflight(
        preflight_path,
        preflight,
        batch_size=args.batch_size,
        verify_seconds=args.verify_seconds,
    )
    print(json.dumps({
        "status": receipt["status"],
        "scope": receipt["scope"],
        "submitted_card_count": receipt["submitted_card_count"],
        "processed_card_count": receipt["processed_card_count"],
        "card_success_count": receipt["card_success_count"],
        "field_readback_match_count": receipt["field_readback_match_count"],
        "field_readback_mismatch_count": receipt["field_readback_mismatch_count"],
        "field_success_ratio": receipt["field_success_ratio"],
        "task_ids": receipt["task_ids"],
        "write_api_calls": receipt["write_api_calls"],
        "read_api_calls": receipt["read_api_calls"],
        "inventory_api_calls": receipt["inventory_api_calls"],
        "receipt_report": receipt["receipt_report"],
    }, ensure_ascii=False))
    return 0 if receipt["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
