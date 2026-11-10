"""氣泡幾何與繪圖的單元測試（不開 GUI、不寫任何真實狀態）。

用法：python -m unittest tests.test_bubble   （在專案根目錄）
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import pet_bubble as pb  # noqa: E402

WORK = (0, 0, 1920, 1040)


def mono(s: str) -> int:
    return 10 * len(s)  # 每個字元 10px


class WrapTests(unittest.TestCase):
    def test_cjk_wraps_per_char(self):
        lines = pb.wrap_text("一二三四五六七八九十", mono, 50)
        self.assertEqual(lines, ["一二三四五", "六七八九十"])

    def test_latin_wraps_at_words(self):
        lines = pb.wrap_text("hello world again", mono, 100)
        self.assertEqual(lines, ["hello", "world", "again"])
        for ln in lines:
            self.assertLessEqual(mono(ln), 100)

    def test_long_word_hard_split_and_newline(self):
        lines = pb.wrap_text("abcdefghij\nxy", mono, 40)
        self.assertEqual(lines, ["abcd", "efgh", "ij", "xy"])

    def test_empty(self):
        self.assertEqual(pb.wrap_text("", mono, 100), [""])


class HeadTests(unittest.TestCase):
    def test_head_fractions_from_bbox(self):
        cx, top = pb.head_fractions((100, 200, 300, 1000), 400, 1000)
        self.assertAlmostEqual(cx, 0.5)
        self.assertAlmostEqual(top, 0.2)

    def test_head_point_scales_with_window(self):
        self.assertEqual(pb.head_point(100, 50, 200, 400, 0.5, 0.25), (200, 150))
        self.assertEqual(pb.head_point(100, 50, 400, 800, 0.5, 0.25), (300, 250))  # 放大後頭頂跟著移動


class PlaceTests(unittest.TestCase):
    def test_above_head_with_tail_pointing_at_head(self):
        size = (200, 80)
        pos = pb.place_bubble((960, 600), size, (800, 560, 1100, 900), WORK)
        self.assertEqual(pos["side"], "above")
        self.assertEqual(pos["y"] + size[1] + pb.BUBBLE_GAP, 600)
        self.assertEqual(pos["x"] + pos["tail_dx"], 960)

    def test_clamped_to_screen_horizontally_tail_still_near_head(self):
        size = (200, 80)
        pos = pb.place_bubble((10, 600), size, (0, 560, 300, 900), WORK)
        self.assertEqual(pos["side"], "above")
        self.assertGreaterEqual(pos["x"], WORK[0])
        self.assertGreaterEqual(pos["tail_dx"], pb.BUBBLE_RADIUS + pb.BUBBLE_TAIL_HALF_W)
        pos = pb.place_bubble((1915, 600), size, (1700, 560, 1920, 900), WORK)
        self.assertLessEqual(pos["x"] + size[0], WORK[2])
        self.assertLessEqual(pos["tail_dx"], size[0])

    def test_no_room_above_goes_beside(self):
        size = (200, 80)
        pos = pb.place_bubble((500, 30), size, (400, 20, 700, 400), WORK)
        self.assertEqual(pos["side"], "right")
        self.assertIsNone(pos["tail_dx"])
        self.assertGreaterEqual(pos["x"], 700)
        self.assertGreaterEqual(pos["y"], WORK[1])

    def test_beside_falls_back_to_left_at_right_edge(self):
        size = (200, 80)
        pos = pb.place_bubble((1800, 30), size, (1700, 20, 1900, 400), WORK)
        self.assertEqual(pos["side"], "left")
        self.assertLessEqual(pos["x"] + size[0], 1700)

    def test_always_inside_work_area(self):
        for head in [(0, 0), (1919, 1039), (960, 5), (5, 1000)]:
            pos = pb.place_bubble(head, (260, 120), (head[0] - 100, head[1], head[0] + 100, head[1] + 300), WORK)
            self.assertGreaterEqual(pos["x"], WORK[0])
            self.assertGreaterEqual(pos["y"], WORK[1])
            self.assertLessEqual(pos["x"] + 260, WORK[2])
            self.assertLessEqual(pos["y"] + 120, WORK[3])

    def test_work_area_with_offset_monitor(self):
        work = (-1920, 0, 0, 1040)  # 副螢幕在左邊
        pos = pb.place_bubble((-100, 500), (200, 80), (-300, 460, 0, 800), work)
        self.assertGreaterEqual(pos["x"], -1920)
        self.assertLessEqual(pos["x"] + 200, 0)


class RenderTests(unittest.TestCase):
    def test_render_size_and_transparent_corner(self):
        import pet_bubble_render as r
        from pet_shell import MAGIC_TRANSPARENT_COLOR

        w, h = r.bubble_image_size("你好，今天天氣真好" * 5)
        img = r.render_bubble("你好，今天天氣真好" * 5, False, 40)
        self.assertEqual(img.size, (w, h))
        self.assertLessEqual(w, pb.BUBBLE_MAX_TEXT_WIDTH + 2 * pb.BUBBLE_PAD_X)
        magenta = tuple(int(MAGIC_TRANSPARENT_COLOR[i:i + 2], 16) for i in (1, 3, 5))
        self.assertEqual(img.getpixel((0, 0)), magenta)  # 圓角外面是色鍵色
        self.assertEqual(img.getpixel((w // 2, h // 2)) != magenta, True)
        # 尾巴尖端附近有畫（不是色鍵色），沒有尾巴時同位置是色鍵色。
        self.assertNotEqual(img.getpixel((40, h - 3)), magenta)
        no_tail = r.render_bubble("你好，今天天氣真好" * 5, False, None)
        self.assertEqual(no_tail.getpixel((40, h - 3)), magenta)

    def test_status_style_differs(self):
        import pet_bubble_render as r

        a = r.render_bubble("x", False, None)
        b = r.render_bubble("x", True, None)
        self.assertNotEqual(a.getpixel((a.width // 2, 12)), b.getpixel((b.width // 2, 12)))


if __name__ == "__main__":
    unittest.main()
