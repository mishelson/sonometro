import logging
import math
import time
import tkinter as tk
from pathlib import Path
from queue import Empty
from tkinter import messagebox, simpledialog, ttk

from sonometro_audio import Capture, Device, input_devices
from sonometro_core import (
    COLORS, FLOOR_DBFS, STATE_NAMES, LevelTracker, Reading, Settings,
    alert_limits, calibrate, legacy_device_index, load_settings, ramp_color, save_settings,
)
from sonometro_overlay import Overlay, enable_dpi_awareness


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "sonometro_config.json"
LOG = logging.getLogger("sonometro")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.capture = Capture()
        self.tracker = LevelTracker()
        self.devices: list[Device] = []
        self.selected: Device | None = None
        self.running = False
        self.closing = False
        self.stop_started: float | None = None
        self.stop_warned = False
        self.capture_error: str | None = None
        self.last_reading: Reading | None = None
        self.state = 0
        self.strength = 0.0
        self.preview_until = 0.0
        self.calibration_phase: str | None = None
        self.phase_started = 0.0
        self.noise_samples: list[Reading] = []
        self.voice_samples: list[Reading] = []
        self.last_tick = time.monotonic()
        self.settings = Settings()
        startup_errors = []
        try:
            self.settings = load_settings(CONFIG_PATH)
        except (OSError, ValueError, TypeError) as exc:
            startup_errors.append(f"No se cargo la configuracion: {exc}\nSe usaran ajustes nuevos.")
        self.overlay: Overlay | None = None
        try:
            self.overlay = Overlay(root)
        except (OSError, tk.TclError) as exc:
            startup_errors.append(f"No se pudo preparar el efecto de pantalla: {exc}")
        self._build_ui()
        self._refresh_devices(initial=True)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(33, self._tick)
        for error in startup_errors:
            self.root.after_idle(lambda text=error: self._error(text))

    def _build_ui(self) -> None:
        self.root.title("Sonometro | Tu voz, en equilibrio")
        self.root.geometry("540x790")
        self.root.minsize(500, 750)
        self.root.configure(bg="#131c28")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#131c28", foreground="#e8eef7", font=("Segoe UI", 10))
        style.configure("TButton", background="#2b4056", padding=(12, 8))
        style.map("TButton", background=[("active", "#3b5875"), ("disabled", "#23303e")])
        style.configure("TCombobox", fieldbackground="#223247", foreground="#e8eef7")
        style.map("TCombobox", fieldbackground=[("readonly", "#223247")],
                  foreground=[("readonly", "#e8eef7")])
        style.configure("Heading.TLabel", font=("Segoe UI", 21, "bold"))
        style.configure("Muted.TLabel", foreground="#a7b7ca")
        style.configure("State.TLabel", font=("Segoe UI", 14, "bold"))
        body = ttk.Frame(self.root, padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Tu voz, en equilibrio", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="Una senal visual para bajar el volumen, no una alarma.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 18))
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Label(row, text="Microfono").pack(side="left")
        self.refresh_button = ttk.Button(row, text="Actualizar", command=self._refresh_devices)
        self.refresh_button.pack(side="right")
        self.device_combo = ttk.Combobox(body, state="readonly")
        self.device_combo.pack(fill="x", pady=(6, 14))
        self.device_combo.bind("<<ComboboxSelected>>", self._select_device)
        controls = ttk.Frame(body)
        controls.pack(fill="x")
        self.start_button = ttk.Button(controls, text="Iniciar", command=self._toggle)
        self.start_button.pack(side="left")
        self.calibrate_button = ttk.Button(controls, text="Calibrar mi voz", command=self._begin_calibration)
        self.calibrate_button.pack(side="left", padx=8)
        self.status = tk.StringVar(value="Listo. Selecciona un microfono e inicia.")
        ttk.Label(body, textvariable=self.status, wraplength=470).pack(anchor="w", pady=(14, 8))
        self.level_text = tk.StringVar(value="-- dBFS")
        ttk.Label(body, textvariable=self.level_text, style="Heading.TLabel").pack(anchor="w")
        self.state_text = tk.StringVar(value="Sin calibrar")
        self.state_label = ttk.Label(body, textvariable=self.state_text, style="State.TLabel")
        self.state_label.pack(anchor="w", pady=(3, 8))
        self.meter = tk.Canvas(body, height=44, bg="#131c28", highlightthickness=0)
        self.meter.pack(fill="x")
        self.reference = tk.StringVar()
        ttk.Label(body, textvariable=self.reference, style="Muted.TLabel",
                  wraplength=470).pack(anchor="w", pady=(5, 10))
        self.sensitivity = tk.DoubleVar(value=self.settings.sensitivity)
        self.shout = tk.DoubleVar(value=self.settings.shout_db)
        self.opacity = tk.DoubleVar(value=self.settings.opacity)
        self.depth = tk.DoubleVar(value=self.settings.depth)
        self.sensitivity_label = tk.StringVar()
        self.shout_label = tk.StringVar()
        self.opacity_label = tk.StringVar()
        self.depth_label = tk.StringVar()
        for label, variable, minimum, maximum in (
            (self.sensitivity_label, self.sensitivity, -2, 8),
            (self.shout_label, self.shout, 6, 30),
            (self.opacity_label, self.opacity, 0.1, 0.75),
            (self.depth_label, self.depth, 0.02, 0.15),
        ):
            ttk.Label(body, textvariable=label).pack(anchor="w")
            slider = ttk.Scale(body, variable=variable, from_=minimum, to=maximum,
                               command=self._sliders_changed)
            slider.pack(fill="x", pady=(0, 7))
            slider.bind("<ButtonRelease-1>", lambda event: self._save())
            slider.bind("<KeyRelease>", lambda event: self._save())
        self.enabled = tk.BooleanVar(value=self.settings.overlay_enabled)
        ttk.Checkbutton(body, text="Mostrar alerta en los bordes al elevar mi voz",
                        variable=self.enabled, command=self._enabled_changed).pack(anchor="w")
        extras = ttk.Frame(body)
        extras.pack(fill="x", pady=(10, 8))
        ttk.Button(extras, text="Vista previa", command=self._preview).pack(side="left")
        self.spl_button = ttk.Button(extras, text="Referencia dB SPL", command=self._set_spl)
        self.spl_button.pack(side="left", padx=8)
        ttk.Label(body, text="Audio local, sin grabaciones. El microfono tambien capta ruido\n"
                  "y otras voces. dBFS no equivale a decibelios acusticos.",
                  style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(4, 0))
        self._sliders_changed()
        self._reference_text()
        self._buttons()

    def _error(self, text: str) -> None:
        LOG.error(text)
        self.status.set(text.splitlines()[0])
        if not self.closing:
            messagebox.showerror("Sonometro", text, parent=self.root)

    def _save(self) -> None:
        try:
            save_settings(CONFIG_PATH, self.settings)
        except (OSError, ValueError) as exc:
            self._error(f"No se pudieron guardar los ajustes: {exc}")

    def _refresh_devices(self, initial: bool = False) -> None:
        if self.capture.alive:
            return
        previous = self.selected
        try:
            self.devices, default = input_devices()
        except (OSError, ValueError, RuntimeError) as exc:
            self.devices = []
            self.device_combo.configure(values=[])
            self.device_combo.set("")
            self.selected = None
            self._buttons()
            self._error(f"No se pudieron enumerar los microfonos: {exc}")
            return
        self.device_combo.configure(values=[d.label for d in self.devices])
        matches = [d for d in self.devices if d.key == self.settings.device_key]
        choice = matches[0] if len(matches) == 1 else None
        notice = ""
        if previous is not None:
            choice = next((d for d in self.devices if d == previous), choice)
        if initial and not CONFIG_PATH.exists():
            try:
                index = legacy_device_index(BASE_DIR / "microphone_config.txt",
                                            [d.index for d in self.devices])
                if index is not None:
                    choice = next(d for d in self.devices if d.index == index)
                    notice = "Seleccion anterior recuperada. Verifica el microfono y calibra."
            except (OSError, ValueError) as exc:
                notice = f"No se recupero la seleccion anterior: {exc}"
                LOG.warning(notice)
        if choice is None:
            if self.settings.device_key:
                notice = "Microfono guardado ausente o ambiguo. Selecciona y vuelve a calibrar."
                self.settings.clear_calibration()
            choice = next((d for d in self.devices if d.index == default),
                          self.devices[0] if self.devices else None)
        self.selected = choice
        if choice is None:
            self.device_combo.set("")
            self.status.set("No hay microfonos. Conecta uno y pulsa Actualizar.")
        else:
            self.device_combo.current(self.devices.index(choice))
            if choice.key != self.settings.device_key:
                self.settings.clear_calibration()
            self.settings.device_key = choice.key
            if notice:
                self.status.set(notice)
        self._reference_text()
        self._buttons()

    def _select_device(self, event: tk.Event | None = None) -> None:
        index = self.device_combo.current()
        if index < 0:
            self._error("Selecciona un microfono disponible.")
            return
        device = self.devices[index]
        if device != self.selected:
            self.settings.clear_calibration()
            self.tracker = LevelTracker()
            self.last_reading = None
            self.level_text.set("-- dBFS")
            self.state_text.set("Sin calibrar")
            self.status.set("Microfono cambiado. Inicia y calibra tu voz.")
        self.selected = device
        self.settings.device_key = device.key
        self._reference_text()
        self._save()

    def _buttons(self) -> None:
        busy = self.capture.alive
        self.device_combo.configure(state="disabled" if busy else "readonly")
        self.refresh_button.configure(state="disabled" if busy else "normal")
        self.start_button.configure(text="Detener" if busy else "Iniciar",
                                    state="normal" if self.selected and self.stop_started is None else "disabled")
        self.calibrate_button.configure(text="Cancelar calibracion" if self.calibration_phase else "Calibrar mi voz",
                                        state="normal" if self.running else "disabled")
        self.spl_button.configure(state="normal" if self.running and
                                  self.settings.normal_dbfs is not None and
                                  not self.calibration_phase else "disabled")

    def _toggle(self) -> None:
        if self.capture.alive:
            self._stop()
        elif self.selected is not None:
            self._save()
            self.tracker = LevelTracker()
            self.last_reading = None
            self.preview_until = 0
            self.capture_error = None
            if self.capture.start(self.selected):
                self.status.set("Abriendo microfono...")
                self._buttons()

    def _stop(self) -> None:
        self.running = False
        self.calibration_phase = None
        self.noise_samples.clear()
        self.voice_samples.clear()
        self.preview_until = 0
        self.state = 0
        self.strength = 0
        if self.overlay:
            self.overlay.hide()
        self.capture.stop()
        self.stop_started = time.monotonic()
        self.stop_warned = False
        self.status.set("Deteniendo microfono...")
        self._buttons()

    def _begin_calibration(self) -> None:
        if self.calibration_phase:
            self.calibration_phase = None
            self.noise_samples.clear()
            self.voice_samples.clear()
            self.tracker = LevelTracker()
            self.status.set("Calibracion cancelada. Se conserva la referencia anterior.")
            self._buttons()
            return
        self.preview_until = 0
        if self.overlay:
            self.overlay.hide()
        self.noise_samples.clear()
        self.voice_samples.clear()
        self.calibration_phase = "noise"
        self.phase_started = time.monotonic()
        self.status.set("1/2: Guarda silencio durante 3 segundos.")
        self._buttons()

    def _calibration_reading(self, reading: Reading) -> None:
        elapsed = reading.timestamp - self.phase_started
        if self.calibration_phase == "noise":
            self.noise_samples.append(reading)
            self.status.set(f"1/2: Guarda silencio... {max(0, 3 - elapsed):.1f} s")
            if elapsed >= 3:
                self.calibration_phase = "prepare"
                self.phase_started = reading.timestamp
                self.status.set("Preparate: empieza a hablar con tu voz normal en 2 segundos.")
        elif self.calibration_phase == "prepare":
            self.status.set(f"Preparate para hablar... {max(0, 2 - elapsed):.1f} s")
            if elapsed >= 2:
                self.calibration_phase = "voice"
                self.phase_started = reading.timestamp
        elif self.calibration_phase == "voice":
            self.voice_samples.append(reading)
            self.status.set(f"2/2: Habla con tu voz normal... {max(0, 6 - elapsed):.1f} s")
            if elapsed >= 6:
                self.calibration_phase = None
                try:
                    noise, normal = calibrate(self.noise_samples, self.voice_samples)
                    self.settings.noise_dbfs = noise
                    self.settings.normal_dbfs = normal
                    self.settings.spl_offset = None
                    self.tracker = LevelTracker()
                    self._save()
                    self.status.set("Calibrado. Los bordes apareceran solo si elevas la voz.")
                    self._reference_text()
                except ValueError as exc:
                    self._error(f"Calibracion no valida: {exc}\nSe conserva la referencia anterior.")
                finally:
                    self.noise_samples.clear()
                    self.voice_samples.clear()
                    self._buttons()

    def _reference_text(self) -> None:
        normal = self.settings.normal_dbfs
        if normal is None:
            self.reference.set("Calibra para aprender tu voz normal. Sin calibrar no hay alertas.")
        else:
            start, _, end = alert_limits(self.settings)
            self.reference.set(f"Tu voz normal: {normal:.1f} dBFS | Verde desde +{start:.1f} dB, "
                               f"rojo gradual en +{end:.1f} dB, grito +{self.settings.shout_db:.1f} dB.\n"
                               "Recalibra si cambias la ganancia o distancia al microfono.")

    def _sliders_changed(self, value: str = "") -> None:
        self.settings.sensitivity = round(self.sensitivity.get(), 1)
        self.settings.shout_db = round(self.shout.get(), 1)
        self.settings.opacity = self.opacity.get()
        self.settings.depth = self.depth.get()
        self.sensitivity_label.set(f"Tolerancia: alerta desde +{3 + self.settings.sensitivity:.1f} dB "
                                   "(izquierda = mas sensible)")
        self.shout_label.set(f"Grito: rojo inmediato desde +{self.settings.shout_db:.1f} dB, "
                             "aunque dure un instante")
        self.opacity_label.set(f"Intensidad del borde: {self.settings.opacity:.0%}")
        self.depth_label.set(f"Profundidad maxima hacia dentro: {self.settings.depth:.0%}")
        self._reference_text()

    def _enabled_changed(self) -> None:
        self.settings.overlay_enabled = self.enabled.get()
        self.preview_until = 0
        if self.overlay:
            self.overlay.hide()
        self._save()

    def _preview(self) -> None:
        if self.calibration_phase:
            self._error("Termina o cancela la calibracion antes de la vista previa.")
        elif self.overlay is None:
            self._error("El efecto de pantalla no esta disponible.")
        else:
            self.preview_until = time.monotonic() + 5
            self.status.set("Vista previa: de verde a rojo, creciendo hacia dentro, durante 5 segundos.")

    def _set_spl(self) -> None:
        value = simpledialog.askstring(
            "Referencia externa",
            "Solo con un sonometro externo junto al microfono.\n"
            "Habla de forma estable y escribe su lectura simultanea en dB SPL (20-120).\n"
            "No introduzcas una cifra generica de voz normal.\n"
            "Escribe 'borrar' para quitar la referencia.",
            parent=self.root,
        )
        if value is None:
            return
        if value.strip().lower() == "borrar":
            self.settings.spl_offset = None
            self._save()
            return
        try:
            reference = float(value.replace(",", "."))
            reading = self.capture.latest() or self.last_reading
            if not math.isfinite(reference) or not 20 <= reference <= 120:
                raise ValueError("La lectura debe estar entre 20 y 120 dB SPL.")
            if reading is None or time.monotonic() - reading.timestamp > 0.5:
                raise ValueError("No hay una lectura reciente del microfono.")
            noise = self.settings.noise_dbfs
            if noise is None or reading.dbfs < noise + 6 or reading.clipped_fraction > 0.01:
                raise ValueError("Habla de forma estable, sin silencio ni saturacion.")
            offset = reference - reading.dbfs
            if not 0 <= offset <= 200:
                raise ValueError("El desfase es excesivo. Revisa la ganancia y la referencia externa.")
            self.settings.spl_offset = offset
            self.settings.validate()
            self._save()
            self.status.set("Referencia guardada. dB SPL es una estimacion, no una medicion certificada.")
        except ValueError as exc:
            self._error(f"No se establecio la referencia SPL: {exc}")

    def _draw_meter(self, level: float) -> None:
        width = max(100, self.meter.winfo_width())
        self.meter.delete("all")
        self.meter.create_rectangle(0, 4, width, 19, fill="#283b50", outline="")
        fraction = min(1, max(0, (level + 80) / 80))
        color = ramp_color(self.strength) if self.state else COLORS[0]
        self.meter.create_rectangle(0, 4, width * fraction, 19, fill=color, outline="")
        for value in (-80, -60, -40, -20, 0):
            x = min(width - 12, max(12, width * (value + 80) / 80))
            self.meter.create_text(x, 34, text=str(value), fill="#a7b7ca", font=("Segoe UI", 8))
        if self.settings.normal_dbfs is not None:
            x = width * min(1, max(0, (self.settings.normal_dbfs + 80) / 80))
            self.meter.create_line(x, 0, x, 23, fill="#e8eef7", width=2)
            shout = self.settings.normal_dbfs + self.settings.shout_db
            x = width * min(1, max(0, (shout + 80) / 80))
            self.meter.create_line(x, 0, x, 23, fill=COLORS[3], width=2, dash=(3, 2))

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now
        failure = False
        while True:
            try:
                event, detail = self.capture.events.get_nowait()
            except Empty:
                break
            if event == "started":
                self.running = self.stop_started is None and not self.closing
                if self.running:
                    self.status.set(f"Escuchando ({detail}). " +
                                    ("Calibra tu voz para activar las alertas."
                                     if self.settings.normal_dbfs is None else "Referencia personal activa."))
            elif event == "error":
                failure = True
                self.capture_error = detail
                self._stop()
                self._error(detail)
            elif event == "stopped":
                self.running = False
                self.calibration_phase = None
                self.state = 0
                self.strength = 0
                if self.overlay:
                    self.overlay.hide()
                if not failure and not self.closing and self.capture_error is None:
                    self.status.set("Detenido. El microfono esta liberado.")
                self.level_text.set("-- dBFS")
                self.state_text.set("Detenido")
        if self.stop_started is not None:
            if not self.capture.alive:
                self.stop_started = None
            elif now - self.stop_started > 3 and not self.stop_warned:
                self.stop_warned = True
                self.root.deiconify()
                self._error("El controlador de audio tarda en detenerse. No se abrira otra captura.")
                if self.closing:
                    self.closing = False
                    self.status.set("El controlador no responde. La captura sigue bloqueada; revisa el dispositivo.")
        reading = self.capture.latest()
        if reading is not None and self.running:
            self.last_reading = reading
            level, self.state, self.strength = self.tracker.update(reading, self.settings)
            if self.calibration_phase:
                self._calibration_reading(reading)
                self.state = 0
                self.strength = 0
            spl = (f" | ~{level + self.settings.spl_offset:.1f} dB SPL estimados"
                   if self.settings.spl_offset is not None else "")
            self.level_text.set(f"{level:.1f} dBFS{spl}")
            if reading.clipped_fraction > 0.01:
                self.state_text.set("Saturacion: baja la ganancia y recalibra")
            elif self.calibration_phase:
                self.state_text.set("Calibrando")
            elif self.settings.normal_dbfs is None:
                self.state_text.set("Sin calibrar")
            elif level < self.settings.noise_dbfs + 6:
                self.state_text.set("Silencio / ruido de fondo")
            else:
                self.state_text.set("Grito detectado" if self.tracker.shouting else STATE_NAMES[self.state])
            self.state_label.configure(foreground=ramp_color(self.strength) if self.state else COLORS[0])
        if self.running and self.last_reading and now - self.last_reading.timestamp > 1.5:
            self._stop()
            self.capture_error = "El microfono dejo de entregar audio."
            self._error("El microfono dejo de entregar audio. Detenlo y revisa su conexion.")
        target = self.strength if self.running and self.settings.overlay_enabled else 0.0
        urgent = self.running and self.tracker.shouting
        if self.calibration_phase or self.closing:
            target = 0.0
            urgent = False
        elif self.preview_until > now:
            progress = min(1.0, (5 - (self.preview_until - now)) / 4)
            target = 0.1 + 0.9 * progress
            urgent = False
        elif self.preview_until:
            self.preview_until = 0
            self.status.set("Vista previa finalizada." if not self.running else "Escuchando.")
        if self.overlay:
            try:
                self.overlay.render(target, self.settings.opacity, self.settings.depth, dt, urgent)
            except (OSError, tk.TclError) as exc:
                self.overlay.destroy()
                self.overlay = None
                self._error(f"Se desactivo el efecto de pantalla por un error: {exc}")
        self._draw_meter(self.tracker.level if self.running else FLOOR_DBFS)
        self._buttons()
        if self.closing and not self.capture.alive:
            if self.overlay:
                self.overlay.destroy()
            self.root.destroy()
            return
        self.root.after(33, self._tick)

    def _close(self) -> None:
        if self.closing:
            return
        self._save()
        self.closing = True
        self._stop()
        self.root.withdraw()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        enable_dpi_awareness()
    except (OSError, RuntimeError) as exc:
        LOG.error("No se pudo iniciar: %s", exc)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Sonometro", str(exc), parent=root)
        root.destroy()
        return
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
