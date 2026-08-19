"""Shared Russian-only color normalization for Ozon-facing fields.

The Ozon free-text color-name field (``Название цвета`` / 10097) must contain
only a Russian color word.  It must never receive a mixed SKU label such as
``卡其色1.9L`` or ``601-800 мл``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


COLOR_NAME_ATTRIBUTE_IDS = {10097}
COLOR_NAME_ATTRIBUTE_NAMES = {"название цвета"}
_RUSSIAN_COLOR_RE = re.compile(r"^[А-Яа-яЁё]+(?:[ -][А-Яа-яЁё]+)*$")


_COLOR_MAPPINGS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("黑曜石", "曜石黑", "黑色", "black", "черн"), "черный"),
    (("奶油白", "乳白", "米白", "暖白", "象牙白", "кремов"), "кремовый"),
    (("白色", "white", "бел"), "белый"),
    (("军绿色", "军绿", "橄榄绿", "橄榄色", "olive", "олив"), "зеленый"),
    (("浅绿", "淡绿", "светло-зелен"), "светло-зеленый"),
    (("深绿", "墨绿", "темно-зелен", "тёмно-зелен"), "темно-зеленый"),
    (("绿色", "green", "зелен"), "зеленый"),
    (("卡其色", "卡其", "khaki", "хаки"), "хаки"),
    (("酒红", "酒红色", "深酒红", "бордов"), "бордовый"),
    (("红色", "red", "красн"), "красный"),
    (("粉红", "粉色", "pink", "розов"), "розовый"),
    (("浅蓝", "淡蓝", "светло-голуб", "голуб"), "голубой"),
    (("светло-син",), "светло-синий"),
    (("深蓝", "藏青", "темно-син", "тёмно-син"), "темно-синий"),
    (("蓝色", "blue", "син"), "синий"),
    (("黄色", "yellow", "желт", "жёлт"), "желтый"),
    (("橙黄", "橙色", "orange", "оранж"), "оранжевый"),
    (("透明", "transparent", "прозрач"), "прозрачный"),
    (("银色", "silver", "серебр"), "серебристый"),
    (("金色", "gold", "золот"), "золотистый"),
    (("灰色", "grey", "gray", "сер"), "серый"),
    (("米色", "beige", "беж"), "бежевый"),
    (("棕色", "咖啡色", "brown", "корич"), "коричневый"),
    (("紫色", "purple", "violet", "фиолет"), "фиолетовый"),
)


def _normalized_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def is_russian_color_name(value: Any) -> bool:
    """Return true only when the value is Russian text with no numbers/specs."""
    text = _normalized_text(value).replace("—", "-").replace("–", "-")
    if not text:
        return False
    return bool(_RUSSIAN_COLOR_RE.fullmatch(text))


def is_color_name_attribute(attribute_id: Any = None, attribute_name: Any = None) -> bool:
    try:
        if int(attribute_id or 0) in COLOR_NAME_ATTRIBUTE_IDS:
            return True
    except (TypeError, ValueError):
        pass
    name = _normalized_text(attribute_name).casefold()
    return name in COLOR_NAME_ATTRIBUTE_NAMES


def normalize_russian_color_name(value: Any) -> str | None:
    """Extract one safe Russian color word from source text.

    Capacity, model, size and other numeric SKU fragments are deliberately not
    preserved.  If no color is present, return ``None`` rather than guessing.
    """
    raw = _normalized_text(value)
    if not raw:
        return None
    folded = raw.casefold().replace("ё", "е")
    for tokens, russian in _COLOR_MAPPINGS:
        if any(token.casefold().replace("ё", "е") in folded for token in tokens):
            return russian
    if is_russian_color_name(raw) and not re.search(r"\d", raw):
        return raw.lower().replace("ё", "е")
    return None


def russian_color_or_unknown(value: Any) -> str:
    return normalize_russian_color_name(value) or "unknown"
