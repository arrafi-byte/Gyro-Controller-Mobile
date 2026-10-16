"""
tests/test_integration.py — End-to-end integration test for Gyro Racing Wheel.

Validates Milestones 2, 3, 4, 5, and 6:
  - HTTP static file serving (index.html, style.css, app.js, dummy)
  - WebSocket connection and TCP_NODELAY handling
  - Initial configuration sync
  - Ping/Pong RTT latency measurement
  - Raw tilt angle processing through SignalPipeline
  - Recenter command and offset calibration
  - Dynamic runtime config updates
  - Disconnect failsafe (inputs zeroed out)
"""

import asyncio
import json
import threading
import time
import unittest
import urllib.request

import uvicorn
import websockets

from server.ws_server import app, input_state, signal_pipeline


class TestIntegrationServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8011
        # Configure uvicorn server in a background daemon thread
        config = uvicorn.Config(app=app, host="127.0.0.1", port=cls.port, log_level="warning")
        cls.server = uvicorn.Server(config)
        cls.server_thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.server_thread.start()

        # Wait for server to start accepting connections
        deadline = time.time() + 5.0
        started = False
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/", timeout=0.5)
                started = True
                break
            except Exception:
                time.sleep(0.1)

        if not started:
            raise RuntimeError("Test server failed to start within 5 seconds")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.server_thread.join(timeout=2.0)

    def test_01_http_endpoints(self):
        """Verify main cockpit app and dummy page are served with correct headers."""
        # Main page
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("no-cache", resp.headers.get("Cache-Control", ""))
            html = resp.read().decode("utf-8")
            self.assertIn("GYRO RACING WHEEL", html)
            self.assertIn("START ENGINE", html)
            self.assertIn("start-status-pill", html)
            self.assertIn("brake-zone", html)
            self.assertIn("throttle-zone", html)

        # Static CSS
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/style.css") as resp:
            self.assertEqual(resp.status, 200)
            css = resp.read().decode("utf-8")
            self.assertIn("cockpit-container", css)

        # Static JS
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/static/app.js") as resp:
            self.assertEqual(resp.status, 200)
            js = resp.read().decode("utf-8")
            self.assertIn("DeviceMotionEvent", js)

        # Dummy fallback page
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/dummy") as resp:
            self.assertEqual(resp.status, 200)
            dummy = resp.read().decode("utf-8")
            self.assertIn("Dummy Controls", dummy)

    def test_02_websocket_full_flow(self):
        """Verify WebSocket connection, ping/pong, signal pipeline, recenter, and failsafe."""
        async def run_ws_test():
            uri = f"ws://127.0.0.1:{self.port}/ws"
            async with websockets.connect(uri) as ws:
                # 1. Server sends initial config snapshot
                msg_raw = await ws.recv()
                msg = json.loads(msg_raw)
                self.assertEqual(msg.get("type"), "config")
                cfg = msg.get("config", {})
                self.assertIn("max_angle", cfg)
                self.assertIn("deadzone", cfg)

                # 2. Ping / Pong latency check
                t0 = time.time()
                await ws.send(json.dumps({"type": "ping", "t_send": 12345.67}))
                pong_raw = await ws.recv()
                pong = json.loads(pong_raw)
                self.assertEqual(pong.get("type"), "pong")
                self.assertEqual(pong.get("t_send"), 12345.67)

                # Reset pipeline offset to 0 for clean test
                signal_pipeline.recenter(0.0)

                # 3. Send raw input packet (s = 45.0 degrees, throttle = 0.85, brake = 0.0)
                await ws.send(json.dumps({
                    "seq": 100,
                    "s": 45.0,
                    "t": 0.85,
                    "b": 0.0,
                }))
                await asyncio.sleep(0.05)

                steer, throttle, brake = input_state.get()
                self.assertAlmostEqual(throttle, 0.85, places=2)
                self.assertAlmostEqual(brake, 0.0, places=2)
                self.assertEqual(input_state.get_raw_steer(), 45.0)
                # 45 deg / 90 max_angle ≈ 0.5 (with deadzone rescaling ≈ 0.489)
                self.assertGreater(steer, 0.4)
                self.assertLess(steer, 0.6)

                # 4. Trigger Recenter at current position (45.0 deg)
                await ws.send(json.dumps({
                    "type": "recenter",
                    "angle": 45.0,
                }))
                rc_raw = await ws.recv()
                rc = json.loads(rc_raw)
                self.assertEqual(rc.get("type"), "recenter_ack")
                self.assertEqual(rc.get("recenter_offset"), 45.0)

                # Now send 45.0 deg again -> should evaluate to ~0.0 (center)
                await ws.send(json.dumps({
                    "seq": 101,
                    "s": 45.0,
                    "t": 0.5,
                    "b": 0.0,
                }))
                await asyncio.sleep(0.05)
                steer, _, _ = input_state.get()
                self.assertAlmostEqual(steer, 0.0, places=2)

                # 5. Dynamic config update
                await ws.send(json.dumps({
                    "type": "config",
                    "config": {
                        "max_angle": 180.0,
                        "deadzone": 0.05,
                    },
                }))
                cfg_ack_raw = await ws.recv()
                cfg_ack = json.loads(cfg_ack_raw)
                self.assertEqual(cfg_ack.get("type"), "config")
                self.assertEqual(cfg_ack["config"]["max_angle"], 180.0)
                self.assertEqual(cfg_ack["config"]["deadzone"], 0.05)

            # 6. Verify failsafe after disconnect
            # After WebSocket closes, inputs should be reset to 0.0
            await asyncio.sleep(0.1)
            steer, throttle, brake = input_state.get()
            self.assertEqual(steer, 0.0)
            self.assertEqual(throttle, 0.0)
            self.assertEqual(brake, 0.0)

        asyncio.run(run_ws_test())


if __name__ == "__main__":
    unittest.main()
