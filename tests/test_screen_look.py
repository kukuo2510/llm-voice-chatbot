"""閒置「看螢幕」抽籤：狀態機事件、animations.json 解析的單元測試（暫存資料夾，不碰真實素材）。

用法：python -m unittest tests.test_screen_look   （在專案根目錄）
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import pet_anim as pa  # noqa: E402
import pet_anim_data as pd  # noqa: E402


class FakeRng:
    """uniform 取下限；choices 回傳指定名稱（沒指定就取權重最大者）。"""

    def __init__(self, pick=None):
        self.pick = pick

    def uniform(self, lo, hi):
        return lo

    def choices(self, names, weights, k):
        if self.pick is not None:
            return [self.pick]
        return [names[weights.index(max(weights))]]


def build(tmp: Path, data: dict):
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(tmp / "a.png")
    base = {"states": {"idle": {"frames": ["a.png"]}}, "occasional_interval_sec": [10, 20]}
    (tmp / "animations.json").write_text(json.dumps({**base, **data}), encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        return pd.load_animations(tmp)


class ScreenLookStepTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def ctrl(self, data, pick=pa.SCREEN_LOOK):
        return pa.AnimController(build(self.dir, data), rng=FakeRng(pick))

    def test_picked_emits_event_and_stays_idle(self):
        c = self.ctrl({"screen_look": {"weight": 1},
                       "occasional": {"tea": {"frames": ["a.png"], "loop": False}}})
        c.step(0.0, False, False, allow_screen_look=True)
        old_next = c._next_occ
        key, _ = c.step(old_next + 1, False, False, allow_screen_look=True)
        self.assertEqual(key, "idle")
        self.assertEqual(c.pop_event(), pa.SCREEN_LOOK)
        self.assertIsNone(c.pop_event())
        self.assertGreater(c._next_occ, old_next)

    def test_not_allowed_never_emits(self):
        c = self.ctrl({"screen_look": {"weight": 1},
                       "occasional": {"tea": {"frames": ["a.png"], "loop": False}}}, pick="tea")
        c.step(0.0, False, False)
        c.step(c._next_occ + 1, False, False, allow_screen_look=False)
        self.assertIsNone(c.pop_event())

    def test_zero_weight_never_emits(self):
        c = self.ctrl({"screen_look": {"weight": 0},
                       "occasional": {"tea": {"frames": ["a.png"], "loop": False}}}, pick="tea")
        c.step(0.0, False, False, allow_screen_look=True)
        c.step(c._next_occ + 1, False, False, allow_screen_look=True)
        self.assertIsNone(c.pop_event())

    def test_only_screen_look_in_pool(self):
        c = self.ctrl({"screen_look": {"weight": 1}})
        c.step(0.0, False, False, allow_screen_look=True)
        key, _ = c.step(c._next_occ + 1, False, False, allow_screen_look=True)
        self.assertEqual(key, "idle")
        self.assertEqual(c.pop_event(), pa.SCREEN_LOOK)

    def test_only_screen_look_but_disallowed(self):
        c = self.ctrl({"screen_look": {"weight": 1}})
        c.step(0.0, False, False)
        c.step(c._next_occ + 1, False, False)
        self.assertIsNone(c.pop_event())


class ScreenLookParseTests(unittest.TestCase):
    def weight(self, data):
        with tempfile.TemporaryDirectory() as t:
            return build(Path(t), data).screen_look_weight

    def test_given(self):
        self.assertEqual(self.weight({"screen_look": {"weight": 2}}), 2.0)

    def test_missing(self):
        self.assertEqual(self.weight({}), 0.0)

    def test_negative(self):
        self.assertEqual(self.weight({"screen_look": {"weight": -3}}), 0.0)

    def test_malformed(self):
        self.assertEqual(self.weight({"screen_look": "yes"}), 0.0)
        self.assertEqual(self.weight({"screen_look": {"weight": "abc"}}), 0.0)
        self.assertEqual(self.weight({"screen_look": {}}), 0.0)


if __name__ == "__main__":
    unittest.main()
