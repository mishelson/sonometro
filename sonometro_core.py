import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


FLOOR_DBFS = -100.0
COLORS = ("#36c995", "#f2ca52", "#f59545", "#ed5968")
STATE_NAMES = ("Voz normal", "Elevada", "Alta", "Muy alta")


@dataclass(frozen=True)
class Reading:
    timestamp: float
    dbfs: float
    clipped_fraction: float = 0.0


def measure_pcm(data: bytes, timestamp: float) -> Reading:
    if not data or len(data) % 2:
        raise ValueError("El bloque de audio esta vacio o incompleto.")
    samples = np.frombuffer(data, dtype="<i2").astype(np.float64)
    normalized = samples / 32768.0
    rms = float(np.sqrt(np.mean(normalized * normalized)))
    level = max(FLOOR_DBFS, 20 * math.log10(max(rms, 10 ** (FLOOR_DBFS / 20))))
    clipped = float(np.mean(np.abs(samples) >= 32760))
    return Reading(timestamp, level, clipped)


def calibrate(noise: list[Reading], voice: list[Reading]) -> tuple[float, float]:
    if len(noise) < 20 or len(voice) < 20:
        raise ValueError("Faltan muestras. Repite la calibracion.")
    if any(r.clipped_fraction > 0.01 for r in noise + voice):
        raise ValueError("El microfono satura. Baja su ganancia y vuelve a calibrar.")
    floor = float(np.percentile([r.dbfs for r in noise], 70))
    spoken = [r.dbfs for r in voice if r.dbfs >= floor + 6]
    if len(spoken) < 20 or len(spoken) / len(voice) < 0.25:
        raise ValueError("No se distingue tu voz del fondo. Acerca el microfono y repite.")
    normal = float(np.percentile(spoken, 65))
    if normal > -6:
        raise ValueError("Tu voz deja poco margen. Baja la ganancia y repite.")
    return floor, normal


@dataclass
class Settings:
    version: int = 1
    device_key: str = ""
    noise_dbfs: float | None = None
    normal_dbfs: float | None = None
    spl_offset: float | None = None
    sensitivity: float = 0.0
    opacity: float = 0.45
    depth: float = 0.10
    overlay_enabled: bool = True

    def validate(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Version de configuracion no compatible.")
        if not isinstance(self.device_key, str) or type(self.overlay_enabled) is not bool:
            raise ValueError("Dispositivo o estado de alerta invalidos.")
        for name, low, high in (
            ("sensitivity", -2, 8), ("opacity", 0.1, 0.75), ("depth", 0.02, 0.15),
            ("noise_dbfs", FLOOR_DBFS, 0), ("normal_dbfs", FLOOR_DBFS, -6),
            ("spl_offset", 0, 200),
        ):
            value = getattr(self, name)
            if value is None and name in ("noise_dbfs", "normal_dbfs", "spl_offset"):
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Valor fuera de rango: {name}.")
        if (self.normal_dbfs is None) != (self.noise_dbfs is None):
            raise ValueError("Calibracion incompleta.")
        if self.normal_dbfs is not None:
            if self.noise_dbfs is None or self.normal_dbfs < self.noise_dbfs + 6:
                raise ValueError("La referencia de voz no se distingue del fondo.")
        elif self.spl_offset is not None:
            raise ValueError("La referencia SPL requiere calibracion personal.")
        if self.normal_dbfs is not None and not self.device_key:
            raise ValueError("La calibracion requiere un microfono identificado.")

    def clear_calibration(self) -> None:
        self.noise_dbfs = self.normal_dbfs = self.spl_offset = None


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("La configuracion debe ser un objeto JSON.")
    unknown = payload.keys() - Settings.__dataclass_fields__.keys()
    if unknown:
        raise ValueError("Campos desconocidos en configuracion: " + ", ".join(sorted(unknown)))
    settings = Settings(**payload)
    settings.validate()
    return settings


def save_settings(path: Path, settings: Settings) -> None:
    settings.validate()
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def legacy_device_index(path: Path, available: list[int]) -> int | None:
    if not path.exists():
        return None
    index = int(path.read_text(encoding="utf-8").strip())
    if index not in available:
        raise ValueError("El indice del microfono anterior ya no esta disponible.")
    return index


class LevelTracker:
    def __init__(self) -> None:
        self.level = FLOOR_DBFS
        self.state = 0
        self.timestamp: float | None = None
        self.candidate = 0
        self.candidate_since = 0.0

    def update(self, reading: Reading, settings: Settings) -> tuple[float, int, float]:
        if self.timestamp is None:
            self.level = reading.dbfs
        else:
            dt = max(0, reading.timestamp - self.timestamp)
            tau = 0.10 if reading.dbfs > self.level else 0.65
            self.level += (reading.dbfs - self.level) * (1 - math.exp(-dt / tau))
        self.timestamp = reading.timestamp
        if settings.normal_dbfs is None or settings.noise_dbfs is None:
            self.state = self.candidate = 0
            return self.level, 0, 0.0
        excess = self.level - settings.normal_dbfs
        thresholds = [3 + settings.sensitivity, 7 + settings.sensitivity, 12 + settings.sensitivity]
        desired = sum(excess >= limit for limit in thresholds)
        if self.level < settings.noise_dbfs + 6:
            desired = 0
        elif desired < self.state and excess >= max(0.5, thresholds[self.state - 1] - 1.5):
            desired = self.state
        if desired != self.candidate:
            self.candidate = desired
            self.candidate_since = reading.timestamp
        dwell = 0.18 if desired > self.state else 0.35
        if reading.timestamp - self.candidate_since >= dwell:
            self.state = desired
        strength = min(1.0, max(0.12, (excess - thresholds[0] + 3) / 15)) if self.state else 0.0
        return self.level, self.state, strength
