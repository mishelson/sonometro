import threading
import time
from dataclasses import dataclass
from queue import Empty, Full, Queue
from typing import Callable

import pyaudio

from sonometro_core import Reading, measure_pcm


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    host: str
    channels: int
    rate: int

    @property
    def key(self) -> str:
        return f"{self.host}|{self.name}|{self.channels}|{self.rate}"

    @property
    def label(self) -> str:
        return f"{self.name} [{self.host}, #{self.index}]"


def input_devices() -> tuple[list[Device], int | None]:
    audio = pyaudio.PyAudio()
    try:
        devices = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if int(info["maxInputChannels"]) > 0:
                host = audio.get_host_api_info_by_index(int(info["hostApi"]))["name"]
                devices.append(Device(index, str(info["name"]), str(host),
                                      int(info["maxInputChannels"]), int(info["defaultSampleRate"])))
        default = None
        for host_index in range(audio.get_host_api_count()):
            host_info = audio.get_host_api_info_by_index(host_index)
            if host_info["type"] == pyaudio.paWASAPI:
                candidate = int(host_info["defaultInputDevice"])
                if any(d.index == candidate for d in devices):
                    default = candidate
                    break
        return devices, default
    finally:
        audio.terminate()


class Capture:
    def __init__(self, audio_factory: Callable[[], pyaudio.PyAudio] = pyaudio.PyAudio) -> None:
        self.audio_factory = audio_factory
        self.readings: Queue[Reading] = Queue(maxsize=1)
        self.events: Queue[tuple[str, str]] = Queue()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, device: Device) -> bool:
        if self.alive:
            return False
        self.stop_event.clear()
        for queue in (self.events, self.readings):
            while True:
                try:
                    queue.get_nowait()
                except Empty:
                    break
        self.thread = threading.Thread(target=self._run, args=(device,), daemon=True,
                                       name="sonometro-audio")
        self.thread.start()
        return True

    def stop(self) -> None:
        self.stop_event.set()

    def latest(self) -> Reading | None:
        try:
            return self.readings.get_nowait()
        except Empty:
            return None

    def _run(self, device: Device) -> None:
        audio = None
        stream = None
        try:
            audio = self.audio_factory()
            rate = None
            for candidate in dict.fromkeys((device.rate, 48000, 44100, 16000)):
                try:
                    supported = audio.is_format_supported(candidate, input_device=device.index,
                                                          input_channels=1, input_format=pyaudio.paInt16)
                except ValueError:
                    supported = False
                if supported:
                    rate = candidate
                    break
            if rate is None:
                raise ValueError("El microfono no admite captura mono PCM de 16 bits.")
            chunk = max(256, int(rate * 0.025))
            stream = audio.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                                input_device_index=device.index, frames_per_buffer=chunk)
            self.events.put(("started", f"{rate} Hz"))
            while not self.stop_event.is_set():
                reading = measure_pcm(stream.read(chunk), time.monotonic())
                try:
                    self.readings.put_nowait(reading)
                except Full:
                    self.latest()
                    self.readings.put_nowait(reading)
        except (OSError, ValueError, RuntimeError) as exc:
            self.events.put(("error", f"No se pudo capturar audio: {exc}"))
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except (OSError, ValueError, RuntimeError) as exc:
                    self.events.put(("error", f"No se pudo detener el flujo: {exc}"))
                try:
                    stream.close()
                except (OSError, ValueError, RuntimeError) as exc:
                    self.events.put(("error", f"No se pudo cerrar el flujo: {exc}"))
            if audio is not None:
                try:
                    audio.terminate()
                except (OSError, ValueError, RuntimeError) as exc:
                    self.events.put(("error", f"No se pudo liberar el microfono: {exc}"))
            self.events.put(("stopped", ""))
