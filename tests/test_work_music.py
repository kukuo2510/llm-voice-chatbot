"""工作子選單、股票結果、音樂播放清單與鋼琴播放器選單邏輯的單元測試。

全部用假物件：不開 Tk 視窗、不播真的聲音、不碰真實偏好檔。
用法：python -m unittest tests.test_work_music   （在專案根目錄）
"""
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402

import pet_music  # noqa: E402
import pet_music_player as mp  # noqa: E402
import pet_playlist as pl  # noqa: E402
import pet_work  # noqa: E402
import pet_work_data as wd  # noqa: E402


class FakeCtrl:
    """只實作 start/stop/current_sequence/config.sequences 的假動畫控制器。"""

    def __init__(self, defined, current=None):
        self.config = SimpleNamespace(sequences={n: object() for n in defined})
        self.current_sequence = current
        self.calls = []

    def start(self, name):
        self.calls.append(("start", name))
        if name not in self.config.sequences:
            return False
        self.current_sequence = name
        return True

    def stop(self, name):
        self.calls.append(("stop", name))
        if self.current_sequence != name:
            return False
        self.current_sequence = None
        return True


ALL_WORK = ["work", "delivery", "chef", "stock"]
ALL_OUTCOMES = [n for n, _ in wd.STOCK_OUTCOMES]


class WorkLogicTests(unittest.TestCase):
    def test_enabled_depends_on_definition(self):
        ctrl = FakeCtrl(["work", "stock"])
        self.assertTrue(wd.job_enabled(ctrl, "work"))
        self.assertFalse(wd.job_enabled(ctrl, "chef"))
        self.assertFalse(wd.job_enabled(None, "work"))

    def test_start_job(self):
        ctrl = FakeCtrl(ALL_WORK)
        self.assertTrue(wd.start_job(ctrl, "work"))
        self.assertEqual(ctrl.calls, [("start", "work")])

    def test_start_undefined_or_no_ctrl(self):
        ctrl = FakeCtrl(["work"])
        self.assertFalse(wd.start_job(ctrl, "chef"))
        self.assertEqual(ctrl.calls, [])
        self.assertFalse(wd.start_job(None, "work"))

    def test_switch_stops_old_first(self):
        ctrl = FakeCtrl(ALL_WORK, current="work")
        self.assertTrue(wd.start_job(ctrl, "chef"))
        self.assertEqual(ctrl.calls, [("stop", "work"), ("start", "chef")])

    def test_same_job_is_noop(self):
        ctrl = FakeCtrl(ALL_WORK, current="work")
        self.assertFalse(wd.start_job(ctrl, "work"))
        self.assertEqual(ctrl.calls, [])

    def test_finish_only_for_jobs(self):
        ctrl = FakeCtrl(ALL_WORK + ["sleep"], current="sleep")
        self.assertFalse(wd.finish_job(ctrl))
        self.assertEqual(ctrl.calls, [])
        ctrl.current_sequence = "delivery"
        self.assertTrue(wd.finish_job(ctrl))
        self.assertEqual(ctrl.calls, [("stop", "delivery")])
        self.assertFalse(wd.finish_job(None))

    def test_job_table(self):
        self.assertEqual([s for _, s in wd.WORK_JOBS], ALL_WORK)
        self.assertEqual([n for n, _ in wd.WORK_JOBS], ["打工", "外送", "廚師", "股票"])


class StockTests(unittest.TestCase):
    def test_outcome_and_lines_tables_match(self):
        self.assertEqual(set(ALL_OUTCOMES), set(wd.STOCK_LINES))
        self.assertEqual(len(ALL_OUTCOMES), 6)
        for lines in wd.STOCK_LINES.values():
            self.assertTrue(2 <= len(lines) <= 3)
            self.assertTrue(all(0 < len(s) <= 20 for s in lines))

    def test_pick_outcome_always_valid_and_varied(self):
        rng = random.Random(1)
        got = {wd.pick_outcome(rng) for _ in range(500)}
        self.assertTrue(got <= set(ALL_OUTCOMES))
        self.assertEqual(got, set(ALL_OUTCOMES))

    def test_pick_line(self):
        for o in ALL_OUTCOMES:
            self.assertIn(wd.pick_line(o, random.Random(2)), wd.STOCK_LINES[o])
        self.assertEqual(wd.pick_line("nope"), "")

    def test_settle_giveup(self):
        ctrl = FakeCtrl(ALL_WORK + ALL_OUTCOMES, current="stock")
        self.assertEqual(wd.settle_stock(ctrl, chosen=False), (None, ""))
        self.assertEqual(ctrl.calls, [("stop", "stock")])

    def test_settle_chosen_plays_outcome(self):
        ctrl = FakeCtrl(ALL_WORK + ALL_OUTCOMES, current="stock")
        outcome, line = wd.settle_stock(ctrl, chosen=True, rng=random.Random(3))
        self.assertIn(outcome, ALL_OUTCOMES)
        self.assertIn(line, wd.STOCK_LINES[outcome])
        self.assertEqual(ctrl.calls, [("stop", "stock"), ("start", outcome)])

    def test_settle_undefined_outcome_only_line(self):
        ctrl = FakeCtrl(ALL_WORK, current="stock")
        outcome, line = wd.settle_stock(ctrl, chosen=True, rng=random.Random(3))
        self.assertTrue(line)
        self.assertEqual(ctrl.calls, [("stop", "stock")])

    def test_settle_without_ctrl(self):
        outcome, line = wd.settle_stock(None, chosen=True, rng=random.Random(3))
        self.assertTrue(line)


