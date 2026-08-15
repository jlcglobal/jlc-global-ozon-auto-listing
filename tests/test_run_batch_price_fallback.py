import unittest

from scripts.run_batch import captured_shared_purchase_price_cny


class RunBatchPriceFallbackTest(unittest.TestCase):
    def test_detects_shared_product_price_for_skus_without_direct_price(self):
        source = {
            "price_information": {
                "currency": "CNY",
                "price_ranges": [
                    {"min_quantity": None, "price_cny": 26, "raw_text": "价格¥26.00登录查看更多优惠"},
                    {"min_quantity": None, "price_cny": 26, "raw_text": "￥26"},
                    {"min_quantity": None, "price_cny": 99500, "raw_text": "库存99500个"},
                    {"min_quantity": None, "price_cny": 1, "raw_text": "价格比较说明：（1）活动前价格"},
                ],
                "raw_text": "价格¥26.00 | 库存99500个",
            }
        }
        self.assertEqual(captured_shared_purchase_price_cny(source), 26.0)


if __name__ == "__main__":
    unittest.main()
