"""Evidence-aware scoring without invented metrics."""

from __future__ import annotations

from typing import Dict, Mapping, Optional


def calculate_index(
    components: Mapping[str, Optional[float]],
    weights: Mapping[str, float],
) -> Dict[str, object]:
    """Calculate a 0-100 index using only available components.

    Missing components are excluded and reduce data completeness. They are never
    replaced with model-generated numbers.
    """

    unknown_components = set(components) - set(weights)
    if unknown_components:
        raise ValueError(f"Unknown score components: {', '.join(sorted(unknown_components))}")
    if not weights or any(float(weight) <= 0 for weight in weights.values()):
        raise ValueError("All score weights must be positive")

    total_weight = sum(float(weight) for weight in weights.values())
    available_weight = 0.0
    weighted_total = 0.0
    used: Dict[str, float] = {}
    missing = []
    for name, weight in weights.items():
        raw_value = components.get(name)
        if raw_value is None:
            missing.append(name)
            continue
        value = float(raw_value)
        if not 0 <= value <= 100:
            raise ValueError(f"Score component '{name}' must be between 0 and 100")
        numeric_weight = float(weight)
        used[name] = value
        available_weight += numeric_weight
        weighted_total += value * numeric_weight

    if available_weight == 0:
        return {
            "score": "unknown",
            "data_completeness": 0,
            "used_components": {},
            "missing_components": sorted(weights),
        }

    return {
        "score": round(weighted_total / available_weight, 1),
        "data_completeness": round(available_weight / total_weight * 100),
        "used_components": used,
        "missing_components": missing,
    }
