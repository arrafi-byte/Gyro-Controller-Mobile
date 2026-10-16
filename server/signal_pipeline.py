"""
signal_pipeline.py — Milestone 4: Signal processing pipeline for steering input.

Pipeline order (as specified in the spec):
  1. One Euro Filter (noise reduction)
  2. Calibration & Normalization (recenter offset, max angle, clamp to [-1, 1])
  3. Deadzone with rescaling (smooth transition from zero)
  4. Gamma curve (non-linear response)
  5. Inverse deadzone compensation (optional, for in-game deadzone)
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field


# ------------------------------------------------------------------ #
#  One Euro Filter                                                    #
# ------------------------------------------------------------------ #

class OneEuroFilter:
    """One Euro Filter for smooth, low-latency signal filtering.

    Based on: https://cristal.univ-lille.fr/~casiez/1euro/

    Parameters:
        min_cutoff: Minimum cutoff frequency (Hz). Lower = smoother but more lag.
        beta: Speed coefficient. Higher = less lag at fast movements but more noise.
        d_cutoff: Cutoff frequency for the derivative filter.
    """

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.007,
        d_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff

        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    def reset(self) -> None:
        """Reset filter state."""
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    def __call__(self, x: float, t: float | None = None) -> float:
        """Filter value *x* at time *t* (seconds). If *t* is None, uses perf_counter."""
        if t is None:
            t = time.perf_counter()

        if self._x_prev is None:
            # First sample
            self._x_prev = x
            self._dx_prev = 0.0
            self._t_prev = t
            return x

        dt = t - self._t_prev
        if dt <= 0:
            dt = 1e-6  # avoid division by zero

        self._t_prev = t

        # Derivative
        dx = (x - self._x_prev) / dt

        # Filter the derivative
        alpha_d = self._alpha(self.d_cutoff, dt)
        dx_hat = alpha_d * dx + (1 - alpha_d) * self._dx_prev
        self._dx_prev = dx_hat

        # Adaptive cutoff
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)

        # Filter the signal
        alpha = self._alpha(cutoff, dt)
        x_hat = alpha * x + (1 - alpha) * self._x_prev
        self._x_prev = x_hat

        return x_hat

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        """Compute smoothing factor alpha from cutoff frequency and time step."""
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)


# ------------------------------------------------------------------ #
#  Pipeline Configuration                                             #
# ------------------------------------------------------------------ #

@dataclass
class PipelineConfig:
    """Signal pipeline configuration — all parameters adjustable at runtime."""

    # One Euro Filter
    filter_enabled: bool = True
    filter_min_cutoff: float = 1.0
    filter_beta: float = 0.007
    filter_d_cutoff: float = 1.0

    # Calibration
    recenter_offset: float = 0.0      # degrees — current center offset
    max_angle: float = 90.0           # degrees — maximum steering angle

    # Deadzone
    deadzone: float = 0.02            # fraction 0..1

    # Gamma curve
    gamma: float = 1.0                # 1.0 = linear, >1 = less sensitive near center

    # Inverse deadzone (for compensating in-game deadzone)
    inverse_deadzone: float = 0.0     # fraction 0..1 (typical: 0.20–0.25)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs) -> None:
        """Thread-safe update of parameters."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key) and not key.startswith("_"):
                    setattr(self, key, value)

    def snapshot(self) -> dict:
        """Thread-safe snapshot of current parameters."""
        with self._lock:
            return {
                "filter_enabled": self.filter_enabled,
                "filter_min_cutoff": self.filter_min_cutoff,
                "filter_beta": self.filter_beta,
                "filter_d_cutoff": self.filter_d_cutoff,
                "recenter_offset": self.recenter_offset,
                "max_angle": self.max_angle,
                "deadzone": self.deadzone,
                "gamma": self.gamma,
                "inverse_deadzone": self.inverse_deadzone,
            }


# ------------------------------------------------------------------ #
#  Signal Pipeline                                                    #
# ------------------------------------------------------------------ #

class SignalPipeline:
    """Processes raw steering angle (degrees) into controller value [-1, 1].

    Pipeline stages:
      1. One Euro Filter
      2. Recenter + normalize to [-1, 1]
      3. Deadzone with rescaling
      4. Gamma curve
      5. Inverse deadzone compensation
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self._filter = OneEuroFilter(
            min_cutoff=self.config.filter_min_cutoff,
            beta=self.config.filter_beta,
            d_cutoff=self.config.filter_d_cutoff,
        )

    def recenter(self, current_angle: float) -> None:
        """Set the current angle as the new center point."""
        self.config.update(recenter_offset=current_angle)

    def update_filter_params(self) -> None:
        """Re-sync filter parameters from config (after config change)."""
        snap = self.config.snapshot()
        self._filter.min_cutoff = snap["filter_min_cutoff"]
        self._filter.beta = snap["filter_beta"]
        self._filter.d_cutoff = snap["filter_d_cutoff"]

    def process(self, raw_angle_deg: float, timestamp: float | None = None) -> float:
        """Process raw angle through the full pipeline.

        Args:
            raw_angle_deg: Raw tilt angle in degrees from the sensor.
            timestamp: Optional timestamp in seconds (for One Euro Filter).

        Returns:
            Processed steering value in range [-1.0, 1.0].
        """
        snap = self.config.snapshot()

        # 1. One Euro Filter
        if snap.get("filter_enabled", True):
            filtered = self._filter(raw_angle_deg, timestamp)
        else:
            filtered = raw_angle_deg

        # 2. Calibration & Normalization
        centered = filtered - snap["recenter_offset"]
        max_ang = snap["max_angle"]
        if max_ang <= 0:
            max_ang = 90.0
        normalized = centered / max_ang
        normalized = max(-1.0, min(1.0, normalized))  # clamp

        # 3. Deadzone with rescaling
        dz = snap["deadzone"]
        normalized = self._apply_deadzone(normalized, dz)

        # 4. Gamma curve
        gamma = snap["gamma"]
        if gamma != 1.0 and gamma > 0:
            sign = 1.0 if normalized >= 0 else -1.0
            normalized = sign * (abs(normalized) ** gamma)

        # 5. Inverse deadzone compensation
        inv_dz = snap["inverse_deadzone"]
        if inv_dz > 0:
            normalized = self._apply_inverse_deadzone(normalized, inv_dz)

        return normalized

    @staticmethod
    def _apply_deadzone(value: float, deadzone: float) -> float:
        """Apply center deadzone with smooth rescaling.

        If |value| < deadzone → 0.0
        Otherwise rescale so output transitions smoothly from 0.0.
        """
        if deadzone <= 0 or deadzone >= 1.0:
            return value

        abs_val = abs(value)
        if abs_val < deadzone:
            return 0.0

        sign = 1.0 if value >= 0 else -1.0
        rescaled = (abs_val - deadzone) / (1.0 - deadzone)
        return sign * rescaled

    @staticmethod
    def _apply_inverse_deadzone(value: float, inv_deadzone: float) -> float:
        """Compensate for in-game deadzone by expanding small values.

        Maps the output range [0, 1] to [inv_deadzone, 1] so small
        movements are still registered by the game.
        """
        if inv_deadzone <= 0 or value == 0:
            return value

        sign = 1.0 if value >= 0 else -1.0
        abs_val = abs(value)
        expanded = inv_deadzone + abs_val * (1.0 - inv_deadzone)
        return sign * min(1.0, expanded)