class WorkMixinTests(unittest.TestCase):
    def make(self, ctrl):
        w = pet_work.WorkMixin()
        w._anim_ctrl = ctrl
        w.root = object()
        w._set_dialogue = mock.Mock()
        return w

    def test_stock_opens_picker_and_choice_settles(self):
        ctrl = FakeCtrl(ALL_WORK + ALL_OUTCOMES)
        w = self.make(ctrl)
        picker = mock.Mock()
        with mock.patch.object(pet_work, "open_stock_picker", return_value=picker) as op:
            w._on_work_pick("stock")
            op.assert_called_once()
            on_choose = op.call_args.args[2]
        self.assertIs(w._stock_picker, picker)
        on_choose("豐川集團")
        self.assertIsNone(w._stock_picker)
        self.assertEqual(ctrl.calls[0], ("start", "stock"))
        self.assertEqual(ctrl.calls[1], ("stop", "stock"))
        w._set_dialogue.assert_called_once()
        self.assertFalse(w._set_dialogue.call_args.kwargs["is_status"])

    def test_giveup_stops_stock_no_line(self):
        ctrl = FakeCtrl(ALL_WORK + ALL_OUTCOMES, current="stock")
        w = self.make(ctrl)
        w._on_stock_choice(None)
        self.assertEqual(ctrl.calls, [("stop", "stock")])
        w._set_dialogue.assert_not_called()

    def test_finish_closes_picker(self):
        ctrl = FakeCtrl(ALL_WORK, current="stock")
        w = self.make(ctrl)
        picker = mock.Mock()
        w._stock_picker = picker
        w._on_work_finish()
        picker.destroy.assert_called_once()
        self.assertEqual(ctrl.calls, [("stop", "stock")])

    def test_menu_refresh_states(self):
        w = self.make(FakeCtrl(["work", "stock"], current="work"))
        w._work_menu = mock.Mock()
        w._refresh_work_menu()
        states = [c.kwargs["state"] for c in w._work_menu.entryconfigure.call_args_list]
        # 打工、外送、廚師、股票、(分隔線 index 4 不動)、收工
        self.assertEqual(states, ["normal", "disabled", "disabled", "normal", "normal"])
        self.assertEqual(w._work_menu.entryconfigure.call_args_list[-1].args[0], 5)

    def test_menu_all_disabled_without_ctrl(self):
        w = self.make(None)
        w._work_menu = mock.Mock()
        w._refresh_work_menu()
        states = [c.kwargs["state"] for c in w._work_menu.entryconfigure.call_args_list]
        self.assertEqual(states, ["disabled"] * 5)


class PlaylistTests(unittest.TestCase):
    def test_scan_filters_extensions_not_recursive(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ["a.mp3", "b.WAV", "c.flac", "d.ogg", "e.txt", "f.m4a"]:
                (d / n).write_bytes(b"x")
            (d / "sub").mkdir()
            (d / "sub" / "g.mp3").write_bytes(b"x")
            (d / "h.mp3").mkdir()  # 資料夾不算
            names = [Path(p).name for p in pl.scan_folder(d)]
        self.assertEqual(names, ["a.mp3", "b.WAV", "c.flac", "d.ogg"])

    def test_scan_empty_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pl.scan_folder(d), [])
            self.assertEqual(pl.scan_folder(Path(d) / "nope"), [])
        self.assertEqual(pl.scan_folder(None), [])
        self.assertEqual(pl.scan_folder(""), [])

    def test_each_round_plays_all_once(self):
        tracks = [f"t{i}" for i in range(6)]
        p = pl.Playlist(tracks, random.Random(5))
        for _ in range(4):
            round_ = [p.next() for _ in tracks]
            self.assertEqual(sorted(round_), sorted(tracks))

    def test_shuffled_and_reshuffled(self):
        tracks = [f"t{i}" for i in range(8)]
        p = pl.Playlist(tracks, random.Random(7))
        r1 = [p.next() for _ in tracks]
        r2 = [p.next() for _ in tracks]
        self.assertNotEqual(r1, tracks)
        self.assertNotEqual(r1, r2)

    def test_no_repeat_at_round_boundary(self):
        p = pl.Playlist(["a", "b"], random.Random(0))
        seq = [p.next() for _ in range(200)]
        self.assertTrue(all(x != y for x, y in zip(seq, seq[1:])))

    def test_single_and_empty(self):
        p = pl.Playlist(["only"])
        self.assertEqual([p.next() for _ in range(3)], ["only"] * 3)
        self.assertIsNone(pl.Playlist([]).next())


