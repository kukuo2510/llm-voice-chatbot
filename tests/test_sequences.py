"""多段式小動作（sequences）：解析、挑選、分段推進、點擊喚醒、取消、漫遊暫停的單元測試（暫存資料夾合成 PNG）。

用法：python -m unittest tests.test_sequences   （在專案根目錄）
"""
import json
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
import pet_anim_fx as fx  # noqa: E402
import pet_physics as pp  # noqa: E402


class FakeRng:
    """uniform 取下限；choices 回傳指定名稱（沒指定就取權重最大者）。"""

    def __init__(self, pick=None):
        self.pick = pick
        self.last_choices = None

    def uniform(self, lo, hi):
        return lo

    def choices(self, names, weights, k):
        self.last_choices = (list(names), list(weights))
        if self.pick is not None:
            return [self.pick]
        return [names[weights.index(max(weights))]]


NAP = {
    "weight": 2, "native_facing": "front", "wake_on_click": True,
    "phases": [
        {"frames": ["n.png"] * 6, "fps": 1.5, "loop": False, "duration_sec": [10, 12]},
        {"frames": ["n.png"] * 7, "fps": 2, "loop": False, "wake": True},
    ],
}


def build(tmp: Path, sequences, occasional=None):
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(tmp / "n.png")
    data = {
        "occasional_interval_sec": [20, 20],
        "states": {"idle": {"frames": ["n.png"] * 4, "fps": 4}, "walk": {"frames": ["n.png"] * 2, "fps": 10}},
        "occasional": occasional or {},
        "reactions": {"drag": {"frames": ["n.png"], "fps": 8}},
    }
    if sequences is not None:
        data["sequences"] = sequences
    (tmp / "animations.json").write_text(json.dumps(data), encoding="utf-8")
    return pd.load_animations(tmp)


class SequenceCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)

    def start_nap(self, sequences=None, rng=None):
        cfg = build(self.tmp, {"nap": NAP} if sequences is None else sequences)
        ctrl = pa.AnimController(cfg, rng or FakeRng("nap"))
        ctrl.step(0, False, False)  # 排程：下次小動作 t=20
        self.assertEqual(ctrl.step(20, False, False), ("nap#0", 0))
        return ctrl


class TestParse(SequenceCase):
    def test_absent(self):
        cfg = build(self.tmp, None)
        self.assertEqual(cfg.sequences, {})

    def test_valid(self):
        cfg = build(self.tmp, {"nap": NAP})
        s = cfg.sequences["nap"]
        self.assertEqual([p.key for p in s.phases], ["nap#0", "nap#1"])
        self.assertEqual(s.wake_index, 1)
        self.assertEqual(s.phases[0].duration, (10.0, 12.0))
        self.assertIsNone(s.phases[1].duration)
        self.assertEqual(cfg.set_for("nap#1").native_facing, "front")

    def test_invalid_skipped(self):
        bad = {
            "empty": {"phases": []},
            "nophases": {},
            "notobj": 5,
            "missing": {"phases": [{"frames": ["nope.png"]}]},
            "twowake": {"phases": [{"frames": ["n.png"], "wake": True}, {"frames": ["n.png"], "wake": True}]},
            "baddur": {"phases": [{"frames": ["n.png"], "duration_sec": [5, 1]}]},
            "a#b": {"phases": [{"frames": ["n.png"]}]},
            "good": NAP,
        }
        cfg = build(self.tmp, bad)
        self.assertEqual(list(cfg.sequences), ["good"])

    def test_not_a_dict(self):
        self.assertEqual(build(self.tmp, [1, 2]).sequences, {})

    def test_collisions(self):
        seq = {"idle": NAP, "poke": NAP, "tea": NAP, "ok": NAP}
        cfg = build(self.tmp, seq, occasional={"tea": {"frames": ["n.png"], "fps": 2}})
        self.assertEqual(list(cfg.sequences), ["ok"])


