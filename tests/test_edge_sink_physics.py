"""邊緣坐下與支撐線下沉：走位計算、anchor_y 換算、find_support、物理 tick 的單元測試。

全部用假矩形、假視窗物件、合成圖，不碰真實 Tk、不寫任何真實狀態檔。
用法：python -m unittest tests.test_edge_sink_physics   （在專案根目錄）
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "desktop_pet"))
sys.path.insert(0, str(ROOT / "scripts"))

import desktop_physics as dp  # noqa: E402
import pet_anim_frames as pf  # noqa: E402
import pet_edge  # noqa: E402
import pet_physics as pp  # noqa: E402
import pet_sink  # noqa: E402


class EdgeMathTest(unittest.TestCase):
    def test_plan_nearest_edge(self):
        # 視窗寬 200、支撐物 [100, 700]：中心靠左 -> 左端、面向 left；中心靠右 -> 右端、面向 right
        self.assertEqual(pet_edge.plan_edge(150, 200, 100, 700, 1920), (0, "left"))
        self.assertEqual(pet_edge.plan_edge(450, 200, 100, 700, 1920), (600, "right"))

    def test_plan_clamped_to_screen(self):
        # 工作區地面：左端 0 -> 視窗中心對齊 0 會超出螢幕，鉗在 0
        self.assertEqual(pet_edge.plan_edge(30, 200, 0, 1920, 1920), (0, "left"))
        self.assertEqual(pet_edge.plan_edge(1700, 200, 0, 1920, 1920), (1720, "right"))

    def test_step_toward(self):
        self.assertEqual(pet_edge.step_toward(100, 200, 10), (110, False))
        self.assertEqual(pet_edge.step_toward(100, 50, 10), (90, False))
        self.assertEqual(pet_edge.step_toward(195, 200, 10), (200, True))
        self.assertEqual(pet_edge.step_toward(200, 200, 10), (200, True))


class SinkTest(unittest.TestCase):
    def frame(self, margin_top=20, margin_bottom=20, w=100, h=200):
        """有透明邊的原圖：內容在 [margin_top, h - margin_bottom)。"""
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        im.paste((255, 0, 0, 255), (10, margin_top, w - 10, h - margin_bottom))
        return im

    def test_sink_px_formula(self):
        self.assertEqual(pet_sink.sink_px(0.69, 100), 31)
        self.assertEqual(pet_sink.sink_px(1.0, 100), 0)
        self.assertEqual(pet_sink.sink_px(0.0, 100), 100)
        self.assertEqual(pet_sink.sink_px(None, 100), 0)
        self.assertEqual(pet_sink.sink_px(1.5, 100), 0)

    def test_content_height_ignores_transparent_margin(self):
        # 內容 160 高、原圖 200 高（上下各 20 透明）；正規化成可見身高 80
        frames = pf.normalize_set([self.frame()], 80, (100, 100), 50, 100)
        self.assertEqual(pet_sink.content_height(frames), 80)  # 不是畫布高 100，也不是原圖比例換算的 100

    def test_support_line_lands_on_surface(self):
        """支撐線（內容高度 anchor_y 處）加上下沉量後，剛好落在「腳底基準線」原本的位置。"""
        target_h, canvas, baseline = 80, (100, 100), 100
        frames = pf.normalize_set([self.frame(margin_top=20, margin_bottom=20)], target_h, canvas, 50, baseline)
        box = pf.union_bbox(frames)
        top, bottom = box[1], box[3]
        self.assertEqual(bottom, baseline)
        h = pet_sink.content_height(frames)
        anchor = 0.69
        support_row = top + anchor * h  # 圖上支撐線所在的列（視窗座標）
        sink = pet_sink.sink_px(anchor, h)
        self.assertEqual(sink, 25)
        # 腳底（baseline）比支撐線低 (bottom - support_row) ≈ 24.8 px；視窗下沉這麼多，支撐線就會落在原本腳底的位置
        self.assertAlmostEqual(bottom - support_row, sink, delta=0.5)
        surface = 1000  # 落腳面
        window_bottom = surface + sink
        support_y = window_bottom - (canvas[1] - support_row)  # 支撐線在螢幕上的 y
        normal_feet_y = surface - (canvas[1] - bottom)  # 正常站立時腳底的 y
        self.assertAlmostEqual(support_y, normal_feet_y, delta=0.5)

    def test_height_ratio_shrinks_sink(self):
        small = pf.normalize_set([self.frame()], 40, (100, 100), 50, 100)  # height_ratio 0.5 的效果
        self.assertEqual(pet_sink.sink_px(0.69, pet_sink.content_height(small)), 12)


class SupportTest(unittest.TestCase):
    def patched(self, rects):
        return (mock.patch.object(dp, "work_area", lambda: (0, 0, 1920, 1040)),
                mock.patch.object(dp, "_visible_window_rects", lambda hwnd: rects))

    def run_support(self, rects, *args):
        a, b = self.patched(rects)
        with a, b:
            return dp.find_support(*args, None), dp.find_landing_y(*args, None)

    def test_ground_when_no_window(self):
        self.assertEqual(self.run_support([], 100, 300, 500)[0], (1040, 0, 1920))

    def test_window_top_edges(self):
        rects = [(100, 300, 800, 700), (200, 500, 600, 900)]
        (sup, y), = [self.run_support(rects, 150, 300, 250)]
        self.assertEqual(sup, (300, 100, 800))
        self.assertEqual(y, 300)
        sup2, _ = self.run_support(rects, 150, 300, 498)  # 更低的視窗：頂邊 300 已在腳上方，改站 500 那個
        self.assertEqual(sup2, (500, 200, 600))

    def test_no_horizontal_overlap_falls_to_ground(self):
        sup, y = self.run_support([(1000, 300, 1500, 700)], 100, 300, 250)
        self.assertEqual(sup, (1040, 0, 1920))
        self.assertEqual(y, 1040)


class FakeRoot:
    def __init__(self, x, y, w, h, screen_w=1920):
        self.x, self.y, self.w, self.h, self.screen_w = x, y, w, h, screen_w
        self.geoms = []

    def winfo_x(self): return self.x
    def winfo_y(self): return self.y
    def winfo_width(self): return self.w
    def winfo_height(self): return self.h
    def winfo_screenwidth(self): return self.screen_w
    def after(self, *a, **k): pass

    def geometry(self, s):
        self.geoms.append(s)
        x, y = s.lstrip("+").split("+")
        self.x, self.y = int(x), int(y)


class Var:
    def get(self): return True


class FakePhys(pp.PhysicsMixin):
    def __init__(self, root, sink=0, ctrl=None):
        self.root = root
        self._physics_enabled_var = Var()
        self._mouse_down = False
        self._hwnd = None
        self._fall_velocity = 0.0
        self._busy = False
        self._input_popup = None
        self._walk_seconds_left = 0.0
        self._facing = "right"
        self._next_wander_at = 1e18
        self._sink = sink
        self._anim_ctrl = ctrl

    def _anim_sink(self): return self._sink
    def _position_input_popup(self): pass


class PhysicsSinkTest(unittest.TestCase):
    def tick(self, win, support=(600, 0, 1920)):
        with mock.patch.object(pp.desktop_physics, "find_support", lambda *a: support):
            win._physics_tick()

    def test_normal_feet_on_surface(self):
        root = FakeRoot(100, 300, 200, 300)  # 底 600 = 落腳面
        self.tick(FakePhys(root))
        self.assertEqual(root.geoms, [])  # 已經站好，不動

    def test_sink_lowers_window_bottom_below_surface(self):
        root = FakeRoot(100, 300, 200, 300)
        win = FakePhys(root, sink=25)
        for _ in range(60):
            self.tick(win)
        self.assertEqual(root.y + root.h, 625)  # 視窗底部低於落腳面 25px
        n = len(root.geoms)
        self.tick(win)
        self.assertEqual(len(root.geoms), n)  # 沉好之後穩定，不會被當成卡進視窗而繼續掉

    def test_sink_does_not_fall_through_support_window(self):
        root = FakeRoot(100, 325, 200, 300)  # 底 625，已沉 25
        seen = []
        with mock.patch.object(pp.desktop_physics, "find_support",
                               lambda l, r, bottom, hwnd: seen.append(bottom) or (600, 0, 1920)):
            FakePhys(root, sink=25)._physics_tick()
        self.assertEqual(seen, [600])  # 找落腳面用的是扣掉下沉量的底部

    def test_sink_released_returns_to_surface(self):
        root = FakeRoot(100, 325, 200, 300)
        self.tick(FakePhys(root, sink=0))
        self.assertEqual(root.y + root.h, 600)


class PhysicsEdgeWalkTest(unittest.TestCase):
    class Ctrl:
        resting = False

        def __init__(self, pending="sit"):
            self.pending_edge, self.arrived = pending, 0

        def edge_arrived(self): self.arrived += 1

    def make(self, x, ctrl, span=(0, 500), w=200):
        win = FakePhys(FakeRoot(x, 300, w, 300), ctrl=ctrl)
        win._support_span = span
        return win

    def test_walks_toward_nearest_edge_facing_travel(self):
        ctrl = self.Ctrl()
        win = self.make(100, ctrl)  # 中心 200，左端 0 比右端 500 近
        new_x = win._advance_wander(100, 200, 0.1)
        self.assertLess(new_x, 100)
        self.assertEqual(win._facing, "left")
        self.assertEqual(ctrl.arrived, 0)

    def test_arrives_faces_outward_and_notifies(self):
        ctrl = self.Ctrl()
        win = self.make(400, ctrl)  # 中心 500 = 右端，目標 x=400
        new_x = win._advance_wander(400, 200, 0.1)
        self.assertEqual(new_x, 400)
        self.assertEqual(win._facing, "right")
        self.assertEqual(ctrl.arrived, 1)

    def test_left_edge_faces_left(self):
        ctrl = self.Ctrl()
        win = self.make(0, ctrl, span=(100, 700))  # 中心 100 = 左端，目標 x=0（鉗在螢幕內）
        win._facing = "right"
        win._advance_wander(0, 200, 0.1)
        self.assertEqual(win._facing, "left")
        self.assertEqual(ctrl.arrived, 1)

    def test_paused_when_busy(self):
        ctrl = self.Ctrl()
        win = self.make(100, ctrl)
        win._busy = True
        self.assertEqual(win._advance_wander(100, 200, 0.1), 100)

    def test_no_pending_keeps_old_wander(self):
        win = self.make(100, self.Ctrl(pending=None))
        self.assertEqual(win._advance_wander(100, 200, 0.1), 100)  # 沒在漫遊倒數，原地不動


if __name__ == "__main__":
    unittest.main()
