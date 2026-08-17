import copy
import os
import tempfile
import unittest
from pathlib import Path

from scripts.validate_product import (
    ROOT,
    can_start_upload,
    load_json,
    validate_product,
    validate_status_integrity,
    validate_source_truthfulness
)


PRODUCT_DIR = ROOT / "products" / "P000001"
FULL_RUNTIME_PRODUCT = (
    (PRODUCT_DIR / "output/ozon-draft.json").is_file()
    and any((PRODUCT_DIR / "output" / name).is_file() for name in ("image-qc-report.json", "qc-report.json"))
)


if __name__ == "__main__":
    unittest.main()
