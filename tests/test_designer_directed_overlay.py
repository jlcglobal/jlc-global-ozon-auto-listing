import unittest

from PIL import Image, ImageChops

from scripts.designer_directed_overlay import render_overlay_plan


def instruction(text: str, priority: int, y: float) -> dict:
    return {
        "role": "headline" if priority == 1 else "benefit",
        "text": text,
        "box": [0.06, y, 0.56, 0.12],
        "font_size_ratio": 0.045 if priority == 1 else 0.027,
        "font_weight": "bold",
        "text_color": "#1F2930",
        "accent_color": "#708C58",
        "background_style": "none",
        "background_color": "#F5F1E8",
        "accent_style": "top_line" if priority == 1 else "check_icon",
        "align": "left",
        "vertical_align": "middle",
        "priority": priority,
    }


class DesignerDirectedOverlayTests(unittest.TestCase):
    def test_exact_designer_plan_renders_without_changing_ratio(self):
        image = Image.new("RGB", (900, 1200), "white")
        texts = ["КОНТЕЙНЕР ДЛЯ ХРАНЕНИЯ", "ПРОЗРАЧНЫЙ КОРПУС"]
        result = render_overlay_plan(image, texts, [
            instruction(texts[0], 1, 0.06),
            instruction(texts[1], 2, 0.24),
        ])
        self.assertEqual(result.size, (900, 1200))
        self.assertIsNotNone(ImageChops.difference(result, image).getbbox())

    def test_missing_or_reordered_copy_is_a_hard_failure(self):
        image = Image.new("RGB", (900, 1200), "white")
        with self.assertRaisesRegex(ValueError, "match every exact Russian text"):
            render_overlay_plan(image, ["ПЕРВЫЙ", "ВТОРОЙ"], [
                instruction("ВТОРОЙ", 1, 0.06),
                instruction("ПЕРВЫЙ", 2, 0.24),
            ])

    def test_non_three_by_four_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exact 3:4"):
            render_overlay_plan(
                Image.new("RGB", (1000, 1000), "white"),
                ["ТЕКСТ"],
                [instruction("ТЕКСТ", 1, 0.06)],
            )


if __name__ == "__main__":
    unittest.main()
