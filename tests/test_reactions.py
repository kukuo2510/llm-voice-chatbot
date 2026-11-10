"""觸發式反應（reactions）：解析、優先順序、戳臉升級、落地、臉部區的單元測試（暫存資料夾合成 PNG）。

用法：python -m unittest tests.test_reactions   （在專案根目錄）
"""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import pet_anim as pa  # noqa: E402
import pet_anim_data as pd  # noqa: E402
import pet_avatar_metrics as am  # noqa: E402


def make_png(path: Path, canvas=(10, 10), box=(2, 2, 8, 8)):
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (255, 0, 0, 255)), (box[0], box[1]))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


REACTIONS = {
    "poke": {"frames": ["p.png"] * 8, "fps": 12, "loop": False, "native_facing": "front",
             "sequence_sec": [0.75, 1.0, 2.5], "reset_after_sec": 2.0, "cooldown_sec": 0.3},
    "drag": {"frames": ["d.png"], "fps": 8, "loop": False, "native_facing": "left"},
    "fall": {"frames": ["f.png"] * 4, "fps": 8, "loop": True, "native_facing": "left"},
    "land": {"frames": ["l.png"] * 6, "fps": 10, "loop": False, "native_facing": "front"},
}


def build(tmp: Path, reactions):
    for n in ("a", "w", "t", "p", "d", "f", "l"):
        make_png(tmp / f"{n}.png")
    data = {
        "occasional_interval_sec": [20, 60],
        "states": {"idle": {"frames": ["a.png"] * 4, "fps": 4}, "walk": {"frames": ["w.png"] * 2, "fps": 10}},
        "occasional": {"tea": {"frames": ["t.png"] * 3, "fps": 2, "loop": True, "duration_sec": [4, 4]}},
    }
    if reactions is not None:
        data["reactions"] = reactions
    (tmp / "animations.json").write_text(json.dumps(data), encoding="utf-8")
    return pd.load_animations(tmp)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_without_reactions_unchanged(self):
        cfg = build(self.dir, None)
        self.assertEqual(cfg.reactions, {})
        self.assertEqual(cfg.poke_sequence, pd.DEFAULT_POKE_SEQUENCE)
        self.assertEqual(set(cfg.states), {"idle", "walk"})

    def test_with_reactions(self):
        cfg = build(self.dir, REACTIONS)
        self.assertEqual(set(cfg.reactions), {"poke", "drag", "fall", "land"})
        self.assertEqual(cfg.poke_sequence, (0.75, 1.0, 2.5))
        self.assertEqual(cfg.poke_reset_sec, 2.0)
        self.assertEqual(cfg.poke_cooldown_sec, 0.3)
        self.assertEqual(cfg.reactions["drag"].native_facing, "left")
        self.assertTrue(cfg.reactions["fall"].loop)
        self.assertFalse(cfg.reactions["poke"].loop)

    def test_invalid_item_skipped(self):
        bad = dict(REACTIONS)
        bad["fall"] = {"frames": ["nope.png"]}
        bad["land"] = "oops"
        cfg = build(self.dir, bad)
        self.assertEqual(set(cfg.reactions), {"poke", "drag"})

    def test_reactions_not_object_ignored(self):
        cfg = build(self.dir, [1, 2])
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.reactions, {})


class ReactionControllerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = build(Path(self._tmp.name), REACTIONS)
        self.ctl = pa.AnimController(self.cfg, random.Random(1))
        self.ctl.step(0.0, False, False)

    def tearDown(self):
        self._tmp.cleanup()

    def test_old_call_sites_still_work(self):
        self.assertEqual(self.ctl.step(0.1, False, False)[0], "idle")

    def test_poke_escalation_and_cap(self):
        self.assertEqual(self.ctl.poke(1.0), 0)
        self.assertEqual(self.ctl.poke(1.5), 1)
        self.assertEqual(self.ctl.poke(2.0), 2)
        self.assertEqual(self.ctl.poke(2.5), 2)

    def test_poke_cooldown_ignored(self):
        self.assertEqual(self.ctl.poke(1.0), 0)
        self.assertIsNone(self.ctl.poke(1.2))
        self.assertEqual(self.ctl.poke(1.4), 1)

    def test_poke_resets_after_gap(self):
        self.ctl.poke(1.0)
        self.ctl.poke(1.5)
        self.assertEqual(self.ctl.poke(3.6), 0)  # 距上次 2.1 秒 > 2.0

    def test_poke_duration_then_idle(self):
        self.ctl.poke(1.0)
        self.assertEqual(self.ctl.step(1.1, False, False), ("poke", 1))
        self.assertEqual(self.ctl.step(1.74, False, False), ("poke", 7))  # 幀播完停在最後一幀
        self.assertEqual(self.ctl.step(1.76, False, False)[0], "idle")

    def test_poke_level_sets_duration(self):
        self.ctl.poke(1.0)
        self.ctl.poke(1.5)
        self.ctl.poke(2.0)
        self.assertEqual(self.ctl.step(4.4, False, False)[0], "poke")
        self.assertEqual(self.ctl.step(4.6, False, False)[0], "idle")

    def test_poke_without_config_ignored(self):
        cfg = build(Path(self._tmp.name), {"land": REACTIONS["land"]})
        ctl = pa.AnimController(cfg)
        self.assertIsNone(ctl.poke(1.0))

    def test_poke_cancels_occasional(self):
        ctl = pa.AnimController(self.cfg, random.Random(1))
        ctl.step(0.0, False, False)
        t = 0.0
        while ctl.key != "tea":
            t += 1.0
            ctl.step(t, False, False)
        ctl.poke(t + 0.1)
        self.assertEqual(ctl.step(t + 0.2, False, False)[0], "poke")

    def test_land_one_shot(self):
        self.ctl.land(5.0)
        self.assertEqual(self.ctl.step(5.25, False, False), ("land", 2))
        self.assertEqual(self.ctl.step(5.59, False, False), ("land", 5))
        self.assertEqual(self.ctl.step(5.7, False, False)[0], "idle")

    def test_priority_order(self):
        self.ctl.poke(1.0)
        self.assertEqual(self.ctl.step(1.1, False, True, falling=True)[0], "fall")
        self.assertEqual(self.ctl.step(1.2, False, True, falling=True, dragging=True)[0], "drag")
        self.assertIsNone(self.ctl.step(1.3, True, True, falling=True, dragging=True))
        self.assertEqual(self.ctl.step(1.4, False, False)[0], "idle")  # 說話結束後不殘留反應

    def test_poke_beats_walk(self):
        self.ctl.poke(1.0)
        self.assertEqual(self.ctl.step(1.1, False, True)[0], "poke")
        self.assertEqual(self.ctl.step(2.0, False, True)[0], "walk")

    def test_fall_loops_and_returns_idle(self):
        self.assertEqual(self.ctl.step(1.0, False, False, falling=True), ("fall", 0))
        self.assertEqual(self.ctl.step(1.13, False, False, falling=True), ("fall", 1))
        self.assertEqual(self.ctl.step(2.0, False, False, falling=True)[0], "fall")
        self.assertEqual(self.ctl.step(2.1, False, False)[0], "idle")

    def test_missing_drag_falls_through(self):
        cfg = build(Path(self._tmp.name), {"poke": REACTIONS["poke"]})
        ctl = pa.AnimController(cfg)
        ctl.step(0.0, False, False)
        self.assertEqual(ctl.step(1.0, False, False, dragging=True, falling=True)[0], "idle")

    def test_no_occasional_right_after_reaction(self):
        ctl = pa.AnimController(self.cfg, random.Random(1))
        ctl.step(0.0, False, False)
        ctl._next_occ = 1.0  # 本來該演了
        ctl.poke(0.9)
        self.assertEqual(ctl.step(1.0, False, False)[0], "poke")
        self.assertEqual(ctl.step(1.7, False, False)[0], "idle")  # poke 結束
        self.assertEqual(ctl.step(1.8, False, False)[0], "idle")  # 重排後不會馬上演
        self.assertGreaterEqual(ctl._next_occ, 1.7 + 20)

    def test_drag_release_reschedules_occasional(self):
        self.ctl._next_occ = 1.0
        self.ctl.step(2.0, False, False, dragging=True)
        self.assertEqual(self.ctl.step(2.1, False, False)[0], "idle")
        self.assertGreaterEqual(self.ctl._next_occ, 2.1 + 20)


class FaceZoneTests(unittest.TestCase):
    def test_zone_from_bbox(self):
        fake = {"crop_w": 100, "crop_h": 200, "bbox": (10, 20, 90, 180)}
        am._face_cache.clear()
        with mock.patch.object(am, "avatar_metrics", return_value=fake):
            top, bottom = am.face_zone(Path("x"), 50)  # 縮放 0.5
        self.assertAlmostEqual(top, 10.0)
        self.assertAlmostEqual(bottom, (20 + 160 * 0.35) * 0.5)
        am._face_cache.clear()


if __name__ == "__main__":
    unittest.main()