class TestPick(SequenceCase):
    def test_shared_pool_weights(self):
        cfg = build(self.tmp, {"nap": NAP}, occasional={"tea": {"frames": ["n.png"], "fps": 2, "weight": 1}})
        rng = FakeRng()
        ctrl = pa.AnimController(cfg, rng)
        ctrl.step(0, False, False)
        ctrl.step(20, False, False)
        self.assertEqual(rng.last_choices, (["tea", "nap"], [1.0, 2.0]))
        self.assertEqual(ctrl.key, "nap#0")

    def test_without_sequences_pool_unchanged(self):
        cfg = build(self.tmp, None, occasional={"tea": {"frames": ["n.png"], "fps": 2}})
        rng = FakeRng()
        ctrl = pa.AnimController(cfg, rng)
        ctrl.step(0, False, False)
        ctrl.step(20, False, False)
        self.assertEqual(rng.last_choices, (["tea"], [1.0]))
        self.assertEqual(ctrl.key, "tea")
        self.assertFalse(ctrl.resting)


class TestRun(SequenceCase):
    def test_progression_and_finish(self):
        ctrl = self.start_nap()
        self.assertTrue(ctrl.resting)
        self.assertEqual(ctrl.step(25, False, False), ("nap#0", 7)[:1] + (5,))  # 非 loop 夾在最後一幀
        self.assertEqual(ctrl.step(30.5, False, False)[0], "nap#1")  # 20+10 後進第二段
        self.assertEqual(ctrl.step(31, False, False), ("nap#1", 2))
        # 第二段 7 幀 / 2fps = 3.5 秒，t=33.5 結束
        self.assertEqual(ctrl.step(34, False, False), ("idle", 0))
        self.assertFalse(ctrl.resting)
        ctrl.step(60, False, False)  # 重排：34+20=54 之後才會再演
        self.assertEqual(ctrl.key, "nap#0")

    def test_not_replayed_immediately(self):
        ctrl = self.start_nap()
        ctrl.step(34, False, False)
        ctrl.step(34.1, False, False)
        self.assertEqual(ctrl.key, "idle")

    def test_click_wakes_and_swallows_before_wake(self):
        ctrl = self.start_nap()
        self.assertTrue(ctrl.wake_click(22))
        self.assertEqual(ctrl.key, "nap#1")
        self.assertEqual(ctrl.step(22.1, False, False)[0], "nap#1")
        self.assertFalse(ctrl.wake_click(22.5))  # 醒來那段不吞

    def test_no_wake_without_flag(self):
        nap = {**NAP, "wake_on_click": False}
        ctrl = self.start_nap({"nap": nap})
        self.assertFalse(ctrl.wake_click(22))
        self.assertEqual(ctrl.key, "nap#0")

    def test_no_wake_when_idle(self):
        cfg = build(self.tmp, {"nap": NAP})
        ctrl = pa.AnimController(cfg, FakeRng("nap"))
        ctrl.step(0, False, False)
        self.assertFalse(ctrl.wake_click(1))

    def test_talking_cancels(self):
        ctrl = self.start_nap()
        self.assertIsNone(ctrl.step(22, True, False))
        self.assertFalse(ctrl.resting)
        self.assertEqual(ctrl.step(23, False, False)[0], "idle")
        self.assertFalse(ctrl.wake_click(23))

    def test_drag_and_fall_cancel(self):
        for kw in ({"dragging": True}, {"falling": True}):
            ctrl = self.start_nap()
            res = ctrl.step(22, False, False, **kw)
            self.assertFalse(ctrl.resting)
            self.assertNotIn("#", res[0])
            self.assertNotIn("#", ctrl.step(23, False, False)[0])

    def test_walking_cancels(self):
        ctrl = self.start_nap()
        self.assertEqual(ctrl.step(22, False, True)[0], "walk")
        self.assertFalse(ctrl.resting)

    def test_fx_identity_for_phase_keys(self):
        self.assertEqual(fx.fx_for("nap#0", 3.0), fx.IDENTITY)


class TestWander(unittest.TestCase):
    def test_wander_paused(self):
        self.assertTrue(pp.wander_paused(False, False, True))
        self.assertTrue(pp.wander_paused(True, False, False))
        self.assertTrue(pp.wander_paused(False, True, False))
        self.assertFalse(pp.wander_paused(False, False, False))


if __name__ == "__main__":
    unittest.main()
