import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

# ---------- настройки внешнего вида ----------
N_BUTTONS = 5    # количество кнопок
D = 50           # диаметр круглой кнопки, пикселей
GAP = 10         # расстояние между кнопками
PAD_X = 12       # отступ слева
STRIP_H = 22     # высота верхней полоски (за неё двигаем окно)
CAPTURE_TIMEOUT = 10  # сколько секунд ждать нажатия при назначении

BG = "#eceef1"
STRIP = "#dcdfe4"
STRIP_HOVER = "#c9cdd3"
CLOSE_HOVER = "#e5736c"
SHADOWS = ["#e6e8ec", "#e0e3e7", "#d9dce1", "#d2d6db", "#cbcfd5"]
TEXT = "#2b3440"
TEXT_EMPTY = "#b5bbc4"

W = D + PAD_X + 16
H = STRIP_H + GAP + N_BUTTONS * (D + GAP)

BASE = os.path.dirname(os.path.abspath(sys.argv[0]))
CFG_PATH = os.path.join(BASE, "panel_config.json")

# ---------- Windows API ----------
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p

WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_INJECTED = 0x10

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080


class KBDLL(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


# ---------- клавиши (коды VK не зависят от раскладки) ----------
VK_CTRL, VK_SHIFT, VK_ALT, VK_WIN = 0x11, 0x10, 0x12, 0x5B
MODS = [VK_CTRL, VK_SHIFT, VK_ALT, VK_WIN]
NORMALIZE = {0xA0: VK_SHIFT, 0xA1: VK_SHIFT, 0xA2: VK_CTRL, 0xA3: VK_CTRL,
             0xA4: VK_ALT, 0xA5: VK_ALT, 0x5C: VK_WIN}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x6F}
NAMES = {
    VK_CTRL: "CTRL", VK_SHIFT: "SHIFT", VK_ALT: "ALT", VK_WIN: "WIN",
    0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER", 0x14: "CAPS", 0x1B: "ESC",
    0x20: "SPACE", 0x21: "PGUP", 0x22: "PGDN", 0x23: "END", 0x24: "HOME",
    0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN", 0x2C: "PRTSC",
    0x2D: "INS", 0x2E: "DEL", 0x6A: "NUM*", 0x6B: "NUM+", 0x6D: "NUM-",
    0x6E: "NUM.", 0x6F: "NUM/", 0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-",
    0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}


def vk_name(vk):
    if vk in NAMES:
        return NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    if 0x60 <= vk <= 0x69:
        return "NUM%d" % (vk - 0x60)
    return "VK%d" % vk


def combo_name(vks):
    return "+".join(vk_name(v) for v in vks)


def order_combo(vks):
    mods = [v for v in MODS if v in vks]
    rest = [v for v in vks if v not in MODS]
    return mods + rest


def _key_event(vk, up):
    flags = 2 if up else 0
    if vk in EXTENDED:
        flags |= 1
    user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), flags, 0)


def send_combo(vks):
    for vk in vks:
        _key_event(vk, False)
        time.sleep(0.01)
    time.sleep(0.03)
    for vk in reversed(vks):
        _key_event(vk, True)
        time.sleep(0.01)


def capture_combo(timeout):
    """Ждёт сочетание клавиш (все нажатые клавиши, пока их не отпустят).
    Нажатия при этом НЕ уходят в другие программы. Возвращает список VK."""
    pressed, chosen = set(), []
    result = []
    tid = kernel32.GetCurrentThreadId()

    def proc(n_code, w_param, l_param):
        try:
            if n_code == 0:
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLL)).contents
                if kb.flags & LLKHF_INJECTED:  # нажатия, которые отправили мы сами
                    return user32.CallNextHookEx(None, n_code, w_param, l_param)
                vk = NORMALIZE.get(kb.vkCode, kb.vkCode)
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    pressed.add(vk)
                    if vk not in chosen:
                        chosen.append(vk)
                elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                    pressed.discard(vk)
                    if not pressed and chosen:
                        result.extend(chosen)
                        user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
                return 1  # съедаем клавишу
        except Exception:
            pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    callback = HOOKPROC(proc)  # ссылку надо держать, иначе уберёт сборщик мусора
    msg = wintypes.MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # создаём очередь сообщений потока
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        return []
    timer = threading.Timer(timeout, lambda: user32.PostThreadMessageW(tid, WM_QUIT, 0, 0))
    timer.daemon = True
    timer.start()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    timer.cancel()
    user32.UnhookWindowsHookEx(hook)
    return order_combo(result)


