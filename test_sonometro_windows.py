import ctypes
import sys
import tempfile
import time
import tkinter as tk
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from sonometro_audio import Device
from sonometro_core import Reading, Settings, load_settings
from sonometro_overlay import Overlay, enable_dpi_awareness


@unittest.skipUnless(sys.platform == "win32", "Windows desktop required")
class WindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        enable_dpi_awareness()

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.geometry("540x720")
        self.root.update()
        self.callback_errors: list[str] = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(str(args))

    def tearDown(self) -> None:
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.assertEqual(self.callback_errors, [])

    def test_overlay_geometry_styles_focus_and_hide(self) -> None:
        overlay = Overlay(self.root)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        before = user32.GetForegroundWindow()
        try:
            for _ in range(60):
                overlay.render(1, 3, 0.45, 0.10, 0.033)
                self.root.update()
            self.assertEqual(len(overlay.windows), 24)
            self.assertEqual(user32.GetForegroundWindow(), before)
            for _, handle in overlay.windows:
                style = overlay.get_style(handle, -20)
                self.assertEqual(style & (0x80000 | 0x20 | 0x8000000 | 0x80),
                                 0x80000 | 0x20 | 0x8000000 | 0x80)
                self.assertTrue(user32.IsWindowVisible(handle))
                self.assertTrue(style & 0x8)
            rect = wintypes.RECT()
            self.assertTrue(user32.GetWindowRect(overlay.windows[0][1], ctypes.byref(rect)))
            self.assertEqual(rect.left, 0)
            self.assertEqual(rect.top, 0)
            self.assertEqual(rect.right, user32.GetSystemMetrics(0))
            self.assertGreater(rect.bottom, 1)
            hit = user32.WindowFromPoint(wintypes.POINT(rect.right // 2, rect.bottom // 2))
            self.assertNotIn(hit, [handle for _, handle in overlay.windows])
            overlay.hide()
            for _, handle in overlay.windows:
                self.assertFalse(user32.IsWindowVisible(handle))
            overlay.render(0, 0, 0.45, 0.10, 0.033)
            self.assertFalse(overlay.visible)
            overlay.render(0.8, 2, 0.45, 0.10, 0.033)
            self.assertTrue(overlay.visible)
            for _ in range(100):
                overlay.render(0, 0, 0.45, 0.10, 0.033)
            self.assertFalse(overlay.visible)
        finally:
            overlay.destroy()

    def test_control_window_calibration_and_settings(self) -> None:
        import sonometro

        device = Device(7, "Prueba", "WASAPI", 1, 48000)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with patch.object(sonometro, "BASE_DIR", base), \
                    patch.object(sonometro, "CONFIG_PATH", base / "config.json"), \
                    patch.object(sonometro, "input_devices", return_value=([device], 7)):
                app = sonometro.App(self.root)
                errors: list[str] = []
                app._error = errors.append
                self.root.update()
                self.assertEqual(app.selected, device)
                self.assertLessEqual(self.root.winfo_reqheight(), self.root.winfo_height())
                self.assertLessEqual(self.root.winfo_reqwidth(), self.root.winfo_width())
                app.running = True
                app._begin_calibration()
                base_time = app.phase_started
                for i in range(95):
                    app._calibration_reading(Reading(base_time + i * 0.033, -70))
                self.assertEqual(app.calibration_phase, "prepare")
                app._calibration_reading(Reading(app.phase_started + 2.1, -70))
                self.assertEqual(app.calibration_phase, "voice")
                voice_start = app.phase_started
                for i in range(184):
                    app._calibration_reading(Reading(voice_start + i * 0.033, -30))
                self.assertIsNone(app.calibration_phase)
                self.assertEqual(app.settings.normal_dbfs, -30)
                self.assertEqual(load_settings(base / "config.json"), app.settings)
                app.running = False
                app._preview()
                self.assertGreater(app.preview_until, time.monotonic())
                time.sleep(0.04)
                self.root.update()
                app._enabled_changed()
                self.assertEqual(app.preview_until, 0)
                self.assertFalse(app.overlay.visible)
                self.assertEqual(errors, [])
                app.settings.noise_dbfs = -100
                app.settings.normal_dbfs = -90
                app.last_reading = Reading(time.monotonic(), -90)
                with patch.object(sonometro.simpledialog, "askstring", return_value="120"):
                    app._set_spl()
                self.assertIsNone(app.settings.spl_offset)
                self.assertEqual(len(errors), 1)
                self.assertIn("desfase", errors[0])
                app.settings.normal_dbfs = -30
                app.settings.noise_dbfs = -70
                app.last_reading = Reading(time.monotonic(), -30)
                with patch.object(sonometro.simpledialog, "askstring", return_value="60"):
                    app._set_spl()
                self.assertEqual(app.settings.spl_offset, 90)
                app.overlay.destroy()
                app.overlay = None

    def test_no_devices_and_ambiguous_profile(self) -> None:
        import sonometro
        from sonometro_core import save_settings

        a = Device(2, "Prueba", "WASAPI", 1, 48000)
        b = Device(7, "Prueba", "WASAPI", 1, 48000)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            path = base / "config.json"
            save_settings(path, Settings(device_key=a.key, noise_dbfs=-70, normal_dbfs=-30))
            with patch.object(sonometro, "BASE_DIR", base), \
                    patch.object(sonometro, "CONFIG_PATH", path), \
                    patch.object(sonometro, "input_devices", return_value=([a, b], 7)):
                app = sonometro.App(self.root)
                self.assertIsNone(app.settings.normal_dbfs)
                self.assertIn("ambiguo", app.status.get())
                with patch.object(sonometro, "input_devices", return_value=([], None)):
                    app._refresh_devices()
                    self.assertIsNone(app.selected)
                    self.assertEqual(str(app.start_button["state"]), "disabled")
                    self.assertIn("No hay microfonos", app.status.get())
                app.overlay.destroy()
                app.overlay = None


if __name__ == "__main__":
    unittest.main()
