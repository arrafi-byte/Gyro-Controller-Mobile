"""
VirtualController — Abstraction layer over pyvjoystick/vigem (ViGEmBus Xbox 360 controller).

Exposes a simple interface:
    set_steer(val)    – float -1.0 (full left) to 1.0 (full right)
    set_throttle(val) – float 0.0 to 1.0
    set_brake(val)    – float 0.0 to 1.0
    update()          – flush current state to the virtual device

The backend can be swapped to vJoy (DirectInput) in the future by implementing
the same interface without changing any other code.

Uses pyvjoystick (drop-in replacement for vgamepad) which supports Python 3.13+.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Protocol

logger = logging.getLogger(__name__)


class VirtualControllerInterface(Protocol):
    """Protocol that any controller backend must satisfy."""

    def set_steer(self, val: float) -> None: ...
    def set_throttle(self, val: float) -> None: ...
    def set_brake(self, val: float) -> None: ...
    def update(self) -> None: ...
    def close(self) -> None: ...


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* between *lo* and *hi*."""
    return max(lo, min(hi, value))


class VirtualController:
    """Xbox 360 virtual controller backed by pyvjoystick / ViGEmBus.

    Steering is mapped to the **left joystick X-axis**.
    Throttle → right trigger, Brake → left trigger.
    """

    def __init__(self) -> None:
        try:
            import pyvjoystick.vigem as vg
        except ImportError:
            logger.error(
                "pyvjoystick is not installed. Run: pip install pyvjoystick"
            )
            raise

        try:
            self._pad = vg.VX360Gamepad()
        except Exception as exc:
            if "BUS_NOT_FOUND" in str(exc):
                logger.error(
                    "ViGEmBus driver is not installed!\n"
                    "Download and install it from:\n"
                    "  https://github.com/nefarius/ViGEmBus/releases\n"
                    "(Use v1.22.0 or newer to avoid the updater IP-leak bug)"
                )
            raise

        self._steer: float = 0.0
        self._throttle: float = 0.0
        self._brake: float = 0.0
        self._lock = threading.Lock()
        logger.info("VirtualController created (Xbox 360 / ViGEmBus)")

    # ------------------------------------------------------------------ #
    #  Public setters                                                     #
    # ------------------------------------------------------------------ #

    def set_steer(self, val: float) -> None:
        """Set steering value in range [-1.0, 1.0].

        Negative = left, positive = right.
        """
        self._steer = _clamp(val, -1.0, 1.0)

    def set_throttle(self, val: float) -> None:
        """Set throttle (gas) value in range [0.0, 1.0]."""
        self._throttle = _clamp(val)

    def set_brake(self, val: float) -> None:
        """Set brake value in range [0.0, 1.0]."""
        self._brake = _clamp(val)

    # ------------------------------------------------------------------ #
    #  Button actions (Thread-safe)                                       #
    # ------------------------------------------------------------------ #

    def press_button(self, button: str = "A") -> None:
        """Press a virtual button (e.g. 'A', 'B', 'X', 'Y')."""
        try:
            import pyvjoystick.vigem as vg
            btn_map = {
                "A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
                "B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
                "X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
                "Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            }
            if button in btn_map:
                with self._lock:
                    self._pad.press_button(button=btn_map[button])
                    self._pad.update()
        except Exception as e:
            logger.debug(f"Could not press button {button}: {e}")

    def release_button(self, button: str = "A") -> None:
        """Release a virtual button."""
        try:
            import pyvjoystick.vigem as vg
            btn_map = {
                "A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
                "B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
                "X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
                "Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            }
            if button in btn_map:
                with self._lock:
                    self._pad.release_button(button=btn_map[button])
                    self._pad.update()
        except Exception as e:
            logger.debug(f"Could not release button {button}: {e}")

    async def async_pulse_button_a(self, duration: float = 0.08) -> None:
        """Asynchronously tap Button A without blocking the asyncio event loop."""
        import asyncio
        self.press_button("A")
        await asyncio.sleep(duration)
        self.release_button("A")
        logger.info("Pulsed Gamepad Button A (wakes up browser Gamepad API)")

    def pulse_button_a(self, duration: float = 0.08) -> None:
        """Synchronously tap Button A (for non-async callers)."""
        import time
        self.press_button("A")
        time.sleep(duration)
        self.release_button("A")
        logger.info("Pulsed Gamepad Button A (sync)")

    # ------------------------------------------------------------------ #
    #  Flush to device (Thread-safe)                                      #
    # ------------------------------------------------------------------ #

    def update(self) -> None:
        """Push the current steer / throttle / brake state to the virtual gamepad."""
        with self._lock:
            self._pad.left_joystick_float(x_value_float=self._steer, y_value_float=0.0)
            self._pad.right_trigger_float(value_float=self._throttle)
            self._pad.left_trigger_float(value_float=self._brake)
            self._pad.update()

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                          #
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Reset axes to neutral and release the virtual gamepad."""
        try:
            with self._lock:
                self._pad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
                self._pad.right_trigger_float(value_float=0.0)
                self._pad.left_trigger_float(value_float=0.0)
                self._pad.update()
        except Exception:
            pass
        logger.info("VirtualController closed")

    def __enter__(self) -> "VirtualController":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