class Panel:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Панель")
        self.root.attributes("-topmost", True)
        self.assigning = False
        self.assigning_index = -1
        self.minimized = False
        self.last_fg = None
        self.q = queue.Queue()
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
        self.poll_queue()
        self.track_foreground()

    # ---------- интерфейс ----------
    def build(self):
        c = self.canvas = tk.Canvas(
            self.root, width=W, height=H, bg=BG, highlightthickness=0, bd=0
        )
        c.pack()

        c.create_rectangle(0, 0, W, STRIP_H, fill=STRIP, outline="", tags="drag")
        for k in range(3):
            x = 8 + k * 6
            c.create_oval(x, 9, x + 3, 12, fill="#9aa1ab", outline="", tags="drag")
        c.tag_bind("drag", "<ButtonPress-1>", self.drag_start)
        c.tag_bind("drag", "<B1-Motion>", self.drag_move)

        self.small_button("min", W - 40, W - 20, "–", STRIP_HOVER, self.minimize)
        self.small_button("close", W - 20, W, "×", CLOSE_HOVER, self.close)

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
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, i=i: self.press(i))
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
        vks = self.cfg["combos"][i]
        if self.assigning_index == i:
            text, font, fill = "…", ("Segoe UI", 14), TEXT
        elif vks:
            text = combo_name(vks).replace("+", "+\n")
            font, fill = ("Segoe UI", 7, "bold"), TEXT
        else:
            text, font, fill = str(i + 1), ("Segoe UI", 13), TEXT_EMPTY
        self.canvas.itemconfig(self.texts[i], text=text, font=font, fill=fill)

    def down(self, i):
        self.canvas.itemconfig(self.ovals[i], fill="#e4e7eb")

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
    def get_hwnd(self):
        return user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()

    def apply_styles(self, noactivate):
        hwnd = self.get_hwnd()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        if noactivate:
            style |= WS_EX_NOACTIVATE
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

    # запоминаем, какое окно было активным до нашей панели
    def track_foreground(self):
        try:
            fg = user32.GetForegroundWindow()
            if fg and fg != self.get_hwnd():
                self.last_fg = fg
        except Exception:
            pass
        self.root.after(150, self.track_foreground)

    # ---------- настройки ----------
    def load_cfg(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        combos = cfg.get("combos", [])
        combos = [c if isinstance(c, list) else [] for c in combos]
        combos += [[] for _ in range(N_BUTTONS - len(combos))]
        cfg["combos"] = combos[:N_BUTTONS]
        cfg.pop("keys", None)  # старый формат из прошлой версии не подходит
        return cfg

    def save_cfg(self):
        self.cfg["pos"] = "+%d+%d" % self.pos
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    # ---------- кнопки ----------
    def press(self, i):
        vks = self.cfg["combos"][i]
        if not vks or self.assigning:
            return
        own = self.get_hwnd()
        threading.Thread(target=self.send_worker, args=(vks, own), daemon=True).start()

    def send_worker(self, vks, own):
        fg = user32.GetForegroundWindow()
        if fg == own and self.last_fg:  # фокус случайно у панели — вернуть прошлому окну
            user32.SetForegroundWindow(self.last_fg)
            time.sleep(0.08)
        send_combo(vks)

    def assign(self, i):
        if self.assigning:
            return
        self.assigning = True
        self.assigning_index = i
        self.refresh(i)

        def worker():
            try:
                vks = capture_combo(CAPTURE_TIMEOUT)
            except Exception:
                vks = []
            self.q.put((i, vks))

        threading.Thread(target=worker, daemon=True).start()

    def poll_queue(self):
        try:
            while True:
                i, vks = self.q.get_nowait()
                self.done(i, vks)
        except queue.Empty:
            pass
        self.root.after(50, self.poll_queue)

    def done(self, i, vks):
        if vks == [0x1B]:      # одиночный Esc — очистить кнопку
            self.cfg["combos"][i] = []
        elif vks:
            self.cfg["combos"][i] = vks
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
