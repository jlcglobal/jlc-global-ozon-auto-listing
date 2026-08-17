import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.image_generator_contract import build_prompt_packet
from scripts.product_positioning_agent import build_positioning_draft
from scripts.image_planner import ROOT, load_json
from scripts.validate_product import (
    validate_positioning_integrity,
    validate_product,
    validate_schema,
)


PRODUCT_IDS = ("P000004", "P000005", "P000003")
POSITIONING_FIXTURES_INSTALLED = all(
    (ROOT / "products" / product_id / "output/product-positioning.json").is_file()
    for product_id in PRODUCT_IDS
)


if __name__ == "__main__":
    unittest.main()
