from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from predict_realtime import CAMERA_IDS, DETECTIONS_FILE, load_detections


@dataclass
class CameraBaseline:
    right: int
    left: int
    unknown: int


class DummyDataGenerator:
    def __init__(self, interval_seconds: int = 60):
        self.interval_seconds = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.baselines: Dict[int, CameraBaseline] = {
            1: CameraBaseline(60, 10, 30),
            2: CameraBaseline(25, 15, 20),
            3: CameraBaseline(15, 45, 30),
            4: CameraBaseline(12, 18, 25),
        }

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.generate_once()
            except Exception as exc:  # pragma: no cover
                print(f"[dummy] 生成中にエラー: {exc}")
            self._stop_event.wait(self.interval_seconds)

    def _next_timestamp(self) -> datetime:
        detections = load_detections()
        if not detections:
            return datetime.now().replace(second=0, microsecond=0)
        latest_ts = max(
            datetime.fromisoformat(row["timestamp"])
            for row in detections
            if "timestamp" in row
        )
        return latest_ts + timedelta(minutes=1)

    def _randomize(self, value: int, spread: int = 15) -> int:
        return max(0, int(random.gauss(mu=value, sigma=spread)))

    def generate_once(self) -> datetime:
        with self._lock:
            timestamp = self._next_timestamp()
            lines = []
            for camera_id in CAMERA_IDS:
                base = self.baselines.get(camera_id, CameraBaseline(10, 10, 5))
                right = self._randomize(base.right)
                left = self._randomize(base.left)
                unknown = self._randomize(base.unknown, spread=8)
                total = right + left + unknown
                unique = max(1, int(total * 0.01) + random.randint(0, 2))
                lines.append(
                    {
                        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
                        "camera_id": camera_id,
                        "right_count": right,
                        "left_count": left,
                        "unknown_count": unknown,
                        "total_count": total,
                        "unique_detections": unique,
                    }
                )
            DETECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with DETECTIONS_FILE.open("a", encoding="utf-8") as handle:
                for row in lines:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            return timestamp


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ダミーの人流データを生成します。")
    parser.add_argument(
        "interval",
        nargs="?",
        type=int,
        default=60,
        help="生成間隔（秒）。デフォルト60秒。",
    )
    args = parser.parse_args()
    generator = DummyDataGenerator(interval_seconds=args.interval)
    print(f"[dummy] {args.interval}秒間隔でデータ生成を開始します。Ctrl+Cで停止。")
    try:
        generator.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[dummy] 停止します。")
    finally:
        generator.stop()


if __name__ == "__main__":
    main()
