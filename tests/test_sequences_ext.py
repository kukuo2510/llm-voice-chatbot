"""多段動作進階欄位的單元測試：hours／after_idle_sec／sleeping／until／exit／anchor_y／placement／weight 0＋start()。

全部用假時間、假矩形、暫存資料夾合成 PNG，不碰真實 Tk、不寫任何真實狀態檔。
用法：python -m unittest tests.test_sequences_ext（邊緣／下沉／物理另見 tests.test_edge_sink_physics）   （在專案根目錄）
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import pet_anim as pa  # noqa: E402
import pet_anim_data as pd  # noqa: E402
import pet_claude  # noqa: E402
import pet_timeofday as tod  # noqa: E402
from pet_anim_window import AnimationMixin  # noqa: E402


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 14, hour, minute)


class FakeRng:
    """uniform 取下限；choices 回傳指定名稱（沒指定就取權重最大者），並記下最後一次的候選。"""

    def __init__(self, pick=None):
        self.pick = pick
        self.last = None

    def uniform(self, lo, hi):
        return lo

    def choices(self, names, weights, k):
        self.last = list(names)
        if self.pick is not None:
            return [self.pick]
        return [names[weights.index(max(weights))]]


class Clock:
    """可手動撥的假本機時間。"""

    def __init__(self, hour):
        self.hour = hour

    def __call__(self):
        return at(self.hour)


def ph(frames=1, **kw):
    return {"frames": ["n.png"] * frames, "fps": 1, "loop": True, **kw}


# 床：進床 -> 坐 -> 躺 -> 睡（until wake，被點醒或早上自然醒，醒來段是 bed-awake）-> 起身坐 -> 站
BED = {
    "weight": 3, "native_facing": "front", "wake_on_click": True,
    "hours": [23, 7], "after_idle_sec": 600, "sleeping": True,
    "phases": [
        ph(duration_sec=[2, 2]),  # 0 bed-enter
        ph(duration_sec=[2, 2]),  # 1 bed-sit
        ph(duration_sec=[2, 2]),  # 2 bed-lie
        ph(until="wake"),  # 3 bed-sleep
        ph(wake=True, duration_sec=[2, 2]),  # 4 bed-awake
        ph(duration_sec=[2, 2]),  # 5 bed-exit-sit
        ph(duration_sec=[2, 2]),  # 6 bed-exit-stand
    ],
}
SIT = {
    "weight": 1, "placement": "edge",
    "phases": [ph(anchor_y=0.69, loop=True, duration_sec=[4, 9])],
}
WORK = {  # 手動工作：weight 0 只能 start()，until stop，stop 時跳到 exit 段
    "weight": 0,
    "phases": [ph(duration_sec=[1, 1]), ph(until="stop"), ph(exit=True, duration_sec=[3, 3])],
}


def build(tmp: Path, sequences, occasional=None):
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(tmp / "n.png")
    data = {
        "occasional_interval_sec": [20, 20],
        "states": {"idle": {"frames": ["n.png"] * 4, "fps": 4}, "walk": {"frames": ["n.png"] * 2, "fps": 10}},
        "occasional": occasional or {},
        "reactions": {"drag": {"frames": ["n.png"], "fps": 8}, "poke": {"frames": ["n.png"], "fps": 8}},
        "sequences": sequences,
    }
    (tmp / "animations.json").write_text(json.dumps(data), encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        return pd.load_animations(tmp)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def ctrl(self, sequences, hour=12, pick=None, occasional=None):
        self.clock = Clock(hour)
        self.rng = FakeRng(pick)
        return pa.AnimController(build(self.tmp, sequences, occasional), self.rng, clock=self.clock,
                                 mono=lambda: 0.0)


class TimeOfDayTest(unittest.TestCase):
    def test_parse_valid(self):
        self.assertIsNone(tod.parse_hours(None))
        self.assertEqual(tod.parse_hours([23, 7]), (23, 7))
        self.assertEqual(tod.parse_hours((0, 24)), (0, 24))

    def test_parse_invalid(self):
        for bad in ([1], [1, 2, 3], "ab", [7, 7], [-1, 5], [5, 25], [24, 3], [1.5, 3], [True, 5], 5, ["a", "b"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                tod.parse_hours(bad)

    def test_in_hours_same_day(self):
        self.assertTrue(tod.in_hours((9, 17), at(9)))
        self.assertTrue(tod.in_hours((9, 17), at(16, 59)))
        self.assertFalse(tod.in_hours((9, 17), at(17)))
        self.assertFalse(tod.in_hours((9, 17), at(8, 59)))

    def test_in_hours_cross_midnight(self):
        for h in (23, 0, 3, 6):
            self.assertTrue(tod.in_hours((23, 7), at(h)), h)
        for h in (7, 12, 22):
            self.assertFalse(tod.in_hours((23, 7), at(h)), h)

    def test_none_and_full_day(self):
        self.assertTrue(tod.in_hours(None, at(3)))
        self.assertTrue(tod.in_hours((0, 24), at(23, 59)))


class ParseTest(TmpCase):
    def test_new_fields_parsed(self):
        cfg = build(self.tmp, {"bed": BED, "sit": SIT, "work": WORK})
        bed, sit, work = cfg.sequences["bed"], cfg.sequences["sit"], cfg.sequences["work"]
        self.assertEqual(bed.hours, (23, 7))
        self.assertEqual(bed.after_idle_sec, 600.0)
        self.assertTrue(bed.sleeping)
        self.assertEqual(bed.phases[3].until, "wake")
        self.assertEqual(bed.wake_index, 4)
        self.assertEqual(sit.placement, "edge")
        self.assertEqual(sit.phases[0].anchor_y, 0.69)
        self.assertEqual(work.weight, 0)  # weight 0 保留（給 start 用）
        self.assertEqual(work.exit_index, 2)
        self.assertEqual(work.phases[1].until, "stop")
        self.assertEqual(cfg.set_for("bed#3").frames, cfg.sequences["bed"].phases[3].aset.frames)

    def test_defaults_unchanged(self):
        cfg = build(self.tmp, {"nap": {"phases": [ph(duration_sec=[1, 2])]}})
        s = cfg.sequences["nap"]
        self.assertIsNone(s.hours)
        self.assertEqual((s.after_idle_sec, s.sleeping, s.placement), (0.0, False, None))
        self.assertEqual((s.phases[0].until, s.phases[0].anchor_y, s.phases[0].exit), (None, None, False))

    def test_bad_items_skipped_not_whole_file(self):
        good = {"phases": [ph()]}
        bads = {
            "bad_hours": {**good, "hours": [7, 7]},
            "bad_idle": {**good, "after_idle_sec": -1},
            "bad_idle_type": {**good, "after_idle_sec": "x"},
            "bad_placement": {**good, "placement": "middle"},
            "bad_until": {"phases": [ph(until="forever")]},
            "bad_anchor": {"phases": [ph(anchor_y=1.5)]},
            "bad_anchor_type": {"phases": [ph(anchor_y="0.5")]},
            "wake_without_target": {"wake_on_click": True, "phases": [ph(until="wake")]},
            "wake_never_ends": {"phases": [ph(until="wake"), ph(wake=True)]},
            "two_exit": {"phases": [ph(exit=True), ph(exit=True)]},
            "mid_placement": {"phases": [ph(), ph(placement="edge")]},
            "ok": good,
        }
        cfg = build(self.tmp, bads)
        self.assertEqual(list(cfg.sequences), ["ok"])

    def test_occasional_hours(self):
        occ = {"tea": {"frames": ["n.png"], "fps": 2, "hours": [9, 17]},
               "bad": {"frames": ["n.png"], "fps": 2, "hours": [5, 5]}}
        cfg = build(self.tmp, {}, occasional=occ)
        self.assertEqual(cfg.occasional["tea"].hours, (9, 17))
        self.assertNotIn("bad", cfg.occasional)


class PickConditionTest(TmpCase):
    def run_to_pick(self, c, t=20.0):
        c.step(0.0, False, False)
        return c.step(t, False, False)

    def test_hours_gate_sequence(self):
        c = self.ctrl({"bed": {**BED, "after_idle_sec": 0}}, hour=12)
        self.run_to_pick(c)
        self.assertIsNone(c.current_sequence)  # 中午：池子空，不演
        self.assertEqual(c.key, "idle")
        self.clock.hour = 23
        c.step(60.0, False, False)
        self.assertEqual(c.current_sequence, "bed")

    def test_hours_gate_occasional(self):
        occ = {"tea": {"frames": ["n.png"], "fps": 2, "hours": [9, 17]}}
        c = self.ctrl({}, hour=22, occasional=occ)
        self.run_to_pick(c)
        self.assertEqual(c.key, "idle")
        self.clock.hour = 10
        c.step(60.0, False, False)
        self.assertEqual(c.key, "tea")

    def test_pool_lists_only_eligible(self):
        c = self.ctrl({"bed": {**BED, "after_idle_sec": 0}, "sit": {**SIT, "placement": None}, "work": WORK},
                      hour=12)
        self.run_to_pick(c)
        self.assertEqual(self.rng.last, ["sit"])  # bed 時段不對、work 權重 0

    def test_after_idle_sec(self):
        c = self.ctrl({"bed": {**BED, "hours": None, "after_idle_sec": 600}})
        c.step(0.0, False, False)  # 第一次 step 起算互動
        c.step(100.0, False, False)
        self.assertIsNone(c.current_sequence)  # 才 100 秒
        c.step(599.0, False, False)
        self.assertIsNone(c.current_sequence)
        c.step(700.0, False, False)  # 空池時會重排下一次抽籤（約 20 秒後），所以這裡挑晚一點的時間點
        self.assertEqual(c.current_sequence, "bed")

    def test_interactions_reset_idle(self):
        seq = {"bed": {**BED, "hours": None, "after_idle_sec": 600}}
        # 說話
        c = self.ctrl(seq)
        c.step(0.0, False, False)
        c.step(500.0, True, False)
        c.step(700.0, False, False)
        self.assertIsNone(c.current_sequence)  # 距離說話只有 200 秒
        # 拖曳
        c = self.ctrl(seq)
        c.step(0.0, False, False)
        c.step(500.0, False, False, dragging=True)
        c.step(700.0, False, False)
        self.assertIsNone(c.current_sequence)
        # 戳
        c = self.ctrl(seq)
        c.step(0.0, False, False)
        c.poke(500.0)
        c.step(700.0, False, False)
        self.assertIsNone(c.current_sequence)
        # 輸入列開著（allow_occasional=False）
        c = self.ctrl(seq)
        c.step(0.0, False, False)
        c.step(500.0, False, False, allow_occasional=False)
        c.step(700.0, False, False)
        self.assertIsNone(c.current_sequence)
        # 明確呼叫
        c = self.ctrl(seq)
        c.step(0.0, False, False)
        c.note_interaction(500.0)
        c.step(700.0, False, False)
        self.assertIsNone(c.current_sequence)

    def test_empty_pool_reschedules(self):
        c = self.ctrl({"bed": BED}, hour=12)
        self.run_to_pick(c)
        self.assertEqual(c._next_occ, 40.0)  # 抽不到東西：重排下一次，不卡住


class SleepingAndUntilTest(TmpCase):
    def bed(self, hour=23):
        c = self.ctrl({"bed": {**BED, "after_idle_sec": 0}}, hour=hour)
        c.step(0.0, False, False)
        c.step(20.0, False, False)
        self.assertEqual(c.current_sequence, "bed")
        return c

    def test_is_sleeping_and_resting(self):
        c = self.ctrl({"bed": {**BED, "after_idle_sec": 0}, "sit": {**SIT, "placement": None}}, hour=12)
        self.assertFalse(c.is_sleeping)
        c.step(0.0, False, False)
        c.step(20.0, False, False)  # 中午只抽得到 sit
        self.assertEqual(c.current_sequence, "sit")
        self.assertFalse(c.is_sleeping)
        c = self.bed()
        self.assertTrue(c.is_sleeping)
        self.assertTrue(c.resting)
        c.step(21.0, True, False)  # 說話取消
        self.assertFalse(c.is_sleeping)
        self.assertIsNone(c.current_sequence)

    def test_phases_advance_until_wake_loops_forever(self):
        c = self.bed()
        for t, key in ((21.0, "bed#0"), (22.5, "bed#1"), (24.5, "bed#2"), (26.5, "bed#3")):
            self.assertEqual(c.step(t, False, False)[0], key)
        # 睡了幾個小時，還在 bed#3，沒被 duration 結束
        self.assertEqual(c.step(26.5 + 7 * 3600, False, False)[0], "bed#3")
        self.assertTrue(c.is_sleeping)

    def test_click_wakes(self):
        c = self.bed()
        c.step(30.0, False, False)
        self.assertEqual(c.key, "bed#3")
        self.assertTrue(c.wake_click(31.0))
        self.assertEqual(c.key, "bed#4")
        self.assertEqual(c.step(32.0, False, False)[0], "bed#4")
        self.assertEqual(c.step(33.5, False, False)[0], "bed#5")
        self.assertEqual(c.step(35.5, False, False)[0], "bed#6")
        c.step(37.5, False, False)
        self.assertIsNone(c.current_sequence)
        self.assertEqual(c.key, "idle")

    def test_natural_wake_when_leaving_hours(self):
        c = self.bed()
        c.step(30.0, False, False)
        self.assertEqual(c.key, "bed#3")
        self.clock.hour = 6
        self.assertEqual(c.step(40.0, False, False)[0], "bed#3")  # 還在時段內
        self.clock.hour = 7  # 早上了
        self.assertEqual(c.step(50.0, False, False)[0], "bed#4")  # 自然起床，跳到 wake 段
        self.assertEqual(c.step(52.5, False, False)[0], "bed#5")

    def test_no_natural_wake_without_hours(self):
        c = self.ctrl({"bed": {**BED, "hours": None, "after_idle_sec": 0}}, hour=12)
        c.step(0.0, False, False)
        c.step(20.0, False, False)
        c.step(30.0, False, False)
        self.assertEqual(c.step(30.0 + 50000, False, False)[0], "bed#3")  # 沒 hours：只能點醒

    def test_pet_sleeping_skips_notifications(self):
        c = self.bed()

        class W(AnimationMixin):
            pass

        w = W()
        w._anim_ctrl = c
        self.assertTrue(w._pet_sleeping())
        w._anim_ctrl = None
        self.assertFalse(w._pet_sleeping())

        class Claude(pet_claude.ClaudeEventMixin):
            def __init__(self, sleeping):
                self.sleeping, self.busy_calls, self.said = sleeping, 0, []
                self._busy = True  # 要是沒略過，會走到「忙碌 -> 排隊重試」

                class Root:
                    after = staticmethod(lambda *a, **k: self.__setattr__("busy_calls", self.busy_calls + 1))

                self.root = Root()

            def _pet_sleeping(self):
                return self.sleeping

            def _trigger_proactive(self, p):
                self.said.append(p)

        sleeping = Claude(True)
        sleeping._on_claude_event({"event": "x"})
        self.assertEqual(sleeping.busy_calls, 0)  # 沒排隊補講
        awake = Claude(False)
        awake._on_claude_event({"event": "x"})
        self.assertEqual(awake.busy_calls, 1)  # 醒著照舊：忙碌就排隊重試


class StartStopTest(TmpCase):
    def test_weight0_not_picked_but_start_works(self):
        c = self.ctrl({"work": WORK}, hour=12)
        c.step(0.0, False, False)
        c.step(20.0, False, False)
        self.assertIsNone(c.current_sequence)
        self.assertTrue(c.start("work", 25.0))
        self.assertEqual(c.current_sequence, "work")
        self.assertEqual(c.key, "work#0")
        self.assertFalse(c.start("nope", 25.0))

    def test_start_cancels_occasional(self):
        occ = {"tea": {"frames": ["n.png"], "fps": 2, "loop": True, "duration_sec": [30, 30]}}
        c = self.ctrl({"work": WORK}, occasional=occ)
        c.step(0.0, False, False)
        c.step(20.0, False, False)
        self.assertEqual(c.key, "tea")
        c.start("work", 21.0)
        self.assertEqual(c.step(21.1, False, False)[0], "work#0")

    def test_start_survives_walk_aftershock(self):
        c = self.ctrl({"work": WORK})
        c.step(0.0, False, True)
        c.start("work", 1.0)
        self.assertEqual(c.step(1.1, False, True)[0], "work#0")  # 剛走完的餘波不取消
        self.assertEqual(c.step(1.4, False, True)[0], "work#0")
        self.assertEqual(c.step(2.0, False, True)[0], "walk")  # 超過寬限還在走才取消

    def test_start_ignores_hours_and_idle_and_no_auto_wake(self):
        c = self.ctrl({"bed": BED}, hour=12)  # 中午、剛互動，條件都不符
        c.step(0.0, False, False)
        c.start("bed", 1.0)
        for t in (2.0, 4.0, 6.0):
            c.step(t, False, False)
        self.assertEqual(c.step(8.0, False, False)[0], "bed#3")
        self.assertEqual(c.step(5000.0, False, False)[0], "bed#3")  # 強制開始的不會因為不在時段而自然起床

    def test_start_while_talking_is_queued(self):
        c = self.ctrl({"work": WORK})
        c.step(0.0, False, False)
        self.assertIsNone(c.step(1.0, True, False))
        self.assertTrue(c.start("work", 1.5))
        self.assertIsNone(c.current_sequence)  # 說話中不打斷
        self.assertIsNone(c.step(2.0, True, False))
        self.assertEqual(c.step(3.0, False, False)[0], "work#0")  # 講完才開始
        self.assertEqual(c.current_sequence, "work")

    def test_talking_cancels_running_sequence(self):
        c = self.ctrl({"work": WORK})
        c.step(0.0, False, False)
        c.start("work", 1.0)
        self.assertIsNone(c.step(1.5, True, False))
        self.assertIsNone(c.current_sequence)

    def test_stop_jumps_to_exit(self):
        c = self.ctrl({"work": WORK})
        c.step(0.0, False, False)
        c.start("work", 1.0)
        self.assertEqual(c.step(2.5, False, False)[0], "work#1")  # until stop：一直在這段
        self.assertEqual(c.step(900.0, False, False)[0], "work#1")
        self.assertTrue(c.stop("work", 901.0))
        self.assertEqual(c.key, "work#2")
        self.assertEqual(c.step(902.0, False, False)[0], "work#2")
        c.step(904.5, False, False)
        self.assertIsNone(c.current_sequence)

    def test_stop_without_exit_ends(self):
        c = self.ctrl({"w": {"weight": 0, "phases": [ph(until="stop")]}})
        c.step(0.0, False, False)
        c.start("w", 1.0)
        self.assertEqual(c.current_sequence, "w")
        self.assertTrue(c.stop("w", 5.0))
        self.assertIsNone(c.current_sequence)
        self.assertEqual(c.key, "idle")

    def test_stop_wrong_name_or_idle(self):
        c = self.ctrl({"work": WORK})
        c.step(0.0, False, False)
        self.assertFalse(c.stop("work", 1.0))
        c.start("work", 2.0)
        self.assertFalse(c.stop("other", 3.0))
        self.assertEqual(c.current_sequence, "work")

    def test_stop_in_exit_phase_keeps_playing(self):
        c = self.ctrl({"work": WORK})
        c.step(0.0, False, False)
        c.start("work", 1.0)
        c.step(2.5, False, False)
        c.stop("work", 3.0)
        c.step(4.0, False, False)
        self.assertTrue(c.stop("work", 4.5))  # 已經在 exit 段：不重來
        self.assertEqual(c.step(5.0, False, False)[0], "work#2")
        self.assertEqual(c.step(5.5, False, False)[0], "work#2")  # exit 段從 3.0 開始演 3 秒，還沒演完

    def test_current_sequence_none_by_default(self):
        c = self.ctrl({"work": WORK})
        self.assertIsNone(c.current_sequence)
        self.assertFalse(c.is_sleeping)
        self.assertIsNone(c.anchor_y)


class EdgePlacementTest(TmpCase):
    def sit(self, **kw):
        c = self.ctrl({"sit": {**SIT, **kw}}, hour=12)
        c.step(0.0, False, False)
        c.step(20.0, False, False)
        return c

    def test_pick_enters_pending_not_sequence(self):
        c = self.sit()
        self.assertEqual(c.pending_edge, "sit")
        self.assertIsNone(c.current_sequence)
        self.assertIsNone(c.anchor_y)
        self.assertFalse(c.resting)
        self.assertEqual(c.key, "idle")

    def test_no_second_pick_while_pending(self):
        c = self.sit()
        c.step(24.0, False, False)
        self.assertEqual(c.pending_edge, "sit")  # 還沒到邊緣、也沒逾時：不會又抽別的

    def test_arrival_starts_after_step(self):
        c = self.sit()
        c.step(21.0, False, True)  # 走路中
        self.assertIsNone(c.current_sequence)
        c.edge_arrived()
        self.assertEqual(c.step(22.0, False, True)[0], "sit#0")  # 剛到邊緣、走路餘波還在：不被取消
        self.assertEqual(c.current_sequence, "sit")
        self.assertIsNone(c.pending_edge)
        self.assertEqual(c.anchor_y, 0.69)
        self.assertTrue(c.resting)

    def test_phase_placement_on_first_phase(self):
        cfg = build(self.tmp, {"s": {"phases": [ph(placement="edge", anchor_y=0.5, duration_sec=[1, 1])]}})
        self.assertEqual(cfg.sequences["s"].placement, "edge")

    def test_cancelled_by_talking_drag_fall_poke(self):
        for kwargs, talking in (({}, True), ({"dragging": True}, False), ({"falling": True}, False)):
            c = self.sit()
            c.step(21.0, talking, False, **kwargs)
            self.assertIsNone(c.pending_edge, (kwargs, talking))
        c = self.sit()
        c.poke(21.0)
        self.assertIsNone(c.pending_edge)

    def test_timeout_gives_up(self):
        c = self.sit()
        c.step(20.0 + 26.0, False, False)
        self.assertIsNone(c.pending_edge)
        self.assertIsNone(c.current_sequence)

    def test_start_edge_sequence_walks_first_and_stop_cancels(self):
        c = self.ctrl({"sit": {**SIT, "weight": 0}})
        c.step(0.0, False, False)
        c.start("sit", 1.0)
        self.assertEqual(c.pending_edge, "sit")
        self.assertTrue(c.stop("sit", 2.0))
        self.assertIsNone(c.pending_edge)


if __name__ == "__main__":
    unittest.main()
