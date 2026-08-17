import unittest
from unittest.mock import patch

from scripts import repair_zhonglian1_search_visibility_fields as repair
from scripts import repair_zhonglian1_generated_intro_remaining as generated_intro


class Zhonglian1SearchVisibilityRepairTests(unittest.TestCase):
    def test_api_queries_with_prepositions_are_kept_before_generated_fill(self):
        card = {
            "title": "Контейнер для ферментации овощей с клапаном и прессом",
            "category_name": "",
            "current_intro": "Контейнер для ферментации овощей с клапаном и прессом.",
            "product_attributes": [],
        }
        query_rows = [{
            "query": "контейнер для хранения овощей",
            "count": 1716,
            "source_kind": repair.API_QUERY_SOURCE,
            "value_score": 85.8,
        }]

        candidates = repair.tag_candidates(card, card["current_intro"], query_rows)
        merged = repair.merge_tags([], candidates, query_rows)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#контейнердляхраненияовощей", merged["final_tags"])
        api_tags = [
            item["tag"] for item in merged["new_tag_details"]
            if item["source"] == repair.API_QUERY_SOURCE
        ]
        generated_tags = [
            item["tag"] for item in merged["new_tag_details"]
            if item["source"] != repair.API_QUERY_SOURCE
        ]
        self.assertIn("#контейнердляхраненияовощей", api_tags)
        self.assertTrue(generated_tags)

    def test_read_api_retries_transient_connection_reset(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"result": {"items": []}}'

        client = repair.CountingOzonClient({"client_id": "id", "api_key": "key"})
        with patch.object(repair.urllib.request, "urlopen", side_effect=[ConnectionResetError("reset"), FakeResponse()]):
            result = client.post(repair.PRODUCT_LIST_ENDPOINT, {"filter": {}, "limit": 1})

        self.assertEqual(result, {"result": {"items": []}})
        self.assertEqual(client.read_api_calls, 2)
        self.assertEqual(client.write_api_calls, 0)

    def test_write_api_does_not_retry_transient_connection_reset(self):
        client = repair.CountingOzonClient({"client_id": "id", "api_key": "key"})
        with patch.object(repair.urllib.request, "urlopen", side_effect=ConnectionResetError("reset")):
            with self.assertRaises(ConnectionResetError):
                client.post(repair.PRODUCT_ATTRIBUTES_UPDATE_ENDPOINT, {"items": []})

        self.assertEqual(client.write_api_calls, 1)

    def test_generated_fill_handles_short_glove_title(self):
        card = {
            "title": "Перчатки",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [])

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#перчаткидляработы", merged["final_tags"])
        self.assertEqual(repair._tag_policy_block_reason("#чатпродавца"), "marketing")

    def test_platform_sensitive_tags_are_filtered_without_blocking_unisex(self):
        self.assertEqual(repair._tag_policy_block_reason("#интимнаязонамашинкаженки"), "adult")
        self.assertEqual(repair._tag_policy_block_reason("#жмжсекс"), "adult")
        self.assertEqual(repair._tag_policy_block_reason("#краналюминийдлядома"), "platform_moderation")
        self.assertEqual(repair._tag_policy_block_reason("#керхермойкадляавто"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#перчаткипумафлис"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#QuickCharge"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#Switch"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#клавиатурадлянинтендосвитч"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#дуршлагфарфорбелыи"), "bad_text")
        self.assertEqual(repair._tag_policy_block_reason("#shouldcatсумкадляноутбука"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#гоупрокамера"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#миникамера"), "platform_moderation")
        self.assertEqual(repair._tag_policy_block_reason("#разводник"), "platform_moderation")
        self.assertEqual(repair._tag_policy_block_reason("#сковородалитаягардарика"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#электрочайникbrevio"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#глорияджинсцепочканаталию"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#кронштейндлякроватибосс"), "brand")
        self.assertEqual(repair._tag_policy_block_reason("#чемодан_унисекс"), "")
        self.assertEqual(repair._tag_policy_block_reason("#моментальнаяпечать"), "")
        self.assertEqual(repair._tag_policy_block_reason("#аудиомонитор"), "")
        self.assertEqual(repair._tag_policy_block_reason("#топливо"), "")
        self.assertEqual(repair._tag_policy_block_reason("#светодиодныйпрожектор"), "")
        self.assertEqual(repair._tag_policy_block_reason("#дляуборки"), "")
        self.assertEqual(repair.safe_query("друшлак фаянсовый керамический"), "")
        self.assertEqual(repair.safe_query("керхер мойка для авто"), "")
        self.assertEqual(repair.safe_query("ремен для мужской часи swissoak"), "")
        self.assertEqual(repair.safe_query("глория джинс цепочка на талию"), "")
        self.assertEqual(repair.safe_query("кронштейн для кровати босс"), "")
        self.assertEqual(repair.safe_query("моментальная печать"), "моментальная печать")
        self.assertEqual(repair.safe_query("аудио монитор"), "аудио монитор")
        self.assertEqual(repair.safe_query("светодиодный прожектор"), "светодиодный прожектор")
        self.assertEqual(repair.safe_query("насос для топлива"), "насос для топлива")
        self.assertEqual(repair.safe_query("щетка для уборки"), "щетка для уборки")
        self.assertEqual(repair.safe_query("кейс для инструментов и хранения"), "кейс для инструментов и хранения")
        self.assertEqual(repair.safe_query("тренажер с возвратом крови"), "тренажер с возвратом крови")

    def test_spu_duplicate_notice_is_not_target_field_blocker(self):
        items = [{
            "task_id": 1,
            "product_id": "100",
            "offer_id": "offer",
            "status": "imported",
            "errors": [{
                "level": "error",
                "attribute_id": 0,
                "field": "spu",
                "message": "SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT",
            }],
        }]

        self.assertEqual(repair.blocking_import_errors(items), [])

    def test_target_attribute_error_blocks_apply(self):
        items = [{
            "task_id": 1,
            "product_id": "100",
            "offer_id": "offer",
            "status": "imported",
            "errors": [{
                "level": "error",
                "attribute_id": repair.OZON_HASHTAG_ATTRIBUTE_ID,
                "message": "bad hashtag",
            }],
        }]

        self.assertEqual(len(repair.blocking_import_errors(items)), 1)

    def test_rejected_brand_like_api_terms_are_filtered(self):
        card = {
            "title": "Саундбар",
            "category_name": "",
            "current_intro": "Саундбар для телевизора.",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "саундбар филипс", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "саундбар", "count": 90, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, card["current_intro"], query_rows)
        tags = [item["tag"] for item in candidates]

        self.assertNotIn("#саундбарфилипс", tags)
        self.assertIn("#саундбардлятелевизора", tags)

    def test_card_specific_competitor_terms_are_filtered(self):
        deer_card = {"title": "шкура северный олень искусственная", "product_attributes": []}
        diving_card = {"title": "Маска для дайвинга", "product_attributes": []}
        pressure_washer_card = {"title": "Мойка высокого давления аккумуляторная", "product_attributes": []}

        self.assertFalse(repair.candidate_allowed_for_card(
            {"tag": "#шкурамедведянапол", "phrase": "шкура медведя на пол"},
            deer_card,
        ))
        self.assertFalse(repair.candidate_allowed_for_card(
            {"tag": "#маскааквалунг", "phrase": "маска аквалунг"},
            diving_card,
        ))
        self.assertFalse(repair.candidate_allowed_for_card(
            {"tag": "#канистрадлягсм", "phrase": "канистра для гсм"},
            pressure_washer_card,
        ))

    def test_marketing_newness_tag_is_filtered(self):
        self.assertEqual(repair._tag_policy_block_reason("#новинкамойка"), "marketing")

    def test_pressure_washer_fill_uses_washer_terms_not_car_accessories(self):
        card = {
            "title": "Мойка высокого давления аккумуляторная для автомобиля",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#мойкавысокогодавления", merged["final_tags"])
        self.assertIn("#беспроводнаямойка", merged["final_tags"])
        self.assertNotIn("#накладканаавтомобиль", merged["final_tags"])
        self.assertNotIn("#чехолнаавтомобиль", merged["final_tags"])

    def test_brand_api_terms_are_not_used_for_tags_or_intro(self):
        card = {
            "title": "Портативная акустика",
            "category_name": "",
            "current_intro": "Портативная акустика.",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "marshall колонка", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "портативная акустика", "count": 80, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, card["current_intro"], query_rows)
        merged = repair.merge_tags([], candidates, query_rows, card=card)
        intro = repair.build_intro(card, None, query_rows)

        self.assertNotIn("#marshallколонка", merged["final_tags"])
        self.assertIn("#портативнаяакустика", merged["final_tags"])
        self.assertNotIn("#саундбар", merged["final_tags"])
        self.assertNotIn("#звуковаяпанель", merged["final_tags"])
        self.assertNotIn("#колонкидлятелевизора", merged["final_tags"])
        self.assertNotIn("marshall", intro["final_intro"].casefold())

    def test_generated_fill_does_not_cross_basic_product_domains(self):
        skillet = {"title": "Сковорода блинная 24 см, литая, с антипригарным покрытием", "product_attributes": []}
        skillet_candidates = repair.filter_card_candidates(
            repair.tag_candidates(skillet, "", []),
            skillet,
        )
        skillet_tags = [item["tag"] for item in skillet_candidates]
        self.assertNotIn("#отпаривательручной", skillet_tags)
        self.assertNotIn("#разделочнаядоска", skillet_tags)

        kettle = {"title": "Электрочайник белый стеклянный с подсветкой", "product_attributes": []}
        kettle_candidates = repair.filter_card_candidates(
            repair.tag_candidates(kettle, "", []),
            kettle,
        )
        kettle_tags = [item["tag"] for item in kettle_candidates]
        self.assertNotIn("#светодиодныйфонарь", kettle_tags)
        self.assertNotIn("#светильникгроза", kettle_tags)

        sewing = {"title": "Швейная машинка электрическая с педалью мини", "product_attributes": []}
        sewing_candidates = repair.filter_card_candidates(
            repair.tag_candidates(sewing, "", []),
            sewing,
        )
        sewing_tags = [item["tag"] for item in sewing_candidates]
        self.assertNotIn("#швейнаямашинкадляавтомобиля", sewing_tags)
        self.assertNotIn("#машинкаэлектрическаядлягаража", sewing_tags)
        self.assertNotIn("музыкального оборудования", generated_intro.generated_intro(sewing))

    def test_water_filter_fill_does_not_use_fuel_or_camera_terms(self):
        card = {
            "title": "Фильтр для очистителя воды",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#фильтрдляводы", merged["final_tags"])
        self.assertNotIn("#канистрадлягсм", merged["final_tags"])
        self.assertNotIn("#картридждляфото", merged["final_tags"])
        self.assertNotIn("#винныйнабор", merged["final_tags"])
        self.assertNotIn("#кружкадляводы", merged["final_tags"])

    def test_step_platform_fill_does_not_use_toy_terms(self):
        card = {
            "title": "Степ-платформа для фитнеса и аэробики",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#степплатформа", merged["final_tags"])
        self.assertNotIn("#игрушкатрансформер", merged["final_tags"])
        self.assertNotIn("#степплатформадлядетей", merged["final_tags"])

    def test_thermal_label_fill_does_not_use_marketplace_brand_or_camera_terms(self):
        card = {
            "title": "Термоэтикетки 100 150 мм для доставки",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "проверь товар озон", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "этикетки для доставки", "count": 80, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, "", query_rows)
        merged = repair.merge_tags([], candidates, query_rows, card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#проверьтоварозон", merged["final_tags"])
        self.assertNotIn("#картридждляфото", merged["final_tags"])
        self.assertNotIn("#фильтрдляводы", merged["final_tags"])
        self.assertNotIn("#этикеткидлядоставкидляванной", merged["final_tags"])
        self.assertNotIn("#этикеткидлясклададлясклада", merged["final_tags"])
        self.assertIn("#этикеткидлядоставки", merged["final_tags"])

    def test_prohibited_drone_camera_card_is_skipped(self):
        card = {
            "title": "Радиоуправляемая игрушка X30 Pro Max 8K с камерой",
            "category_name": "",
            "current_intro": "Коптер с камерой.",
            "product_attributes": [],
        }

        self.assertEqual(repair.card_update_risk_reason(card, card["current_intro"]), "prohibited_drone_risk")

    def test_context_fill_does_not_build_nonsense_scene_phrases(self):
        card = {
            "title": "Наклейки чемпионат мира",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [{"id": 1, "values": [{"value": "для ванной гигиена"}]}],
        }

        candidates = repair.tag_candidates(card, "", [])
        tags = [item["tag"] for item in candidates]

        self.assertNotIn("#чемпионатмирадляванной", tags)
        self.assertNotIn("#чемпионатмирагигиена", tags)

    def test_massage_terms_do_not_force_hygiene_context(self):
        card = {
            "title": "Перкуссионный массажер для тела",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        tags = [item["tag"] for item in candidates]

        self.assertNotIn("#перкуссионныймассажергигиена", tags)

    def test_speedometer_terms_do_not_use_honda_or_toy_terms(self):
        card = {
            "title": "Спидометр для мотоцикла Honda DIO AF27 AF28",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "спидометр на скутер хонда дио", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "спидометр для скутера", "count": 80, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, "", query_rows)
        merged = repair.merge_tags([], candidates, query_rows, card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#спидометрнаскутерхондадио", merged["final_tags"])
        self.assertNotIn("#толокар", merged["final_tags"])
        self.assertNotIn("#спидометрдляскутерадлядетей", merged["final_tags"])
        self.assertNotIn("#спидометрдляскутерадляигры", merged["final_tags"])
        self.assertIn("#спидометрдляскутера", merged["final_tags"])

    def test_hair_dryer_terms_do_not_use_dyson_or_car_terms(self):
        card = {
            "title": "Фен для волос фнн 1000 Вт, кол-во насадок 5",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "дайсон стайлер аналог", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "фен для волос", "count": 80, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, "", query_rows)
        merged = repair.merge_tags([], candidates, query_rows, card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#дайсонстайлераналог", merged["final_tags"])
        self.assertNotIn("#накладканаавтомобиль", merged["final_tags"])
        self.assertNotIn("#перкуссионныймассажер", merged["final_tags"])
        self.assertNotIn("#защитасиденья", merged["final_tags"])
        self.assertNotIn("#защитарук", merged["final_tags"])
        self.assertNotIn("#рабочаязащита", merged["final_tags"])
        self.assertNotIn("#перчатки", merged["final_tags"])
        self.assertNotIn("#фенволосфнн", merged["final_tags"])
        self.assertIn("#фендляволос", merged["final_tags"])

    def test_fruit_bowl_terms_do_not_use_vikhr_brand_like_terms(self):
        card = {
            "title": "Фруктовая тарелка с эффектом вихря, зеленая керамическая миска",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "сушилка вихрь для овощей", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "миска для мытья фруктов зеленая", "count": 80, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, "", query_rows)
        merged = repair.merge_tags([], candidates, query_rows, card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#сушилкавихрьдляовощей", merged["final_tags"])
        self.assertNotIn("#вихря", merged["final_tags"])
        self.assertNotIn("#ларьдляовощей", merged["final_tags"])
        self.assertNotIn("#ящикдляовощей", merged["final_tags"])
        self.assertIn("#мискадлямытьяфруктовзеленая", merged["final_tags"])

    def test_unrelated_existing_storage_tags_are_not_preserved(self):
        card = {
            "title": "Инфракрасный обогреватель для теплиц с термостатом",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        existing = [
            "#ларьдляовощей",
            "#ящикдляовощей",
            "#контейнердляовощей",
            "#хранениенакухне",
            "#домашнеехранение",
        ]

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags(existing, candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#ларьдляовощей", merged["final_tags"])
        self.assertNotIn("#ящикдляовощей", merged["final_tags"])
        self.assertNotIn("#контейнердляовощей", merged["final_tags"])
        self.assertNotIn("#хранениенакухне", merged["final_tags"])

    def test_thermocontainer_does_not_preserve_mug_or_coffee_maker_tags(self):
        card = {
            "title": "Термоконтейнер для напитков из нержавеющей стали с краном",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        existing = ["#термокружкадляавтомобиля", "#кофеваркадляавто", "#кружкадляводы"]

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags(existing, candidates, [], card=card)

        self.assertNotIn("#термокружкадляавтомобиля", merged["final_tags"])
        self.assertNotIn("#кофеваркадляавто", merged["final_tags"])
        self.assertNotIn("#кружкадляводы", merged["final_tags"])

    def test_3d_dryer_does_not_preserve_kitchen_shelf_tags(self):
        card = {
            "title": "Сушильный бокс для 3d-печати, 4 позиции",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        existing = ["#держательдлякухни", "#полкадляспеций", "#кухонныйорганайзер"]

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags(existing, candidates, [], card=card)

        self.assertNotIn("#держательдлякухни", merged["final_tags"])
        self.assertNotIn("#полкадляспеций", merged["final_tags"])
        self.assertNotIn("#кухонныйорганайзер", merged["final_tags"])

    def test_corrupted_intro_does_not_seed_generated_tags(self):
        card = {
            "title": "Сушильный бокс для 3d-печати, 4 позиции",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        polluted_intro = "Кресло для отдыха. Камера видеонаблюдения. Тачка садовая."

        candidates = repair.tag_candidates(card, polluted_intro, [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#сушильныйбокс", merged["final_tags"])
        self.assertNotIn("#креслодляотдыха", merged["final_tags"])
        self.assertNotIn("#камеравидеонаблюдения", merged["final_tags"])
        self.assertNotIn("#тачкасадовая", merged["final_tags"])

    def test_heater_fill_does_not_use_decor_or_garden_cart_terms(self):
        card = {
            "title": "Инфракрасный обогреватель для теплиц с термостатом",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [{"id": 1, "values": [{"value": "настенный монтаж"}]}],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#инфракрасныйобогреватель", merged["final_tags"])
        self.assertNotIn("#декоративнаярамка", merged["final_tags"])
        self.assertNotIn("#тачкасадовая", merged["final_tags"])

    def test_plant_holder_does_not_create_garden_cart_tags(self):
        card = {
            "title": "Держатель для кашпо 230cm / Кронштейн для кашпо / держатель для цветов",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#держателькашпо", merged["final_tags"])
        self.assertIn("#подставкадляцветов", merged["final_tags"])
        self.assertNotIn("#тачкасадовая", merged["final_tags"])
        self.assertNotIn("#садоваятележка", merged["final_tags"])

    def test_garden_cart_keeps_garden_cart_tags(self):
        card = {
            "title": "Тележка садовая складная для дачи",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#садоваятележка", merged["final_tags"])
        self.assertIn("#тачкадлядачи", merged["final_tags"])

    def test_luggage_cup_holder_does_not_create_mug_tags(self):
        card = {
            "title": "Женский чемодан на колесах с USB-портом и держателем стакана",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#чемодан", merged["final_tags"])
        self.assertNotIn("#термокружкасручкой", merged["final_tags"])
        self.assertNotIn("#кружкадлякофе", merged["final_tags"])
        self.assertNotIn("#рюкзак", merged["final_tags"])
        self.assertNotIn("#рюкзакдляпоездки", merged["final_tags"])

    def test_adult_product_is_skipped_for_moderation_risk(self):
        card = {
            "title": "Мастурбатор многоразовый",
            "current_intro": "",
        }

        self.assertEqual(repair.card_update_risk_reason(card, "Описание товара."), "adult_product_moderation_risk")

    def test_drone_tags_are_skipped_for_moderation_risk(self):
        self.assertEqual(
            repair.final_tag_moderation_risk_reason(["#пульт", "#квадрокоптер", "#дрон", "#миникоптер"]),
            "prohibited_drone_risk",
        )
        self.assertEqual(
            repair.final_tag_moderation_risk_reason(["#мусорноеведронаножках", "#мусорноеведронаколесиках"]),
            "",
        )
        self.assertEqual(
            repair.final_tag_moderation_risk_reason(["#урнадляпрахаживотных", "#мемориалдляпитомца"]),
            "prohibited_memorial_urn_risk",
        )
        self.assertEqual(
            repair.card_update_risk_reason(
                {"title": "Радиоуправляемая игрушка X30 Pro Max 8K, складная с камерой 8K"},
                "Описание товара.",
            ),
            "prohibited_drone_risk",
        )
        self.assertEqual(
            repair.card_update_risk_reason({"title": "Урна для праха животных"}, "Для питомца."),
            "prohibited_memorial_urn_risk",
        )

    def test_remote_control_fill_does_not_create_other_domain_tags(self):
        card = {
            "title": "Пульт",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#пультуправления", merged["final_tags"])
        self.assertNotIn("#подогреватель", merged["final_tags"])
        self.assertNotIn("#степпер", merged["final_tags"])
        self.assertNotIn("#геодезическоеоборудование", merged["final_tags"])

    def test_intro_term_requires_product_support_even_for_api_query(self):
        card = {
            "title": "Сумка для ноутбука 15.6, мужская сумка для документов",
            "current_intro": "",
            "product_attributes": [],
        }
        rows = [
            {"query": "ремен для мужской часи swissoak", "source_kind": repair.API_QUERY_SOURCE, "count": 10},
            {"query": "сумка для документов мужская двухсторонняя подмышками", "source_kind": repair.API_QUERY_SOURCE, "count": 9},
            {"query": "сумка для ноутбука мужская", "source_kind": repair.API_QUERY_SOURCE, "count": 8},
        ]

        query, row = repair.select_intro_term(card, "Сумка для ноутбука 15.6, мужская сумка для документов.", rows)

        self.assertEqual(query, "сумка для ноутбука мужская")
        self.assertEqual(row["count"], 8)

    def test_charger_drops_switch_brand_tags(self):
        card = {
            "title": "Зарядное устройство черный кабель",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        existing = ["#KeyCharger", "#QuickCharge", "#Switch", "#зарядныйблок"]

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags(existing, candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#зарядныйблок", merged["final_tags"])
        self.assertNotIn("#KeyCharger", merged["final_tags"])
        self.assertNotIn("#QuickCharge", merged["final_tags"])
        self.assertNotIn("#Switch", merged["final_tags"])
        self.assertNotIn("#неткабельдлядома", merged["final_tags"])
        self.assertFalse(any("нет" in tag.casefold() for tag in merged["final_tags"]))
        self.assertTrue(repair.candidate_allowed_for_card(
            {"tag": "#кабинет", "phrase": "кабинет"},
            {"title": "Коллекционная фигурка для кабинета"},
        ))
        self.assertFalse(repair.candidate_allowed_for_card(
            {"tag": "#резинанет", "phrase": "резина нет"},
            {"title": "Коврик для мышки"},
        ))
        self.assertFalse(repair.candidate_allowed_for_card(
            {"tag": "#камерасинтернетом", "phrase": "камера с интернетом"},
            {"title": "Автомагнитола 2 DIN Android, AHD камера"},
        ))

    def test_non_adult_card_drops_intimate_existing_tag(self):
        card = {
            "title": "Смеситель для стиральной машины с двумя выходами",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        existing = ["#интимнаязонамашинкаженки", "#смесительдлямашины"]

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags(existing, candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#интимнаязонамашинкаженки", merged["final_tags"])
        self.assertIn("#смесительдлямашины", merged["final_tags"])

    def test_tool_holder_does_not_create_kitchen_spice_shelf_tags(self):
        card = {
            "title": "Магнитный коврик для инструмента, набор из 3 держателей для крепежа и деталей",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#держательдлякухни", merged["final_tags"])
        self.assertNotIn("#полкадляспеций", merged["final_tags"])
        self.assertNotIn("#кухонныйорганайзер", merged["final_tags"])
        self.assertNotIn("#магнитныйковрикдлядетей", merged["final_tags"])
        self.assertNotIn("#магнитныйковрикдляигры", merged["final_tags"])

    def test_grill_fill_does_not_use_hygiene_context(self):
        card = {
            "title": "Компактный угольный гриль из нержавеющей стали для дачи",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [{"id": 1, "values": [{"value": "уход после использования"}]}],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertIn("#угольныйгриль", merged["final_tags"])
        self.assertNotIn("#грильнержавеющейсталидляухода", merged["final_tags"])
        self.assertNotIn("#нержавеющейсталидачидляванной", merged["final_tags"])

    def test_vacuum_brush_water_container_does_not_create_storage_box_tags(self):
        card = {
            "title": "Турбощетка с водяным контейнером для пылесоса",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#контейнердляхранения", merged["final_tags"])
        self.assertNotIn("#контейнердлякухни", merged["final_tags"])

    def test_car_dashcam_does_not_create_security_camera_tags(self):
        card = {
            "title": "Тахограф автомобильный, Автомобильный видеорегистратор с тремя камерами",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        candidates = repair.tag_candidates(card, "", [])
        merged = repair.merge_tags([], candidates, [], card=card)

        self.assertEqual(len(merged["final_tags"]), 30)
        self.assertNotIn("#камеравидеонаблюдения", merged["final_tags"])
        self.assertNotIn("#охраннаякамера", merged["final_tags"])

    def test_bag_and_shelf_categories_fill_to_thirty(self):
        bag = {
            "title": "Сумка для ноутбука 15.6, мужская или женская сумка для документов",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        shelf = {
            "title": "Полка Напольная Прямая, 30х30х92 см",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        bag_merged = repair.merge_tags([], repair.tag_candidates(bag, "", []), [], card=bag)
        shelf_merged = repair.merge_tags([], repair.tag_candidates(shelf, "", []), [], card=shelf)

        self.assertEqual(len(bag_merged["final_tags"]), 30)
        self.assertIn("#сумкадляноутбука", bag_merged["final_tags"])
        self.assertEqual(len(shelf_merged["final_tags"]), 30)
        self.assertIn("#полканапольная", shelf_merged["final_tags"])

    def test_projector_and_back_support_do_not_use_unrelated_templates(self):
        projector = {
            "title": "Прожектор светодиодный уличный влагозащищенный от сети 50Вт",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }
        support = {
            "title": "Поддержка для спины 38x58 см",
            "category_name": "",
            "current_intro": "",
            "product_attributes": [],
        }

        projector_merged = repair.merge_tags([], repair.tag_candidates(projector, "", []), [], card=projector)
        support_merged = repair.merge_tags([], repair.tag_candidates(support, "", []), [], card=support)

        self.assertEqual(len(projector_merged["final_tags"]), 30)
        self.assertNotIn("#пропиткадляобуви", projector_merged["final_tags"])
        self.assertEqual(len(support_merged["final_tags"]), 30)
        self.assertNotIn("#министиральнаямашина", support_merged["final_tags"])

    def test_tefal_brand_terms_are_not_used(self):
        card = {
            "title": "Соковыжималка электрическая для цитрусовых",
            "category_name": "",
            "current_intro": "Соковыжималка для цитрусовых.",
            "product_attributes": [],
        }
        query_rows = [
            {"query": "соковыжималка тефаль", "count": 100, "source_kind": repair.API_QUERY_SOURCE},
            {"query": "соковыжималка для цитрусовых", "count": 80, "source_kind": repair.API_QUERY_SOURCE},
        ]

        candidates = repair.tag_candidates(card, card["current_intro"], query_rows)
        merged = repair.merge_tags([], candidates, query_rows, card=card)

        self.assertNotIn("#соковыжималкатефаль", merged["final_tags"])
        self.assertIn("#соковыжималкадляцитрусовых", merged["final_tags"])


if __name__ == "__main__":
    unittest.main()
