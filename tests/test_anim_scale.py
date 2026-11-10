"""pet_anim_frames.normalize_set：太寬自動縮進畫布、不超出左右邊。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "desktop_pet"))

from PIL import Image  # noqa: E402

from pet_anim_frames import normalize_set  # noqa: E402


def _block(w, h, box):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), box)
    return im


class ScaleTest(unittest.TestCase):
    def test_tall_set_uses_target_height(self):
        out = normalize_set([_block(100, 200, (40, 0, 60, 200))], 150, (100, 200), 50, 200)[0]
        b = out.getbbox()
        self.assertEqual(b[3] - b[1], 150)
        self.assertEqual(b[3], 200)  # 腳底對齊基準線

    def test_wide_set_shrinks_to_fit_canvas(self):
        out = normalize_set([_block(400, 100, (0, 0, 400, 100))], 100, (100, 200), 50, 200)[0]
        b = out.getbbox()
        self.assertEqual((b[0], b[2]), (0, 100))  # 整個放得進來，沒被切
        self.assertLess(b[3] - b[1], 100)  # 為了放得進來而縮小

    def test_off_center_does_not_clip(self):
        out = normalize_set([_block(100, 200, (0, 0, 80, 200))], 200, (100, 200), 10, 200)[0]
        b = out.getbbox()
        self.assertEqual(b[0], 0)
        self.assertEqual(b[2] - b[0], 80)


if __name__ == "__main__":
    unittest.main()
