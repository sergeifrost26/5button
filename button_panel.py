import ctypes
import json
import os
import sys
import threading
import tkinter as tk

import keyboard  # pip install keyboard

N_BUTTONS = 5  # поменяй на 3, если нужно три кнопки

BASE = os.path.dirname(os.path.abspath(sys.argv[0]))
CFG_PATH = os.path.join(BASE, "panel_config.json")

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000


class Panel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Панель")
        self.root.attributes("-topmost", True)  # поверх всех окон
        self.root.resizable(False, False)
        self.assigning = False

        self.cfg = self.load_cfg()
        if self.cfg.get("pos"):
            self.root.geometry(self.cfg["pos"])

        self.buttons = []
        for i in range(N_BUTTONS):
            b = tk.Button(
                self.root, width=18, height=2,
                command=lambda i=i: self.press(i),
            )
            b.bind("<Button-3>", lambda e, i=i: self.assign(i))  # ПКМ = назначить
            b.pack(padx=6, pady=3)
            self.buttons.append(b)
            self.refresh(i)

        tk.Label(
            self.root, text="ЛКМ — нажать, ПКМ — назначить",
            fg="gray", font=("Segoe UI", 8),
        ).pack(pady=(0, 4))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.update_idletasks()
        self.make_noactivate()

    # окно не забирает фокус, поэтому клавиши уходят в то окно, где ты работаешь
    def make_noactivate(self):
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)

    def load_cfg(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        keys = cfg.get("keys", [])
        keys += [""] * (N_BUTTONS - len(keys))
        cfg["keys"] = keys[:N_BUTTONS]
        return cfg

    def save_cfg(self):
        self.cfg["pos"] = "+%d+%d" % (self.root.winfo_x(), self.root.winfo_y())
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def refresh(self, i):
        combo = self.cfg["keys"][i]
        self.buttons[i].config(
            text="Кнопка %d\n%s" % (i + 1, combo.upper() if combo else "не назначено")
        )

    def press(self, i):
        combo = self.cfg["keys"][i]
        if combo and not self.assigning:
            keyboard.send(combo)

    def assign(self, i):
        if self.assigning:
            return
        self.assigning = True
        self.buttons[i].config(text="Нажми сочетание…\n(Esc — очистить)")

        def worker():
            try:
                combo = keyboard.read_hotkey(suppress=True)
            except Exception:
                combo = None
            self.root.after(0, lambda: self.done(i, combo))

        threading.Thread(target=worker, daemon=True).start()

    def done(self, i, combo):
        if combo == "esc":
            self.cfg["keys"][i] = ""
        elif combo:
            self.cfg["keys"][i] = combo
        self.assigning = False
        self.refresh(i)
        self.save_cfg()

    def on_close(self):
        self.save_cfg()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Panel().run()
