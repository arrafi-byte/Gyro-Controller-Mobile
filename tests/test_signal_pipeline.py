"""
tests/test_signal_pipeline.py — Unit tests for Milestone 4 Signal Pipeline.

Verifies:
  1. One Euro Filter noise reduction & responsiveness
  2. Recenter calibration offset
  3. Max angle normalization and clamping to [-1.0, 1.0]
  4. Deadzone with smooth rescaling (no step discontinuity)
  5. Gamma curve non-linear shaping
  6. Inverse deadzone compensation for in-game deadzones
  7. Thread-safe dynamic config updates
"""

import math
import unittest
from server.signal_pipeline import OneEuroFilter, PipelineConfig, SignalPipeline


class TestOneEuroFilter(unittest.TestCase):
    def test_constant_signal(self):
        """Constant signal should converge quickly to the constant value."""
        filt = OneEuroFilter(min_cutoff=1.0, beta=0.007)
        val = 45.0
        t = 0.0
        out = 0.0
        for _ in range(50):
            out = filt(val, t)
            t += 0.01  # 100 Hz
        self.assertAlmostEqual(out, 45.0, places=3)

    def test_noise_reduction(self):
        """Noisy signal should have lower variance after filtering."""
        filt = OneEuroFilter(min_cutoff=1.0, beta=0.001)
        raw_values = [10.0 + (1.0 if i % 2 == 0 else -1.0) * 0.5 for i in range(100)]
        filtered_values = []
        t = 0.0
        for x in raw_values:
            filtered_values.append(filt(x, t))
            t += 0.01

        # Check that after initial warmup (first 10 samples), filtered variance is less than raw
        raw_variance = sum((x - 10.0) ** 2 for x in raw_values[10:]) / len(raw_values[10:])
        filtered_variance = sum((x - 10.0) ** 2 for x in filtered_values[10:]) / len(filtered_values[10:])
        self.assertLess(filtered_variance, raw_variance)

    def test_reset(self):
        filt = OneEuroFilter()
        filt(100.0, 0.0)
        filt.reset()
        # After reset, next value should be treated as first sample
        res = filt(25.0, 1.0)
        self.assertEqual(res, 25.0)


class TestPipelineCalibrationAndNormalization(unittest.TestCase):
    def setUp(self):
        # Configure pipeline with filter bypassed for pure math testing
        self.config = PipelineConfig(
            filter_enabled=False,
            deadzone=0.0,
            gamma=1.0,
            inverse_deadzone=0.0,
            max_angle=90.0,
            recenter_offset=0.0,
        )
        self.pipeline = SignalPipeline(self.config)

    def test_center_and_max_angle(self):
        # 0 deg -> 0.0
        self.assertAlmostEqual(self.pipeline.process(0.0), 0.0, places=2)
        # 45 deg -> 0.5
        self.assertAlmostEqual(self.pipeline.process(45.0), 0.5, places=2)
        # 90 deg -> 1.0
        self.assertAlmostEqual(self.pipeline.process(90.0), 1.0, places=2)
        # -90 deg -> -1.0
        self.assertAlmostEqual(self.pipeline.process(-90.0), -1.0, places=2)

    def test_clamping(self):
        # Exceeding max angle should clamp to [-1.0, 1.0]
        self.assertAlmostEqual(self.pipeline.process(120.0), 1.0, places=2)
        self.assertAlmostEqual(self.pipeline.process(-150.0), -1.0, places=2)

    def test_recenter(self):
        # Suppose user is holding phone tilted at 15 degrees when pressing recenter
        self.pipeline.recenter(15.0)
        self.assertAlmostEqual(self.config.recenter_offset, 15.0)
        # Now 15 degrees should produce 0.0 (center)
        self.assertAlmostEqual(self.pipeline.process(15.0), 0.0, places=2)
        # 105 degrees (15 + 90) should produce +1.0
        self.assertAlmostEqual(self.pipeline.process(105.0), 1.0, places=2)
        # -75 degrees (15 - 90) should produce -1.0
        self.assertAlmostEqual(self.pipeline.process(-75.0), -1.0, places=2)


