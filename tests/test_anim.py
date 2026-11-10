"""動畫載入、幀正規化、狀態機的單元測試（用暫存資料夾裡的小張合成 PNG，不碰真實素材）。

用法：python -m unittest tests.test_anim   （在專案根目錄）
"""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import pet_anim as pa  # noqa: E402
import pet_anim_data as pd  # noqa: E402
import pet_anim_frames as pf  # noqa: E402


def make_png(path: Path, canvas, box, color=(255, 0, 0, 255)):
    """在 canvas 大小的透明圖上，於 box=(x0,y0,x1,y1) 畫一塊不透明矩形。"""
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), color), (box[0], box[1]))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        for name in ("a0", "a1", "w0", "w1", "t0"):
            make_png(self.dir / "frames" / f"{name}.png", (40, 60), (5, 5, 25, 55))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, data):
        (self.dir / "animations.json").write_text(json.dumps(data), encoding="utf-8")

    def test_absent_returns_none(self):
        self.assertIsNone(pd.load_animations(self.dir))

    def test_valid_config(self):
        self.write({
            "states": {
                "idle": {"frames": ["frames/a0.png", "frames/a1.png"], "fps": 4},
                "walk": {"frames": ["frames/w0.png", "frames/w1.png"], "fps": 12, "native_facing": "left"},
            },
            "occasional": {"tea": {"frames": ["frames/t0.png"], "loop": False, "weight": 2}},
            "occasional_interval_sec": [10, 20],
        })
        cfg = pd.load_animations(self.dir)
        self.assertEqual(set(cfg.states), {"idle", "walk"})
        self.assertEqual(cfg.states["walk"].native_facing, "left")
        self.assertEqual(cfg.states["idle"].fps, 4)
        self.assertEqual(cfg.interval, (10.0, 20.0))
        self.assertFalse(cfg.occasional["tea"].loop)
        self.assertEqual(cfg.occasional["tea"].weight, 2)
        self.assertTrue(all(p.is_file() for p in cfg.states["idle"].frames))

    def test_missing_idle_disables(self):
        self.write({"states": {"walk": {"frames": ["frames/w0.png"]}}})
        self.assertIsNone(pd.load_animations(self.dir))

    def test_bad_set_is_skipped_not_fatal(self):
        self.write({
            "states": {"idle": {"frames": ["frames/a0.png"]}, "walk": {"frames": ["frames/nope.png"]}},
            "occasional": {"x": {"frames": ["../outside.png"]}, "y": {"frames": ["frames/t0.png"], "fps": 0}},
        })
        cfg = pd.load_animations(self.dir)
        self.assertEqual(set(cfg.states), {"idle"})
        self.assertEqual(cfg.occasional, {})

    def test_broken_json(self):
        (self.dir / "animations.json").write_text("{oops", encoding="utf-8")
        self.assertIsNone(pd.load_animations(self.dir))


