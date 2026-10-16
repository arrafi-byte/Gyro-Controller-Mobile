"""
update_loop — Fixed-rate update loop for the VirtualController.

Runs VirtualController.update() at a fixed tick rate (default 250 Hz)
in a dedicated thread, reading the latest input values from a shared
state object that is updated asynchronously by the WebSocket handler.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from .virtual_controller import VirtualController

logger = logging.getLogger(__name__)


@dataclass
class InputState:
    """Thread-safe shared state holding the latest input values.

    Written to by the network layer, read by the update loop.
    """

    steer: float = 0.0          # Processed steer [-1.0, 1.0] for VirtualController
    raw_steer: float = 0.0      # Raw tilt angle in degrees
    throttle: float = 0.0       # Throttle [0.0, 1.0]
    brake: float = 0.0          # Brake [0.0, 1.0]
    seq: int = 0
    last_update_time: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(
        self,
        *,
        steer: float,
        throttle: float,
        brake: float,
        seq: int,
        raw_steer: float | None = None,
    ) -> bool:
        """Update all values atomically.

        Returns True if the packet was accepted (seq >= current seq),
        False if the packet is stale and was dropped.
        """
        with self._lock:
            if seq < self.seq:
                return False  # stale / out-of-order packet
            self.steer = steer
            if raw_steer is not None:
                self.raw_steer = raw_steer
            self.throttle = throttle
            self.brake = brake
            self.seq = seq
            self.last_update_time = time.perf_counter()
            return True

    def get(self) -> tuple[float, float, float]:
        """Return (steer, throttle, brake) snapshot."""
        with self._lock:
            return self.steer, self.throttle, self.brake

    def get_raw_steer(self) -> float:
        """Return the latest raw steering angle in degrees."""
        with self._lock:
            return self.raw_steer

    def reset_inputs(self) -> None:
        """Failsafe: immediately zero out all inputs (e.g. on disconnect)."""
        with self._lock:
            self.steer = 0.0
            self.throttle = 0.0
            self.brake = 0.0
            self.last_update_time = time.perf_counter()


class UpdateLoop:
    """Runs VirtualController.update() at a fixed frequency in a background thread."""

    def __init__(
        self,
        controller: VirtualController,
        state: InputState,
        tick_rate: int = 250,
    ) -> None:
        self._controller = controller
        self._state = state
        self._tick_rate = tick_rate
        self._interval = 1.0 / tick_rate
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the update loop thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="update-loop")
        self._thread.start()
        logger.info("Update loop started at %d Hz", self._tick_rate)

    def stop(self) -> None:
        """Signal the update loop to stop and wait for the thread to join."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Update loop stopped")

    def _loop(self) -> None:
        """Main loop — runs until self._running is cleared."""
        next_tick = time.perf_counter()
        while self._running:
            steer, throttle, brake = self._state.get()
            self._controller.set_steer(steer)
            self._controller.set_throttle(throttle)
            self._controller.set_brake(brake)
            self._controller.update()

            # Fixed-rate timing: sleep until the next tick
            next_tick += self._interval
            sleep_time = next_tick - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                # We fell behind — reset the baseline to avoid a burst of catch-up ticks
                next_tick = time.perf_counter()