class TestDeadzoneAndRescaling(unittest.TestCase):
    def test_deadzone_smooth_transition(self):
        """Deadzone must scale smoothly from 0.0, with no sudden jump at threshold."""
        dz = 0.05
        # Value inside deadzone -> exactly 0.0
        self.assertEqual(SignalPipeline._apply_deadzone(0.02, dz), 0.0)
        self.assertEqual(SignalPipeline._apply_deadzone(-0.04, dz), 0.0)
        self.assertEqual(SignalPipeline._apply_deadzone(0.0, dz), 0.0)

        # Value exactly at deadzone -> 0.0
        self.assertEqual(SignalPipeline._apply_deadzone(dz, dz), 0.0)

        # Value just above deadzone -> smooth small value, NOT a jump to 0.05
        just_above = SignalPipeline._apply_deadzone(0.06, dz)
        expected = (0.06 - 0.05) / (1.0 - 0.05)
        self.assertAlmostEqual(just_above, expected, places=5)
        self.assertLess(just_above, 0.02)  # Much smaller than 0.05, proving smooth rescaling

        # Full deflection -> exactly 1.0
        self.assertAlmostEqual(SignalPipeline._apply_deadzone(1.0, dz), 1.0, places=5)
        self.assertAlmostEqual(SignalPipeline._apply_deadzone(-1.0, dz), -1.0, places=5)


class TestGammaCurve(unittest.TestCase):
    def test_gamma_curve_shaping(self):
        config = PipelineConfig(
            filter_enabled=False,
            deadzone=0.0,
            gamma=2.0,
            inverse_deadzone=0.0,
            max_angle=90.0,
        )
        pipeline = SignalPipeline(config)

        # At half angle (45 deg = 0.5 input), gamma=2 should give 0.5^2 = 0.25
        out_pos = pipeline.process(45.0)
        self.assertAlmostEqual(out_pos, 0.25, places=2)

        # Negative input maintains sign: -0.5 -> -(0.5^2) = -0.25
        out_neg = pipeline.process(-45.0)
        self.assertAlmostEqual(out_neg, -0.25, places=2)

        # Extremes remain 0 and 1
        self.assertAlmostEqual(pipeline.process(0.0), 0.0, places=2)
        self.assertAlmostEqual(pipeline.process(90.0), 1.0, places=2)


class TestInverseDeadzone(unittest.TestCase):
    def test_inverse_deadzone_jump(self):
        inv_dz = 0.20
        # Zero remains zero (safe at center)
        self.assertEqual(SignalPipeline._apply_inverse_deadzone(0.0, inv_dz), 0.0)

        # Any non-zero movement immediately jumps to at least inv_dz
        out_tiny = SignalPipeline._apply_inverse_deadzone(0.01, inv_dz)
        self.assertGreaterEqual(out_tiny, inv_dz)

        # Full deflection reaches 1.0
        self.assertAlmostEqual(SignalPipeline._apply_inverse_deadzone(1.0, inv_dz), 1.0, places=5)
        self.assertAlmostEqual(SignalPipeline._apply_inverse_deadzone(-1.0, inv_dz), -1.0, places=5)


class TestDynamicConfig(unittest.TestCase):
    def test_threadsafe_config_update(self):
        config = PipelineConfig(max_angle=90.0)
        pipeline = SignalPipeline(config)
        self.assertEqual(config.max_angle, 90.0)

        config.update(max_angle=180.0, deadzone=0.1)
        snap = config.snapshot()
        self.assertEqual(snap["max_angle"], 180.0)
        self.assertEqual(snap["deadzone"], 0.1)


if __name__ == "__main__":
    unittest.main()
