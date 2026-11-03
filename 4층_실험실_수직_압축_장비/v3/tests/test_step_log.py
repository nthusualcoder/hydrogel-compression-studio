import csv
import tempfile
import time
import unittest
from pathlib import Path

from hydrogel_app.step_log import StepLog, summarize


class StepLogTests(unittest.TestCase):
    def test_mcu_intervals_ignore_usb_batching_and_keep_load_and_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.csv"
            log = StepLog(path)
            at = time.monotonic()
            log.receive("DBG,ON,100,0,0,1", at)
            log.record("TX", detail="start,0.01,0.0025")
            log.receive("DBG,C,200,0,0,start,0.01,0.0025", at)
            # PC에 동시에 도착해도 Arduino가 기록한 500 ms 간격을 쓴다.
            log.receive("DBG,S,500200,1,1,500000", at)
            log.receive("DBG,S,1000200,2,2,500000", at)
            log.record("LOAD", at, grams=1.1, force_N=0.010787315)
            log.receive("DBG,OFF,1000300,2,2,0:0", at)
            log.close()
            result = summarize(path)
            self.assertEqual(result["steps"], 2)
            self.assertEqual(result["mean_interval_us"], 500000)
            self.assertEqual(result["sequence_gaps"], 0)
            self.assertTrue(result["complete"])
            with path.open(encoding="utf-8-sig", newline="") as file:
                file.readline()
                rows = list(csv.DictReader(file))
            steps = [r for r in rows if r["event"] == "STEP"]
            self.assertEqual(steps[0]["interval_us"], "")
            self.assertEqual(float(steps[1]["expected_interval_us"]), 500000)
            self.assertEqual([r["event"] for r in rows].count("LOAD"), 1)

    def test_timer_wrap_gaps_and_high_speed_omissions_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.csv"
            log = StepLog(path)
            at = time.monotonic()
            log.receive("DBG,ON,4294967000,0,0,1", at)
            log.receive("DBG,S,4294967100,1,1,500000", at)
            log.receive("DBG,S,499804,2,2,500000", at)
            log.receive("DBG,S,1499804,4,4,500000", at)
            log.receive("DBG,H,1500000,4,4,1:3", at)
            log.receive("DBG,OFF,1500100,4,4,1:3", at)
            log.close()
            result = summarize(path)
            self.assertEqual(result["mean_interval_us"], 500000)
            self.assertEqual(result["sequence_gaps"], 1)
            self.assertEqual((result["dropped"], result["skipped_fast"]), (1, 3))

    def test_incomplete_and_demo_logs_are_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.csv"
            log = StepLog(path, demo=True)
            log.receive("DBG,S,100,1,1,500000", time.monotonic())
            log.close("unsupported firmware")
            result = summarize(path)
            self.assertTrue(result["demo"])
            self.assertFalse(result["firmware_confirmed"])
            self.assertFalse(result["complete"])
            self.assertEqual(result["steps"], 0)

    def test_corrupt_frames_and_clock_reversal_are_not_silently_used(self):
        with tempfile.TemporaryDirectory() as directory:
            log = StepLog(Path(directory) / "log.csv")
            at = time.monotonic()
            for frame in ("DBG,ON,1,0,0,9", "DBG,S,bad,1,1,500000", "DBG,UNKNOWN,1,0,0,1"):
                with self.subTest(frame=frame), self.assertRaises(ValueError):
                    log.receive(frame, at)
            log.receive("DBG,ON,100,0,0,1", at)
            with self.assertRaises(ValueError):
                log.receive("DBG,S,99,1,1,500000", at)
            log.close("corrupt")
