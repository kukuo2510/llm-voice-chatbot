"""股票選擇小視窗：三支虛構股票加一個「算了」，選完（或關掉視窗）呼叫一次 on_choose。"""

import tkinter as tk

from pet_config import COLOR_ACCENT, COLOR_PANEL, COLOR_TEXT


def open_stock_picker(root, names, on_choose) -> tk.Toplevel:
    """在角色旁邊開選擇視窗。on_choose(股票名稱或 None) 只會被呼叫一次；None 表示放棄。"""
    win = tk.Toplevel(root)
    win.title("選股票")
    win.configure(bg=COLOR_PANEL)
    win.attributes("-topmost", True)
    win.resizable(False, False)

    done = []

    def choose(value) -> None:
        if done:
            return
        done.append(1)
        try:
            win.destroy()
        except Exception:
            pass
        on_choose(value)

    tk.Label(win, text="要買哪一支？", bg=COLOR_PANEL, fg=COLOR_TEXT).pack(padx=16, pady=(12, 6))
    for name in names:
        tk.Button(win, text=name, width=16, bg=COLOR_PANEL, fg=COLOR_TEXT,
                  activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
                  command=lambda n=name: choose(n)).pack(padx=16, pady=2)
    tk.Button(win, text="算了", width=16, bg=COLOR_PANEL, fg=COLOR_TEXT,
              activebackground=COLOR_ACCENT, activeforeground=COLOR_TEXT,
              command=lambda: choose(None)).pack(padx=16, pady=(2, 12))
    win.protocol("WM_DELETE_WINDOW", lambda: choose(None))

    # 放在角色右邊；右邊放不下就放左邊
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = root.winfo_x() + root.winfo_width() + 8
    if x + w > root.winfo_screenwidth():
        x = max(0, root.winfo_x() - w - 8)
    y = max(0, min(root.winfo_y() + root.winfo_height() // 3, root.winfo_screenheight() - h))
    win.geometry(f"+{x}+{y}")
    return win
