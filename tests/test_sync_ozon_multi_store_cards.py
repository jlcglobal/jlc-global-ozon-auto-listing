import unittest

from scripts.sync_ozon_multi_store_cards import (
    build_request_item,
    card_verified_against_source,
    failure_summary,
    ordered_images,
    pair_action,
    snapshot_row,
    source_card_ready,
)


def card(**overrides):
    value = {
        "id": 101,
        "offer_id": "OFFER-1",
        "sku": 202,
        "name": "Storage box 27 x 22 x 18 cm",
        "price": "156.00",
        "currency_code": "CNY",
        "vat": "0.00",
        "model_info": {"model_id": 303},
        "primary_image": ["https://img/main.jpg"],
        "images": ["https://img/main.jpg", "https://img/detail.jpg"],
        "statuses": {"moderate_status": "approved", "validation_status": "success"},
        "errors": [],
        "is_archived": False,
    }
    value.update(overrides)
    return value


class MultiStoreCardSyncTest(unittest.TestCase):
    def test_images_are_ordered_deduplicated_and_bounded(self):
        item = card(primary_image=["a"], images=["a", "b", *[str(i) for i in range(20)]])
        images = ordered_images(item)
        self.assertEqual(images[:2], ["a", "b"])
        self.assertEqual(len(images), 15)

    def test_source_requires_remote_sku_model_approval_and_images(self):
        self.assertTrue(source_card_ready(card()))
        self.assertFalse(source_card_ready(card(sku="")))
        self.assertFalse(source_card_ready(card(images=[], primary_image=[])))
        self.assertFalse(source_card_ready(card(statuses={"moderate_status": "declined"})))

    def test_failed_draft_is_linked_and_existing_link_only_needs_images(self):
        source = card()
        failed = card(sku="", model_info={}, primary_image=[], images=[])
        self.assertEqual(pair_action(source, failed), "LINK_AND_COPY_IMAGES")
        linked = card(primary_image=[], images=[])
        self.assertEqual(pair_action(source, linked), "COPY_IMAGES_ONLY")
        self.assertEqual(pair_action(source, card()), "ALREADY_SYNCED")

    def test_different_remote_card_is_blocked(self):
        self.assertEqual(
            pair_action(card(), card(model_info={"model_id": 999})),
            "BLOCK_DIFFERENT_REMOTE_CARD",
        )
        self.assertEqual(
            pair_action(card(), card(name="Different product")),
            "BLOCK_DIFFERENT_REMOTE_CARD",
        )

    def test_request_keeps_target_offer_and_price(self):
        request = build_request_item(
            card(sku=5327348509, offer_id="SOURCE", price="999.00"),
            card(offer_id="TARGET", price="156.00", old_price="199.00"),
        )
        self.assertEqual(request["sku"], 5327348509)
        self.assertEqual(request["offer_id"], "TARGET")
        self.assertEqual(request["price"], "156.00")
        self.assertEqual(request["old_price"], "199.00")
        self.assertNotIn("stock", request)

    def test_snapshot_row_contains_field_level_readback_flags(self):
        row = snapshot_row(
            "5972346699437",
            card(sku=5327348509, offer_id="SOURCE", model_info={"model_id": 303}),
            card(sku=5328220793, offer_id="TARGET", model_info={"model_id": 303}),
        )
        self.assertTrue(row["target_has_ozon_sku"])
        self.assertTrue(row["model_id_matches_source"])
        self.assertTrue(row["title_matches_source"])
        self.assertTrue(row["image_count_matches_source"])
        self.assertTrue(row["duplicate_error_absent"])
        self.assertTrue(row["shipment_image_error_absent"])

    def test_final_card_verification_rejects_duplicate_errors(self):
        source = card(sku=5327348509, offer_id="SOURCE")
        duplicate = card(
            sku=5328220793,
            offer_id="TARGET",
            errors=[{"code": "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"}],
        )
        self.assertFalse(card_verified_against_source(source, duplicate))

    def test_failure_summary_keeps_error_code_counts(self):
        summary = failure_summary({
            "rows": [
                {"verified": False, "errors": ["SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"]},
                {"verified": False, "errors": ["SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT"]},
                {"verified": True, "errors": []},
            ]
        })
        self.assertIn("2/3", summary)
        self.assertIn("SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT=2", summary)


if __name__ == "__main__":
    unittest.main()
