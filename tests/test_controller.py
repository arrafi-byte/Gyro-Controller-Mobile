"""
test_controller.py — Milestone 1 self-test script.

Automatically exercises the VirtualController by sweeping the steering
axis (left stick X) through a sine wave and ramping throttle / brake
so the movement can be verified in:
  • Windows joy.cpl  (Start → "Set up USB game controllers")
  • https://gamepad-tester.com
  • https://hardwaretester.com/gamepad

Usage:
    python -m tests.test_controller          # default: 10-second test
    python -m tests.test_controller --duration 30  # 30-second test

The script prints live values to the terminal as it runs.
Press Ctrl+C to stop early.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import time

# Allow running as `python -m tests.test_controller` from project root
sys.path.insert(0, ".")

from server.virtual_controller import VirtualController


def run_test(duration: float = 10.0, tick_hz: int = 250) -> None:
    """Drive steer / throttle / brake through predictable patterns."""

    interval = 1.0 / tick_hz
    print(f"[test] Starting VirtualController self-test ({duration}s, {tick_hz} Hz)")
    print("[test] Open joy.cpl or https://gamepad-tester.com to observe.\n")

    with VirtualController() as pad:
        t0 = time.perf_counter()
        tick = 0
        try:
            while True:
                elapsed = time.perf_counter() - t0
                if elapsed >= duration:
                    break

                # --- Steering: sine wave with ~2-second period ---
                steer = math.sin(elapsed * math.pi)  # -1 … +1

                # --- Throttle: sawtooth ramp 0→1 every 3 seconds ---
                throttle = (elapsed % 3.0) / 3.0

                # --- Brake: on for 1s, off for 1s (square wave) ---
                brake = 1.0 if (int(elapsed) % 2 == 0) else 0.0

                pad.set_steer(steer)
                pad.set_throttle(throttle)
                pad.set_brake(brake)
                pad.update()

                # Print status ~4 times per second
                if tick % (tick_hz // 4) == 0:
                    bar_s = _bar(steer, -1.0, 1.0, width=20)
                    bar_t = _bar(throttle, 0.0, 1.0, width=10)
                    bar_b = _bar(brake, 0.0, 1.0, width=10)
                    print(
                        f"\r  t={elapsed:5.1f}s  "
                        f"steer={steer:+.2f} [{bar_s}]  "
                        f"throttle={throttle:.2f} [{bar_t}]  "
                        f"brake={brake:.2f} [{bar_b}]",
                        end="",
                        flush=True,
                    )

                tick += 1

                # Fixed-rate sleep
                next_time = t0 + tick * interval
                sleep_for = next_time - time.perf_counter()
                if sleep_for > 0:
                    time.sleep(sleep_for)

        except KeyboardInterrupt:
            print("\n\n[test] Interrupted by user.")

    print("\n[test] Done - controller axes reset to neutral.")


def _bar(value: float, lo: float, hi: float, width: int = 20) -> str:
    """Render a simple ASCII progress bar."""
    ratio = (value - lo) / (hi - lo) if hi != lo else 0.0
    filled = max(0, min(width, int(ratio * width)))
    return "#" * filled + "-" * (width - filled)


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 1: VirtualController self-test")
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Test duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--hz",
        type=int,
        default=250,
        help="Update tick rate in Hz (default: 250)",
    )
    args = parser.parse_args()
    run_test(duration=args.duration, tick_hz=args.hz)


if __name__ == "__main__":
    main()
