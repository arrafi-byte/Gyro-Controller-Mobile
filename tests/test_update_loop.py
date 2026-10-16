"""
test_update_loop.py — Milestone 1 UpdateLoop integration test.

Verifies that InputState + UpdateLoop + VirtualController work together:
  1. Creates a VirtualController
  2. Creates an InputState and UpdateLoop at 250 Hz
  3. Programmatically changes InputState values
  4. Confirms the loop is running and applying values

Usage:
    python -m tests.test_update_loop
"""

from __future__ import annotations

import math
import sys
import time

sys.path.insert(0, ".")

from server.update_loop import InputState, UpdateLoop
from server.virtual_controller import VirtualController


def main() -> None:
    print("[test] UpdateLoop integration test")
    print("[test] Open joy.cpl or https://gamepad-tester.com to observe.\n")

    state = InputState()
    controller = VirtualController()
    loop = UpdateLoop(controller, state, tick_rate=250)

    try:
        loop.start()
        t0 = time.perf_counter()
        seq = 0

        while True:
            elapsed = time.perf_counter() - t0
            if elapsed > 10.0:
                break

            # Simulate incoming network packets updating the shared state
            steer = math.sin(elapsed * math.pi)
            throttle = (elapsed % 3.0) / 3.0
            brake = 1.0 if int(elapsed) % 2 == 0 else 0.0
            seq += 1

            state.set(steer=steer, throttle=throttle, brake=brake, seq=seq)

            # Simulate ~60 Hz input rate (like WebSocket messages)
            time.sleep(1.0 / 60)

            if seq % 15 == 0:
                print(
                    f"\r  t={elapsed:5.1f}s  seq={seq:4d}  "
                    f"steer={steer:+.2f}  throttle={throttle:.2f}  brake={brake:.2f}",
                    end="",
                    flush=True,
                )

    except KeyboardInterrupt:
        print("\n\n[test] Interrupted.")
    finally:
        loop.stop()
        controller.close()

    print("\n[test] Done.")


if __name__ == "__main__":
    main()