class NormalizeTests(unittest.TestCase):
    def test_height_baseline_center_match_across_canvas_sizes(self):
        # 兩組畫布大小、人物大小都不同，正規化後可見身高、腳底、水平中心都要一致。
        canvas = (200, 300)
        target_h, cx, base = 120, 100, 280
        small = [Image.new("RGBA", (40, 60), (0, 0, 0, 0)) for _ in range(2)]
        for im in small:
            im.paste(Image.new("RGBA", (10, 30), (255, 0, 0, 255)), (5, 20))   # bbox (5,20,15,50)
        big = [Image.new("RGBA", (400, 600), (0, 0, 0, 0)) for _ in range(2)]
        for im in big:
            im.paste(Image.new("RGBA", (60, 240), (0, 255, 0, 255)), (100, 300))  # bbox (100,300,160,540)

        for frames in (pf.normalize_set(small, target_h, canvas, cx, base),
                       pf.normalize_set(big, target_h, canvas, cx, base)):
            self.assertEqual(len(frames), 2)
            for f in frames:
                self.assertEqual(f.size, canvas)
                x0, y0, x1, y1 = f.split()[-1].point(lambda a: 255 if a > 200 else 0).getbbox()
                self.assertEqual(y1, base)                       # 腳底在同一條基準線
                self.assertEqual(y1 - y0, target_h)              # 可見身高相同
                self.assertLessEqual(abs((x0 + x1) / 2 - cx), 1)  # 水平置中

    def test_intra_set_motion_preserved(self):
        # 組內第二幀的手伸出去（外框變寬），用聯集裁切，所以第一幀不應該被拉伸或位移。
        a = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        a.paste(Image.new("RGBA", (20, 60), (255, 0, 0, 255)), (40, 30))
        b = a.copy()
        b.paste(Image.new("RGBA", (30, 10), (255, 0, 0, 255)), (60, 40))
        fa, fb = pf.normalize_set([a, b], 60, (100, 100), 50, 90)
        self.assertEqual(fa.split()[-1].getbbox()[3], fb.split()[-1].getbbox()[3])
        self.assertLess(fa.split()[-1].getbbox()[2], fb.split()[-1].getbbox()[2])

    def test_mirror_and_needs_mirror(self):
        self.assertTrue(pf.needs_mirror("left", "right"))
        self.assertFalse(pf.needs_mirror("left", "left"))
        self.assertFalse(pf.needs_mirror("front", "right"))
        self.assertFalse(pf.needs_mirror("front", "left"))
        im = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
        im.putpixel((0, 0), (255, 0, 0, 255))
        self.assertEqual(pf.mirror_all([im])[0].getpixel((3, 0)), (255, 0, 0, 255))

    def test_target_geometry_from_static_avatar(self):
        import pet_avatar_metrics as am
        import pet_shell

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            make_png(d / "mouth_closed.png", (200, 400), (60, 100, 140, 380))
            make_png(d / "mouth_open.png", (200, 400), (60, 100, 140, 380))
            am._cache.clear()
            pet_shell._avatar_source_cache.pop(d, None)
            m = am.avatar_metrics(d)
            self.assertAlmostEqual(m["top_frac"], 100 / m["crop_h"])
            canvas, height, cx, base = pf.target_geometry(d, 100)
            s = 100 / m["crop_w"]
            self.assertAlmostEqual(height, 280 * s)
            self.assertAlmostEqual(base, 380 * s)
            self.assertEqual(canvas, (100, round(m["crop_h"] * s)))
            pet_shell._avatar_source_cache.pop(d, None)
            am._cache.clear()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        for n in ("a", "w", "t", "b"):
            make_png(d / f"{n}.png", (10, 10), (2, 2, 8, 8))
        (d / "animations.json").write_text(json.dumps({
            "occasional_interval_sec": [20, 60],
            "states": {"idle": {"frames": ["a.png"] * 4, "fps": 4}, "walk": {"frames": ["w.png"] * 2, "fps": 10}},
            "occasional": {
                "tea": {"frames": ["t.png"] * 3, "fps": 2, "loop": True, "duration_sec": [4, 4]},
                "bow": {"frames": ["b.png"] * 5, "fps": 5, "loop": False},
            },
        }), encoding="utf-8")
        self.cfg = pd.load_animations(d)
        self.ctl = pa.AnimController(self.cfg, random.Random(1))

    def tearDown(self):
        self._tmp.cleanup()

    def test_idle_loops(self):
        self.assertEqual(self.ctl.step(0.0, False, False), ("idle", 0))
        self.assertEqual(self.ctl.step(0.26, False, False), ("idle", 1))
        self.assertEqual(self.ctl.step(1.01, False, False), ("idle", 0))  # 4 幀 4fps，1 秒回到頭

    def test_walk_only_while_moving(self):
        self.ctl.step(0.0, False, False)
        self.assertEqual(self.ctl.step(1.0, False, True)[0], "walk")
        self.assertEqual(self.ctl.step(1.5, False, False)[0], "idle")

    def test_talking_pauses_and_cancels(self):
        self.ctl.step(0.0, False, False)
        self.assertIsNone(self.ctl.step(100.0, True, False))  # 到點也不演小動作
        self.assertEqual(self.ctl.step(100.1, False, False)[0], "idle")  # 講完不會馬上跳小動作

    def test_occasional_fires_in_interval_and_ends(self):
        self.ctl.step(0.0, False, False)
        t = 0.0
        key = "idle"
        while key == "idle" and t < 61:
            t += 0.5
            key = self.ctl.step(t, False, False)[0]
        self.assertIn(key, ("tea", "bow"))
        self.assertGreaterEqual(t, 20)
        self.assertLessEqual(t, 60.5)
        for _ in range(40):  # 小動作都會在幾秒內結束並回到 idle
            t += 0.5
            key = self.ctl.step(t, False, False)[0]
        self.assertEqual(key, "idle")

    def test_no_occasional_when_popup_open_or_walking_interrupts(self):
        self.ctl.step(0.0, False, False)
        self.assertEqual(self.ctl.step(500.0, False, False, allow_occasional=False)[0], "idle")
        # 強制進入小動作，再讓它走路：小動作被打斷。
        self.ctl._next_occ = 0
        key = self.ctl.step(501.0, False, False)[0]
        self.assertIn(key, ("tea", "bow"))
        self.assertEqual(self.ctl.step(501.5, False, True)[0], "walk")

    def test_non_loop_holds_last_frame(self):
        self.ctl.step(0.0, False, False)
        self.ctl.key, self.ctl._start, self.ctl._end = "bow", 10.0, 11.0
        self.assertEqual(self.ctl.frame_index(10.9), 4)
        self.assertEqual(self.ctl.frame_index(50.0), 4)


if __name__ == "__main__":
    unittest.main()
