import ctypes
import math
import sys
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np

from sonometro_core import ramp_color


WS_EX_LAYERED_CLICKTHROUGH = 0x80000 | 0x20 | 0x8000000 | 0x80
MAX_DEPTH = 0.15
# The fade continues past the configured depth so the inner edge never looks like a line.
FADE_REACH = 2.3
# Red reaches a little further inward than the configured depth.
RED_REACH = 1.2
# While green the alert is only a thin outline; it starts growing inward once it turns yellow.
GREEN_END = 0.4
OUTLINE_REACH = 0.014
CORNER_SHARPNESS = 6.0


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


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def edge_distance(ys: np.ndarray, xs: np.ndarray, width: int, height: int, sharpness: float) -> np.ndarray:
    """Distance in pixels from the nearest screen edge.

    A soft minimum (p-norm) blends the two edges at the corners: no diagonal crease, and the
    corner only reaches about 12% further than the straight edges.
    """
    dx = np.minimum(xs, width - 1 - xs).astype(np.float64) + 1
    dy = np.minimum(ys, height - 1 - ys).astype(np.float64) + 1
    return np.maximum(0, (dx ** -sharpness + dy ** -sharpness) ** (-1 / sharpness) - 1)


class Overlay:
    def __init__(self, root: tk.Tk) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._bind_api()
        self.strength = 0.0
        self.visible = False
        self.size = (0, 0)
        self.memory_dc = None
        self.bitmap = None
        self.old_bitmap = None
        self.pixels: np.ndarray | None = None
        self.strips: list[tuple[tuple[slice, slice], np.ndarray]] = []
        self.margin = 0
        self.last_key: tuple | None = None
        self.started = time.monotonic()
        self.window = tk.Toplevel(root)
        try:
            self.window.withdraw()
            self.window.overrideredirect(True)
            self.window.update_idletasks()
            self.handle = self.get_root(self.window.winfo_id(), 2)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            style = self.get_style(self.handle, -20)
            ctypes.set_last_error(0)
            result = self.set_style(self.handle, -20, style | WS_EX_LAYERED_CLICKTHROUGH)
            if not result and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            self._ensure_surface()
        except (OSError, tk.TclError):
            self.destroy()
            raise

    @property
    def windows(self) -> list[tuple[tk.Toplevel, int]]:
        return [(self.window, self.handle)] if self.window is not None else []

    def _bind_api(self) -> None:
        u, g = self.user32, self.gdi32
        self.get_root = u.GetAncestor
        self.get_root.argtypes = [wintypes.HWND, wintypes.UINT]
        self.get_root.restype = wintypes.HWND
        wide = ctypes.sizeof(ctypes.c_void_p) == 8
        self.get_style = u.GetWindowLongPtrW if wide else u.GetWindowLongW
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = ctypes.c_ssize_t
        self.set_style = u.SetWindowLongPtrW if wide else u.SetWindowLongW
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        self.position = u.SetWindowPos
        self.position.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.position.restype = wintypes.BOOL
        self.show = u.ShowWindow
        self.show.argtypes = [wintypes.HWND, ctypes.c_int]
        self.show.restype = wintypes.BOOL
        self.update_layered = u.UpdateLayeredWindow
        self.update_layered.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                        ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                        ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        self.update_layered.restype = wintypes.BOOL
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteObject.restype = wintypes.BOOL
        g.DeleteDC.argtypes = [wintypes.HDC]
        g.DeleteDC.restype = wintypes.BOOL

    def _release_surface(self) -> None:
        self.pixels = None
        self.strips = []
        if self.memory_dc:
            if self.old_bitmap:
                self.gdi32.SelectObject(self.memory_dc, self.old_bitmap)
            self.gdi32.DeleteDC(self.memory_dc)
        if self.bitmap:
            self.gdi32.DeleteObject(self.bitmap)
        self.memory_dc = self.bitmap = self.old_bitmap = None
        self.size = (0, 0)

    def _ensure_surface(self) -> None:
        size = (self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1))
        if size == self.size and self.pixels is not None:
            return
        self._release_surface()
        width, height = size
        if width < 16 or height < 16:
            raise OSError("Resolucion de pantalla no valida.")
        self.memory_dc = self.gdi32.CreateCompatibleDC(None)
        if not self.memory_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        self.bitmap = self.gdi32.CreateDIBSection(self.memory_dc, ctypes.byref(header), 0,
                                                  ctypes.byref(bits), None, 0)
        if not self.bitmap or not bits.value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.old_bitmap = self.gdi32.SelectObject(self.memory_dc, self.bitmap)
        buffer = (ctypes.c_uint32 * (width * height)).from_address(bits.value)
        self.pixels = np.ctypeslib.as_array(buffer).reshape(height, width)
        self.pixels[:] = 0
        self.size = size
        smallest = min(width, height)
        self.margin = min(smallest // 2, math.ceil(smallest * MAX_DEPTH * FADE_REACH * RED_REACH) + 2)
        m = self.margin
        regions = [(slice(0, m), slice(0, width)), (slice(height - m, height), slice(0, width)),
                   (slice(m, height - m), slice(0, m)), (slice(m, height - m), slice(width - m, width))]
        self.strips = []
        for rows, cols in regions:
            ys = np.arange(rows.start, rows.stop)[:, None]
            xs = np.arange(cols.start, cols.stop)[None, :]
            distance = edge_distance(ys, xs, width, height, CORNER_SHARPNESS)
            self.strips.append(((rows, cols), np.minimum(np.rint(distance), m).astype(np.uint16)))
        self.last_key = None
        self.window.geometry(f"{width}x{height}+0+0")

    def _reach(self, depth: float) -> float:
        """How far the glow fades inward: a thin outline while green, growing from yellow to red."""
        smallest = min(self.size)
        outline = smallest * OUTLINE_REACH
        if self.strength <= GREEN_END:
            return outline
        growth = ((self.strength - GREEN_END) / (1 - GREEN_END)) ** 0.85
        redness = min(1.0, max(0.0, (self.strength - 0.75) / 0.22))
        full = smallest * depth * FADE_REACH * (1 + (RED_REACH - 1) * redness)
        return outline + (max(outline, full) - outline) * growth

    def _palette(self, opacity: float, depth: float, pulse: float) -> np.ndarray:
        redness = min(1.0, max(0.0, (self.strength - 0.75) / 0.22))
        reach = max(4.0, self._reach(depth))
        s = np.arange(self.margin + 1, dtype=np.float32) / reach
        falloff = np.clip(1 - s, 0, 1)
        # Strongest at the very edge with a long soft tail, like a damage vignette; no plateau or seam.
        # The green outline uses a gentler curve so the thin line stays readable.
        greenness = min(1.0, max(0.0, (GREEN_END + 0.15 - self.strength) / 0.15))
        shape = falloff ** (2.4 - 0.9 * greenness)
        peak = min(0.95, opacity * pulse * (1 + 1.1 * redness + 0.5 * greenness)
                   * (0.35 + 0.65 * self.strength ** 0.5))
        alpha = np.rint(shape * peak * 255).astype(np.uint32)
        color = ramp_color(self.strength)
        r, g, b = (np.uint32(int(color[i:i + 2], 16)) for i in (1, 3, 5))
        premultiplied = [(alpha * channel + 127) // 255 for channel in (r, g, b)]
        return (alpha << 24) | (premultiplied[0] << 16) | (premultiplied[1] << 8) | premultiplied[2]

    def render(self, target: float, opacity: float, depth: float, dt: float,
               urgent: bool = False) -> None:
        """target is the alert intensity: 0 hidden, 0.1 green at the limit, 1 full red."""
        dt = max(0.0, dt)
        tau = (0.04 if urgent else 0.16) if target > self.strength else 0.45
        self.strength += (target - self.strength) * (1 - math.exp(-dt / tau))
        if self.strength < 0.005:
            self.hide()
            return
        self._ensure_surface()
        pulse = 1.0
        if self.strength > 0.85:
            phase = (time.monotonic() - self.started) * (2.4 if urgent else 1.3)
            pulse = 0.86 + 0.14 * (0.5 + 0.5 * math.sin(2 * math.pi * phase))
        key = (round(self.strength, 3), round(opacity, 3), round(depth, 4), round(pulse, 2))
        if key != self.last_key or not self.visible:
            lut = self._palette(opacity, depth, pulse)
            for (rows, cols), index in self.strips:
                self.pixels[rows, cols] = lut[index]
            width, height = self.size
            blend = BLENDFUNCTION(0, 0, 255, 1)
            if not self.update_layered(self.handle, None, ctypes.byref(wintypes.POINT(0, 0)),
                                       ctypes.byref(wintypes.SIZE(width, height)), self.memory_dc,
                                       ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2):
                raise ctypes.WinError(ctypes.get_last_error())
            self.last_key = key
        width, height = self.size
        if not self.position(self.handle, wintypes.HWND(-1), 0, 0, width, height, 0x10 | 0x40):
            raise ctypes.WinError(ctypes.get_last_error())
        self.visible = True

    def hide(self) -> None:
        if self.visible and self.window is not None:
            self.show(self.handle, 0)
        self.strength = 0.0
        self.visible = False
        self.last_key = None

    def destroy(self) -> None:
        self._release_surface()
        if self.window is not None:
            self.window.destroy()
            self.window = None