class GainTests(unittest.TestCase):
    def test_duck(self):
        self.assertEqual(mp.duck_gain(False), 1.0)
        self.assertLess(mp.duck_gain(True), 1.0)

    def test_apply_gain(self):
        block = np.ones((4, 2), dtype=np.float32)
        self.assertIs(mp.apply_gain(block, 1.0, 1.0), block)
        np.testing.assert_allclose(mp.apply_gain(block, 0.5, 0.5), 0.5)
        ramp = mp.apply_gain(block, 1.0, 0.0)
        self.assertEqual(ramp.shape, (4, 2))
        self.assertAlmostEqual(float(ramp[0, 0]), 1.0)
        self.assertAlmostEqual(float(ramp[-1, 0]), 0.0)


class MusicMixinTests(unittest.TestCase):
    def make(self, ctrl=None, folder=None):
        m = pet_music.MusicMixin()
        m._anim_ctrl = ctrl
        m.root = mock.Mock()
        m._set_dialogue = mock.Mock()
        m._clear_status = mock.Mock()
        self.prefs = {"music_folder": folder} if folder else {}
        self.saved = []
        patches = [
            mock.patch.object(pet_music, "_load_prefs", lambda: dict(self.prefs)),
            mock.patch.object(pet_music, "_save_prefs", lambda **kw: self.saved.append(kw)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return m

    def fake_player(self, m, playing=False):
        player = mock.Mock()
        player.is_playing.return_value = playing
        player.play.return_value = True
        m._music_player = player
        return player

    def test_play_starts_music_and_piano(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.mp3").write_bytes(b"x")
            ctrl = FakeCtrl(["piano"])
            m = self.make(ctrl, d)
            player = self.fake_player(m)
            m._on_music_play()
        player.play.assert_called_once()
        self.assertEqual(ctrl.calls, [("start", "piano")])

    def test_play_without_piano_sequence_still_plays(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.wav").write_bytes(b"x")
            ctrl = FakeCtrl([])
            m = self.make(ctrl, d)
            player = self.fake_player(m)
            m._on_music_play()
        player.play.assert_called_once()

    def test_play_empty_folder_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.make(FakeCtrl(["piano"]), d)
            player = self.fake_player(m)
            m._on_music_play()
        player.play.assert_not_called()
        m._set_dialogue.assert_called_once()

    def test_stop_stops_player_and_piano(self):
        ctrl = FakeCtrl(["piano"], current="piano")
        m = self.make(ctrl)
        player = self.fake_player(m, playing=True)
        m._on_music_stop()
        player.stop.assert_called_once()
        self.assertEqual(ctrl.calls, [("stop", "piano")])

    def test_next_and_shutdown(self):
        m = self.make()
        player = self.fake_player(m, playing=True)
        m._on_music_next()
        player.next.assert_called_once()
        m._music_shutdown()
        player.stop.assert_called_once()

    def test_shutdown_without_player(self):
        self.make()._music_shutdown()  # 沒建過播放器也不能出錯

    def test_choose_folder_saves_pref(self):
        m = self.make()
        with mock.patch.object(pet_music.filedialog, "askdirectory", return_value="D:/music"):
            m._on_music_choose_folder()
        self.assertEqual(self.saved, [{"music_folder": "D:/music"}])

    def test_choose_folder_cancel_saves_nothing(self):
        m = self.make()
        with mock.patch.object(pet_music.filedialog, "askdirectory", return_value=""):
            m._on_music_choose_folder()
        self.assertEqual(self.saved, [])

    def test_menu_states(self):
        m = self.make(folder="D:/music")
        m._music_menu = mock.Mock()
        self.fake_player(m, playing=False)
        m._refresh_music_menu()
        st = {c.args[0]: c.kwargs["state"] for c in m._music_menu.entryconfigure.call_args_list}
        self.assertEqual(st, {2: "normal", 3: "disabled", 4: "disabled"})
        m._music_player.is_playing.return_value = True
        m._music_menu.reset_mock()
        m._refresh_music_menu()
        st = {c.args[0]: c.kwargs["state"] for c in m._music_menu.entryconfigure.call_args_list}
        self.assertEqual(st, {2: "disabled", 3: "normal", 4: "normal"})

    def test_menu_play_disabled_without_folder(self):
        m = self.make()
        m._music_menu = mock.Mock()
        m._refresh_music_menu()
        st = {c.args[0]: c.kwargs["state"] for c in m._music_menu.entryconfigure.call_args_list}
        self.assertEqual(st[2], "disabled")


if __name__ == "__main__":
    unittest.main()
