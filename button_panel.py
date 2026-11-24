import ctypes
import json
import os
import re
import sys
import threading
import tkinter as tk

import keyboard  # pip install keyboard

# ---------- настройки внешнего вида ----------
N_BUTTONS = 5    # количество кнопок
D = 50           # диаметр круглой кнопки, пикселей
GAP = 10         # расстояние между кнопками
PAD_X = 12       # отступ слева
STRIP_H = 22     # высота верхней полоски (за неё двигаем окно)

BG = "#eceef1"
STRIP = "#dcdfe4"
STRIP_HOVER = "#c9cdd3"
CLOSE_HOVER = "#e5736c"
SHADOWS = ["#e6e8ec", "#e0e3e7", "#d9dce1", "#d2d6db", "#cbcfd5"]  # от дальней к ближней
TEXT = "#2b3440"
TEXT_EMPTY = "#b5bbc4"

W = D + PAD_X + 16
H = STRIP_H + GAP + N_BUTTONS * (D + GAP)

BASE = os.path.dirname(os.path.abspath(sys.argv[0]))
CFG_PATH = os.path.join(BASE, "panel_config.json")

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080


class Panel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Панель")
        self.root.attributes("-topmost", True)
        self.assigning = False
        self.assigning_index = -1
        self.minimized = False
        self._dx = self._dy = 0

        self.cfg = self.load_cfg()
        self.pos = (100, 100)
        m = re.match(r"\+(-?\d+)\+(-?\d+)", self.cfg.get("pos", ""))
        if m:
            self.pos = (int(m.group(1)), int(m.group(2)))

        self.root.overrideredirect(True)  # без рамки, свой верх
        self.root.geometry("+%d+%d" % self.pos)
        self.build()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Map>", self.on_map)
        self.root.update_idletasks()
        self.apply_styles(True)
        self.reshow()

    # ---------- интерфейс ----------
    def build(self):
        c = self.canvas = tk.Canvas(
            self.root, width=W, height=H, bg=BG, highlightthickness=0, bd=0
        )
        c.pack()

        # верхняя полоска: за неё можно взять и перетащить окно
        c.create_rectangle(0, 0, W, STRIP_H, fill=STRIP, outline="", tags="drag")
        for k in range(3):
            x = 8 + k * 6
            c.create_oval(x, 9, x + 3, 12, fill="#9aa1ab", outline="", tags="drag")
        c.tag_bind("drag", "<ButtonPress-1>", self.drag_start)
        c.tag_bind("drag", "<B1-Motion>", self.drag_move)

        # свернуть и закрыть
        self.small_button("min", W - 40, W - 20, "–", STRIP_HOVER, self.minimize)
        self.small_button("close", W - 20, W, "×", CLOSE_HOVER, self.close)

        # круглые кнопки
        self.ovals, self.texts = [], []
        for i in range(N_BUTTONS):
            x0 = PAD_X
            y0 = STRIP_H + GAP + i * (D + GAP)
            x1, y1 = x0 + D, y0 + D
            tag = "b%d" % i
            for grow, col in zip((5, 4, 3, 2, 1), SHADOWS):
                c.create_oval(
                    x0 - grow + 3, y0 - grow + 5, x1 + grow + 3, y1 + grow + 5,
                    fill=col, outline="", tags=tag,
                )
            ov = c.create_oval(x0, y0, x1, y1, fill="#ffffff", outline="#f6f7f9", tags=tag)
            tx = c.create_text(
                (x0 + x1) / 2, (y0 + y1) / 2, text="", justify="center",
                width=D - 8, tags=tag,
            )
            c.tag_bind(tag, "<ButtonPress-1>", lambda e, i=i: self.down(i))
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, i=i: self.up(i))
            c.tag_bind(tag, "<Button-3>", lambda e, i=i: self.assign(i))
            self.ovals.append(ov)
            self.texts.append(tx)
            self.refresh(i)
        c.bind("<ButtonRelease-1>", lambda e: self.reset_fills(), add="+")

    def small_button(self, tag, x0, x1, label, hover, command):
        c = self.canvas
        rect = c.create_rectangle(x0, 0, x1, STRIP_H, fill=STRIP, outline="", tags=tag)
        c.create_text((x0 + x1) / 2, STRIP_H / 2 - 1, text=label, fill=TEXT,
                      font=("Segoe UI", 11), tags=tag)
        c.tag_bind(tag, "<Enter>", lambda e: c.itemconfig(rect, fill=hover))
        c.tag_bind(tag, "<Leave>", lambda e: c.itemconfig(rect, fill=STRIP))
        c.tag_bind(tag, "<ButtonRelease-1>", lambda e: command())

    def refresh(self, i):
        combo = self.cfg["keys"][i]
        if self.assigning_index == i:
            text, font, fill = "…", ("Segoe UI", 14), TEXT
        elif combo:
            text = combo.upper().replace("+", "+\n")
            font, fill = ("Segoe UI", 7, "bold"), TEXT
        else:
            text, font, fill = str(i + 1), ("Segoe UI", 13), TEXT_EMPTY
        self.canvas.itemconfig(self.texts[i], text=text, font=font, fill=fill)

    def down(self, i):
        self.canvas.itemconfig(self.ovals[i], fill="#e4e7eb")

    def up(self, i):
        self.press(i)

    def reset_fills(self):
        for ov in self.ovals:
            self.canvas.itemconfig(ov, fill="#ffffff")

    # ---------- перемещение ----------
    def drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def drag_move(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        self.root.geometry("+%d+%d" % (x, y))
        self.pos = (x, y)

    # ---------- окно: значок в панели задач, сворачивание ----------
    def apply_styles(self, noactivate):
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        if noactivate:
            style |= WS_EX_NOACTIVATE   # клик не забирает фокус у вашей программы
        else:
            style &= ~WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def reshow(self):
        self.root.withdraw()
        self.root.after(40, self.root.deiconify)

    def minimize(self):
        self.pos = (self.root.winfo_x(), self.root.winfo_y())
        self.minimized = True
        self.apply_styles(False)
        self.root.overrideredirect(False)
        self.root.update_idletasks()
        self.root.iconify()

    def on_map(self, e):
        if e.widget is self.root and self.minimized:
            self.minimized = False
            self.root.after(30, self.restore)

    def restore(self):
        self.root.overrideredirect(True)
        self.root.geometry("+%d+%d" % self.pos)
        self.root.attributes("-topmost", True)
        self.root.update_idletasks()
        self.apply_styles(True)
        self.reshow()

    # ---------- настройки ----------
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
        self.cfg["pos"] = "+%d+%d" % self.pos
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    # ---------- кнопки ----------
    def press(self, i):
        combo = self.cfg["keys"][i]
        if combo and not self.assigning:
            keyboard.send(combo)

    def assign(self, i):
        if self.assigning:
            return
        self.assigning = True
        self.assigning_index = i
        self.refresh(i)

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
        self.assigning_index = -1
        self.refresh(i)
        self.save_cfg()

    def close(self):
        if not self.minimized:
            self.pos = (self.root.winfo_x(), self.root.winfo_y())
        self.save_cfg()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Panel().run()
