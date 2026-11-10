"""pet_anim_fx：程式產生的身體動作（純函式）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "desktop_pet"))

from PIL import Image  # noqa: E402

from pet_anim_fx import IDENTITY, apply_fx, fx_for  # noqa: E402


class FxTest(unittest.TestCase):
    def test_idle_and_unknown_have_no_fx(self):
        self.assertEqual(fx_for("idle", 1.3), IDENTITY)
        self.assertEqual(fx_for("drink-tea", 0.5), IDENTITY)

    def test_walk_sways_both_ways(self):
        angles = {fx_for("walk", i * 0.05).angle for i in range(40)}
        self.assertTrue(max(angles) > 0 > min(angles))

    def test_land_squashes_then_settles(self):
        self.assertLess(fx_for("land", 0.0).sy, 0.9)
        self.assertEqual(fx_for("land", 2.0), IDENTITY)

    def test_poke_shake_decays(self):
        self.assertEqual(fx_for("poke", 3.0), IDENTITY)

    def test_drag_pivots_on_top(self):
        self.assertEqual(fx_for("drag", 0.2).pivot, "top")

    def test_values_are_quantized_for_caching(self):
        fxs = {fx_for("walk", i * 0.001) for i in range(2000)}
        self.assertLess(len(fxs), 200)

    def test_apply_keeps_size_and_identity_is_noop(self):
        img = Image.new("RGBA", (40, 60), (255, 0, 0, 255))
        self.assertIs(apply_fx(img, IDENTITY), img)
        for key, t in (("walk", 0.1), ("land", 0.0), ("drag", 0.2), ("poke", 0.04)):
            self.assertEqual(apply_fx(img, fx_for(key, t)).size, (40, 60))

    def test_land_squash_keeps_feet_on_baseline(self):
        img = Image.new("RGBA", (40, 60), (0, 0, 0, 0))
        img.paste((255, 0, 0, 255), (10, 0, 30, 60))
        out = apply_fx(img, fx_for("land", 0.0))
        bbox = out.getbbox()
        self.assertEqual(bbox[3], 60)  # 腳底仍貼底
        self.assertGreater(bbox[1], 0)  # 頭頂被壓低


if __name__ == "__main__":
    unittest.main()
