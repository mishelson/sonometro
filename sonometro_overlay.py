import ctypes
import math
import sys
import tkinter as tk
from ctypes import wintypes

from sonometro_core import COLORS


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        raise RuntimeError("La superposicion requiere Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if hasattr(user32, "SetProcessDpiAwarenessContext"):
        function = user32.SetProcessDpiAwarenessContext
        function.argtypes = [wintypes.HANDLE]
        function.restype = wintypes.BOOL
        if not function(ctypes.c_void_p(-4)) and ctypes.get_last_error() != 5:
            raise ctypes.WinError(ctypes.get_last_error())
    elif not user32.SetProcessDPIAware():
        raise ctypes.WinError(ctypes.get_last_error())


class Overlay:
    BANDS = 6

    def __init__(self, root: tk.Tk) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.get_root = self.user32.GetAncestor
        self.get_root.argtypes = [wintypes.HWND, wintypes.UINT]
        self.get_root.restype = wintypes.HWND
        self.get_style = (self.user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8
                          else self.user32.GetWindowLongW)
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = ctypes.c_ssize_t
        self.set_style = (self.user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8
                          else self.user32.SetWindowLongW)
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        self.position = self.user32.SetWindowPos
        self.position.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.position.restype = wintypes.BOOL
        self.show = self.user32.ShowWindow
        self.show.argtypes = [wintypes.HWND, ctypes.c_int]
        self.show.restype = wintypes.BOOL
        self.windows: list[tuple[tk.Toplevel, int]] = []
        self.strength = 0.0
        self.visible = False
        self.color = COLORS[1]
        self.rgb = [float(int(self.color[i:i + 2], 16)) for i in (1, 3, 5)]
        try:
            for _ in range(4 * self.BANDS):
                window = tk.Toplevel(root)
                window.withdraw()
                window.overrideredirect(True)
                window.configure(bg=self.color)
                window.attributes("-alpha", 0.0)
                window.update_idletasks()
                handle = self.get_root(window.winfo_id(), 2)
                if not handle:
                    window.destroy()
                    raise ctypes.WinError(ctypes.get_last_error())
                self.windows.append((window, handle))
                style = self.get_style(handle, -20)
                ctypes.set_last_error(0)
                result = self.set_style(handle, -20, style | 0x80000 | 0x20 | 0x8000000 | 0x80)
                if not result and ctypes.get_last_error():
                    raise ctypes.WinError(ctypes.get_last_error())
        except (OSError, tk.TclError):
            self.destroy()
            raise

    def render(self, target: float, state: int, opacity: float, depth: float, dt: float) -> None:
        tau = 0.13 if target > self.strength else 0.35
        self.strength += (target - self.strength) * (1 - math.exp(-max(0, dt) / tau))
        if self.strength < 0.005:
            self.hide()
            return
        if state:
            target_color = COLORS[state]
            mix = 1 - math.exp(-max(0, dt) / 0.15)
            for i, offset in enumerate((1, 3, 5)):
                self.rgb[i] += (int(target_color[offset:offset + 2], 16) - self.rgb[i]) * mix
            self.color = "#" + "".join(f"{round(channel):02x}" for channel in self.rgb)
        width = self.user32.GetSystemMetrics(0)
        height = self.user32.GetSystemMetrics(1)
        thickness = max(self.BANDS, round(min(width, height) * depth * self.strength))
        for side in range(4):
            for band in range(self.BANDS):
                start = round(thickness * band / self.BANDS)
                end = round(thickness * (band + 1) / self.BANDS)
                size = max(1, end - start)
                if side == 0:
                    geometry = (0, start, width, size)
                elif side == 1:
                    geometry = (0, height - end, width, size)
                elif side == 2:
                    geometry = (start, thickness, size, max(1, height - 2 * thickness))
                else:
                    geometry = (width - end, thickness, size, max(1, height - 2 * thickness))
                window, handle = self.windows[side * self.BANDS + band]
                window.configure(bg=self.color)
                window.attributes("-alpha", opacity * self.strength ** 0.4
                                  * (1 - band / self.BANDS) ** 1.4)
                if not self.position(handle, wintypes.HWND(-1), *geometry, 0x10 | 0x40):
                    raise ctypes.WinError(ctypes.get_last_error())
        self.visible = True

    def hide(self) -> None:
        if self.visible:
            for _, handle in self.windows:
                self.show(handle, 0)
        self.strength = 0.0
        self.visible = False

    def destroy(self) -> None:
        for window, _ in self.windows:
            window.destroy()
        self.windows.clear()
