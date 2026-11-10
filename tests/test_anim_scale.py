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


class WideAndSharedTest(unittest.TestCase):
    def test_wide_canvas_grows_evenly_around_center(self):
        from pet_anim_frames import wide_canvas
        imgs = [_block(400, 100, (0, 0, 400, 100))]
        canvas, cx = wide_canvas(imgs, 100, (100, 200), 50)
        self.assertEqual(canvas, (400, 200))
        self.assertEqual(cx, 200)  # 原中心 50 + 往左撐開的 150

    def test_wide_canvas_unchanged_when_fits(self):
        from pet_anim_frames import wide_canvas
        canvas, cx = wide_canvas([_block(50, 100, (0, 0, 50, 100))], 100, (100, 200), 50)
        self.assertEqual((canvas, cx), ((100, 200), 50))

    def test_shared_bbox_keeps_scale_between_sets(self):
        tall = _block(100, 200, (40, 0, 60, 200))
        short = _block(100, 200, (40, 100, 60, 200))
        from pet_anim_frames import union_bbox
        ub = union_bbox([tall, short])
        out = normalize_set([short], 100, (100, 200), 50, 200, ub)[0]
        b = out.getbbox()
        self.assertAlmostEqual(b[3] - b[1], 50, delta=4)  # 跟 tall 同比例縮：約一半高（縮圖邊緣有半透明）


class TalkScheduleTest(unittest.TestCase):
    def test_talking_does_not_push_back_occasional(self):
        from pet_anim import AFTER_TALK_SEC, AnimController
        from pet_anim_data import AnimConfig, AnimSet
        idle = AnimSet("idle", (Path("x"),), 8, True, "front", (4, 7), 1)
        tea = AnimSet("tea", (Path("x"),), 8, True, "front", (4, 7), 1)
        cfg = AnimConfig("idle", (20.0, 20.0), {"idle": idle}, {"tea": tea})
        c = AnimController(cfg)
        c.step(0.0, False, False)  # 排在 20 秒
        c.step(15.0, True, False)  # 說話
        self.assertEqual(c._next_occ, 20.0)  # 沒被往後推
        c.step(19.0, True, False)
        self.assertEqual(c._next_occ, 19.0 + AFTER_TALK_SEC)  # 只保證講完隔一下
