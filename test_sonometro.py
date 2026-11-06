import math
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Empty

import numpy as np

from sonometro_audio import Capture, Device
from sonometro_core import (
    COLORS, FLOOR_DBFS, LevelTracker, Reading, Settings, alert_limits, calibrate,
    legacy_device_index, load_settings, measure_pcm, ramp_color, save_settings,
)


def samples(level: float, count: int = 100) -> list[Reading]:
    return [Reading(i * 0.033, level) for i in range(count)]


class MeasurementTests(unittest.TestCase):
    def test_silence_is_finite(self) -> None:
        reading = measure_pcm(bytes(1024), 1)
        self.assertEqual(reading.dbfs, FLOOR_DBFS)
        self.assertEqual(reading.clipped_fraction, 0)

    def test_rms_and_amplitude_ratio(self) -> None:
        low = measure_pcm(np.array([8192, -8192] * 64, dtype="<i2").tobytes(), 0)
        high = measure_pcm(np.array([16384, -16384] * 64, dtype="<i2").tobytes(), 0)
        self.assertAlmostEqual(low.dbfs, -12.0411998, places=6)
        self.assertAlmostEqual(high.dbfs - low.dbfs, 6.0205999, places=6)

    def test_minimum_int16_does_not_overflow(self) -> None:
        reading = measure_pcm(np.array([-32768, 32767], dtype="<i2").tobytes(), 0)
        self.assertTrue(math.isfinite(reading.dbfs))
        self.assertGreater(reading.dbfs, -0.01)
        self.assertEqual(reading.clipped_fraction, 1)

    def test_bad_blocks_fail_explicitly(self) -> None:
        for data in (b"", b"x"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                measure_pcm(data, 0)

    def test_calibration_discards_pauses(self) -> None:
        noise, voice = calibrate(samples(-70), samples(-35, 60) + samples(-70, 40))
        self.assertEqual(noise, -70)
        self.assertEqual(voice, -35)

    def test_invalid_calibration(self) -> None:
        cases = [
            ([], samples(-30)),
            (samples(-50), samples(-48)),
            (samples(-70), samples(-70, 95) + samples(-35, 5)),
            (samples(-70), samples(-4)),
            (samples(-70), [Reading(0, -20, 0.02)] * 50),
        ]
        for noise, voice in cases:
            with self.subTest(voice=voice[:1]), self.assertRaises(ValueError):
                calibrate(noise, voice)


class TrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(device_key="mic", noise_dbfs=-70, normal_dbfs=-30)

    def settle(self, level: float, tracker: LevelTracker | None = None,
               start: float = 0) -> tuple[LevelTracker, tuple[float, int, float]]:
        tracker = tracker or LevelTracker()
        output = (0.0, 0, 0.0)
        for i in range(150):
            output = tracker.update(Reading(start + i * 0.033, level), self.settings)
        return tracker, output

    def test_exact_thresholds(self) -> None:
        for level, expected in ((-30, 0), (-27.01, 0), (-27, 1), (-23, 2), (-18, 3)):
            with self.subTest(level=level):
                _, output = self.settle(level)
                self.assertEqual(output[1], expected)
                self.assertEqual(output[2] > 0, expected > 0)

    def test_no_alert_without_calibration(self) -> None:
        output = LevelTracker().update(Reading(1, -1), Settings())
        self.assertEqual(output[1:], (0, 0.0))

    def test_peak_does_not_trigger(self) -> None:
        tracker = LevelTracker()
        tracker.update(Reading(0, -30), self.settings)
        self.assertEqual(tracker.update(Reading(0.033, -5), self.settings)[1], 0)

    def test_hysteresis_and_release(self) -> None:
        tracker, _ = self.settle(-26)
        tracker, output = self.settle(-28, tracker, 5)
        self.assertEqual(output[1], 1)
        _, output = self.settle(-30, tracker, 10)
        self.assertEqual(output[1:], (0, 0.0))

    def test_smoothing_matches_time_constant(self) -> None:
        tracker = LevelTracker()
        tracker.update(Reading(0, -50), self.settings)
        level, _, _ = tracker.update(Reading(0.1, -30), self.settings)
        self.assertAlmostEqual(level, -50 + 20 * (1 - math.exp(-1)))
        level2, _, _ = tracker.update(Reading(0.75, -50), self.settings)
        self.assertAlmostEqual(level2, level + (-50 - level) * (1 - math.exp(-1)))

    def test_sensitivity_shifts_all_limits(self) -> None:
        self.settings.sensitivity = 8
        _, output = self.settle(-23)
        self.assertEqual(output[1], 0)

    def test_normal_voice_releases_even_at_maximum_sensitivity(self) -> None:
        self.settings.sensitivity = -2
        tracker, _ = self.settle(-27)
        _, output = self.settle(-30, tracker, 5)
        self.assertEqual(output[1:], (0, 0.0))

    def test_short_shout_triggers_red_immediately_despite_high_tolerance(self) -> None:
        self.settings.sensitivity = 8
        tracker = LevelTracker()
        tracker.update(Reading(0, -30), self.settings)
        t = 0.0
        output = (0.0, 0, 0.0)
        while t < 0.25:
            t += 0.025
            output = tracker.update(Reading(t, -12), self.settings)
            if output[1] == 3:
                break
        self.assertEqual(output[1:], (3, 1.0))
        self.assertLess(t, 0.25)
        self.assertTrue(tracker.shouting)
        # The red alert is held briefly after the shout ends, then released.
        output = tracker.update(Reading(t + 0.5, -30), self.settings)
        self.assertEqual(output[1], 3)
        _, output = self.settle(-30, tracker, t + 2)
        self.assertEqual(output[1:], (0, 0.0))
        self.assertFalse(tracker.shouting)

    def test_shout_needs_to_last_a_moment(self) -> None:
        tracker = LevelTracker()
        tracker.update(Reading(0, -30), self.settings)
        tracker.update(Reading(0.025, -5), self.settings)
        tracker.update(Reading(0.05, -5), self.settings)
        output = tracker.update(Reading(0.075, -30), self.settings)
        self.assertNotEqual(output[1], 3)
        self.assertFalse(tracker.shouting)

    def test_shout_below_threshold_uses_gradual_path(self) -> None:
        self.settings.sensitivity = 8
        tracker, output = self.settle(-17)
        self.assertIn(output[1], (1, 2))
        self.assertLess(output[2], 1.0)
        self.assertFalse(tracker.shouting)

    def test_gradual_ramp_goes_from_green_to_red_before_shout(self) -> None:
        intensities = []
        for excess in (3.5, 6, 9, 13.9):
            _, output = self.settle(-30 + excess)
            intensities.append(output[2])
        self.assertEqual(intensities, sorted(intensities))
        self.assertLess(intensities[0], 0.2)
        self.assertEqual(intensities[-1], 1.0)
        self.assertEqual(ramp_color(0.1), COLORS[0])
        self.assertEqual(ramp_color(1.0), COLORS[3])
        self.settings.sensitivity = 8
        start, middle, end = alert_limits(self.settings)
        self.assertLess(start, middle)
        self.assertLess(end, self.settings.shout_db)


class ConfigTests(unittest.TestCase):
    def test_save_creates_missing_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "app-data" / "config.json"
            save_settings(path, Settings())
            self.assertEqual(load_settings(path), Settings())

    def test_round_trip_and_spl_reference(self) -> None:
        settings = Settings(device_key="mic", noise_dbfs=-70, normal_dbfs=-30, spl_offset=90)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            save_settings(path, settings)
            self.assertEqual(load_settings(path), settings)
            self.assertFalse(path.with_suffix(".tmp").exists())
        self.assertEqual(-30 + settings.spl_offset, 60)

    def test_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_settings(Path(folder) / "absent.json"), Settings())

    def test_invalid_config(self) -> None:
        cases = ["[]", "{", '{"version":2}', '{"opacity":9}', '{"opacity":NaN}',
                 '{"overlay_enabled":"false"}', '{"normal_dbfs":-30}',
                 '{"device_key":1}', '{"surprise":true}', '{"spl_offset":90}',
                 '{"version":true}', '{"depth":null}', '{"sensitivity":true}', '{"shout_db":2}']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for content in cases:
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_settings(path)

    def test_legacy_index_is_not_list_position(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "microphone_config.txt"
            path.write_text("7", encoding="utf-8")
            self.assertEqual(legacy_device_index(path, [2, 7, 12]), 7)
            with self.assertRaises(ValueError):
                legacy_device_index(path, [2, 12])
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                legacy_device_index(path, [2, 12])

    def test_duplicate_names_have_distinct_labels(self) -> None:
        a = Device(2, "Microfono", "WASAPI", 1, 48000)
        b = Device(7, "Microfono", "WASAPI", 1, 48000)
        self.assertNotEqual(a.label, b.label)
        self.assertEqual(a.key, b.key)

    def test_invalid_profile_and_clear(self) -> None:
        with self.assertRaises(ValueError):
            Settings(device_key="mic", noise_dbfs=-32, normal_dbfs=-30).validate()
        settings = Settings(device_key="mic", noise_dbfs=-70, normal_dbfs=-30, spl_offset=90)
        settings.clear_calibration()
        self.assertIsNone(settings.normal_dbfs)
        self.assertIsNone(settings.noise_dbfs)
        self.assertIsNone(settings.spl_offset)


class FakeStream:
    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.closed = False
        self.stopped = False
        self.read_once = threading.Event()
        self.release = threading.Event()

    def read(self, chunk: int) -> bytes:
        self.read_once.set()
        if not self.release.wait(2):
            raise OSError("Fake stream timed out")
        if self.broken:
            raise OSError("Disconnected")
        return bytes(chunk * 2)

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakeAudio:
    def __init__(self, stream: FakeStream, fail_open: bool = False) -> None:
        self.stream = stream
        self.fail_open = fail_open
        self.terminated = False

    def is_format_supported(self, rate: int, **kwargs: object) -> bool:
        return True

    def open(self, **kwargs: object) -> FakeStream:
        if self.fail_open:
            raise OSError("Permission denied")
        return self.stream

    def terminate(self) -> None:
        self.terminated = True


class CaptureTests(unittest.TestCase):
    def test_start_stop_and_cleanup(self) -> None:
        stream = FakeStream()
        audio = FakeAudio(stream)
        capture = Capture(lambda: audio)
        device = Device(7, "Test", "Test", 1, 48000)
        self.assertTrue(capture.start(device))
        self.assertTrue(stream.read_once.wait(2))
        self.assertFalse(capture.start(device))
        capture.stop()
        stream.release.set()
        capture.thread.join(2)
        self.assertFalse(capture.alive)
        self.assertTrue(stream.stopped)
        self.assertTrue(stream.closed)
        self.assertTrue(audio.terminated)
        self.assertLessEqual(capture.readings.qsize(), 1)
        capture.stop()
        stream.read_once.clear()
        stream.release.clear()
        self.assertTrue(capture.start(device))
        self.assertTrue(stream.read_once.wait(2))
        capture.stop()
        stream.release.set()
        capture.thread.join(2)
        self.assertFalse(capture.alive)

    def test_disconnect_is_reported_and_cleaned_up(self) -> None:
        stream = FakeStream(broken=True)
        stream.release.set()
        audio = FakeAudio(stream)
        capture = Capture(lambda: audio)
        capture.start(Device(7, "Test", "Test", 1, 48000))
        capture.thread.join(2)
        events = []
        while True:
            try:
                events.append(capture.events.get_nowait())
            except Empty:
                break
        self.assertIn(("error", "No se pudo capturar audio: Disconnected"), events)
        self.assertEqual(events[-1], ("stopped", ""))
        self.assertTrue(stream.closed)
        self.assertTrue(audio.terminated)

    def test_open_failure(self) -> None:
        audio = FakeAudio(FakeStream(), fail_open=True)
        capture = Capture(lambda: audio)
        capture.start(Device(7, "Test", "Test", 1, 48000))
        capture.thread.join(2)
        self.assertEqual(capture.events.get_nowait()[0], "error")
        self.assertTrue(audio.terminated)
        self.assertFalse(capture.alive)


if __name__ == "__main__":
    unittest.main()
