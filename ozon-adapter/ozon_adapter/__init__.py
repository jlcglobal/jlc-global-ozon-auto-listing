"""Read-only Ozon Seller API adapter for marketplace metadata."""

from .client import OzonApiError, OzonReadOnlyClient
from .config import OzonConfig, OzonConfigurationError
from .service import (
    build_live_metadata_package,
    fetch_and_write_product_metadata,
    remap_cached_product_metadata,
)
from .variant_rules import load_cached_aspect_rule

__all__ = [
    "OzonApiError",
    "OzonConfig",
    "OzonConfigurationError",
    "OzonReadOnlyClient",
    "build_live_metadata_package",
    "fetch_and_write_product_metadata",
    "remap_cached_product_metadata",
    "load_cached_aspect_rule",
]
