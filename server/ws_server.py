"""
ws_server.py — FastAPI/WebSocket server for Gyro Racing Wheel.

Milestone 2: Serves a dummy web page with manual sliders and handles
WebSocket communication. Data format: {seq, s, t, b}.

TCP_NODELAY is enabled for low-latency WebSocket communication.

Usage:
    python -m server.ws_server
    # Then open http://localhost:8000 in a browser
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .connection import (
    load_or_create_token, verify_token, setup_adb_reverse, ADBWatcher,
    generate_qr_code, setup_mkcert, get_local_ip, print_firewall_instructions,
)
from .hotkey import GlobalHotkeyListener
from .signal_pipeline import SignalPipeline, PipelineConfig
from .update_loop import InputState, UpdateLoop
from .virtual_controller import VirtualController

from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Shared state — written by WebSocket handler, read by update loop
input_state = InputState()

# Milestone 4: Signal pipeline for steering
pipeline_config = PipelineConfig()
signal_pipeline = SignalPipeline(pipeline_config)

# Controller + loop are initialized on startup
controller: VirtualController | None = None
update_loop: UpdateLoop | None = None
hotkey_listener: GlobalHotkeyListener | None = None
adb_watcher: ADBWatcher | None = None
server_port: int = 8000

active_connections: set[WebSocket] = set()
main_async_loop: asyncio.AbstractEventLoop | None = None
require_token: bool = False

STATIC_DIR = Path(__file__).parent.parent / "client"


# ------------------------------------------------------------------ #
#  Broadcast & Hotkey Recenter                                        #
# ------------------------------------------------------------------ #

async def broadcast_message(data: dict) -> None:
    """Send JSON message to all active WebSocket clients."""
    disconnected = []
    for client in list(active_connections):
        try:
            await client.send_json(data)
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        active_connections.discard(client)


def on_pc_recenter() -> None:
    """Invoked when user presses the global PC hotkey (Ctrl+Alt+C or F9)."""
    raw_angle = input_state.get_raw_steer()
    signal_pipeline.recenter(raw_angle)
    logger.info("PC Global Hotkey triggered recenter at %.2f deg", raw_angle)
    if main_async_loop and main_async_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            broadcast_message({"type": "recenter_ack", "recenter_offset": raw_angle}),
            main_async_loop,
        )


# ------------------------------------------------------------------ #
#  Lifecycle (Lifespan Context Manager)                               #
# ------------------------------------------------------------------ #

@asynccontextmanager
async def lifespan(app: FastAPI):
    global controller, update_loop, hotkey_listener, main_async_loop, adb_watcher
    main_async_loop = asyncio.get_running_loop()

    # Start background ADBWatcher so phone can be plugged in at any time
    try:
        adb_watcher = ADBWatcher(port=server_port, check_interval=2.0)
        adb_watcher.start()
    except Exception as e:
        logger.debug(f"Could not start ADBWatcher: {e}")

    try:
        controller = VirtualController()
        update_loop = UpdateLoop(controller, input_state, tick_rate=250)
        update_loop.start()
        logger.info("Server started — controller & update loop running (250 Hz)")
    except Exception as e:
        err_msg = str(e)
        logger.error("\n" + "=" * 62)
        logger.error("  [!] VIRTUAL CONTROLLER GAGAL DIINISIALISASI!")
        if "pyvjoystick" in err_msg.lower() or "nomodulenamed" in err_msg.lower().replace(" ", ""):
            logger.error("  PENYEBAB: Library 'pyvjoystick' belum terpasang.")
            logger.error("  SOLUSI:   Jalankan: pip install pyvjoystick")
        elif "bus_not_found" in err_msg.lower():
            logger.error("  PENYEBAB: Driver 'ViGEmBus' belum terpasang di Windows.")
            logger.error("  SOLUSI:   Unduh dan instal ViGEmBus installer dari:")
            logger.error("            https://github.com/nefarius/ViGEmBus/releases")
        else:
            logger.error(f"  PENYEBAB: {e}")
        logger.error("  Server akan berjalan dalam DUMMY MODE (tanpa gamepad virtual).")
        logger.error("=" * 62 + "\n")

    try:
        hotkey_listener = GlobalHotkeyListener(on_recenter=on_pc_recenter)
        hotkey_listener.start()
    except Exception as e:
        logger.warning(f"Could not start GlobalHotkeyListener: {e}")

    yield

    if adb_watcher:
        adb_watcher.stop()
    if hotkey_listener:
        hotkey_listener.stop()
    if update_loop:
        update_loop.stop()
    if controller:
        controller.close()
    logger.info("Server shut down cleanly")


# ------------------------------------------------------------------ #
#  App setup                                                          #
# ------------------------------------------------------------------ #

app = FastAPI(title="Gyro Racing Wheel", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------------------------------------------ #
#  Dummy slider page (Milestone 2)                                    #
# ------------------------------------------------------------------ #

DUMMY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gyro Racing Wheel — Dummy Controls</title>
<style>
  :root {
    --bg: #0f0f1a;
    --surface: #1a1a2e;
    --accent: #00d4ff;
    --accent2: #ff6b6b;
    --accent3: #51cf66;
    --text: #e0e0e0;
    --text-dim: #888;
    --border: #2a2a40;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 2rem;
  }

  h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
    background: linear-gradient(135deg, var(--accent), #7c5cfc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .subtitle {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin-bottom: 2rem;
  }

  .status-bar {
    display: flex;
    gap: 1rem;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
    justify-content: center;
  }

  .status-pill {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.35rem 0.9rem;
    font-size: 0.8rem;
  }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #ff4444;
    transition: background 0.3s;
  }

  .dot.connected { background: var(--accent3); }

  .controls {
    display: grid;
    gap: 1.5rem;
    width: 100%;
    max-width: 500px;
  }

  .slider-group {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.2rem;
    transition: border-color 0.2s;
  }

  .slider-group:hover { border-color: var(--accent); }

  .slider-label {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.7rem;
  }

  .slider-label span:first-child {
    font-weight: 500;
    font-size: 0.95rem;
  }

  .slider-value {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 0.95rem;
    min-width: 5ch;
    text-align: right;
  }

  input[type="range"] {
    width: 100%;
    height: 6px;
    -webkit-appearance: none;
    appearance: none;
    background: var(--border);
    border-radius: 3px;
    outline: none;
    cursor: pointer;
  }

  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    cursor: grab;
  }

  #steer-slider::-webkit-slider-thumb { background: var(--accent); }
  #throttle-slider::-webkit-slider-thumb { background: var(--accent3); }
  #brake-slider::-webkit-slider-thumb { background: var(--accent2); }

  .bar-container {
    height: 8px;
    background: var(--border);
    border-radius: 4px;
    margin-top: 0.5rem;
    overflow: hidden;
  }

  .bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.05s linear;
  }

  #steer-bar { background: var(--accent); }
  #throttle-bar { background: var(--accent3); }
  #brake-bar { background: var(--accent2); }

  .packet-log {
    margin-top: 1.5rem;
    width: 100%;
    max-width: 500px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
  }

  .packet-log h3 {
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-bottom: 0.5rem;
  }

  #log-content {
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 0.8rem;
    color: var(--accent);
    max-height: 120px;
    overflow-y: auto;
    white-space: pre;
    line-height: 1.5;
  }

  .send-rate {
    margin-top: 1rem;
    display: flex;
    align-items: center;
    gap: 0.8rem;
    font-size: 0.85rem;
  }

  .send-rate select {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.3rem 0.5rem;
    cursor: pointer;
  }
</style>
</head>
<body>

<h1>Gyro Racing Wheel</h1>
<p class="subtitle">Milestone 2 — Dummy Slider Controls</p>

<div class="status-bar">
  <div class="status-pill">
    <div class="dot" id="ws-dot"></div>
    <span id="ws-status">Disconnected</span>
  </div>
  <div class="status-pill">
    <span>Seq:</span>
    <span id="seq-display">0</span>
  </div>
  <div class="status-pill">
    <span>RTT:</span>
    <span id="rtt-display">—</span>
  </div>
</div>

<div class="controls">
  <div class="slider-group">
    <div class="slider-label">
      <span>Steering (s)</span>
      <span class="slider-value" id="steer-val">0.0</span>
    </div>
    <input type="range" id="steer-slider" min="-90" max="90" step="0.1" value="0">
    <div class="bar-container">
      <div class="bar-fill" id="steer-bar" style="width:50%;margin-left:0"></div>
    </div>
  </div>

  <div class="slider-group">
    <div class="slider-label">
      <span>Throttle (t)</span>
      <span class="slider-value" id="throttle-val">0.00</span>
    </div>
    <input type="range" id="throttle-slider" min="0" max="1" step="0.01" value="0">
    <div class="bar-container">
      <div class="bar-fill" id="throttle-bar" style="width:0%"></div>
    </div>
  </div>

  <div class="slider-group">
    <div class="slider-label">
      <span>Brake (b)</span>
      <span class="slider-value" id="brake-val">0.00</span>
    </div>
    <input type="range" id="brake-slider" min="0" max="1" step="0.01" value="0">
    <div class="bar-container">
      <div class="bar-fill" id="brake-bar" style="width:0%"></div>
    </div>
  </div>
</div>

<div class="send-rate">
  <span>Send rate:</span>
  <select id="rate-select">
    <option value="16">60 Hz</option>
    <option value="33">30 Hz</option>
    <option value="100">10 Hz</option>
    <option value="0">On change only</option>
  </select>
</div>

<div class="packet-log">
  <h3>Last Packets Sent</h3>
  <div id="log-content">Waiting for connection...</div>
</div>

<script>
(function() {
  "use strict";

  // --- Elements ---
  const steerSlider    = document.getElementById("steer-slider");
  const throttleSlider = document.getElementById("throttle-slider");
  const brakeSlider    = document.getElementById("brake-slider");
  const steerVal       = document.getElementById("steer-val");
  const throttleVal    = document.getElementById("throttle-val");
  const brakeVal       = document.getElementById("brake-val");
  const steerBar       = document.getElementById("steer-bar");
  const throttleBar    = document.getElementById("throttle-bar");
  const brakeBar       = document.getElementById("brake-bar");
  const wsDot          = document.getElementById("ws-dot");
  const wsStatus       = document.getElementById("ws-status");
  const seqDisplay     = document.getElementById("seq-display");
  const rttDisplay     = document.getElementById("rtt-display");
  const logContent     = document.getElementById("log-content");
  const rateSelect     = document.getElementById("rate-select");

  let ws = null;
  let seq = 0;
  let sendInterval = null;
  let logLines = [];
  const MAX_LOG_LINES = 8;

  // --- WebSocket ---
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = proto + "//" + location.host + "/ws";
    ws = new WebSocket(url);

    ws.onopen = () => {
      wsDot.classList.add("connected");
      wsStatus.textContent = "Connected";
      addLog(">> Connected to " + url);
      startSending();
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "pong" && msg.t_send) {
          const rtt = Date.now() - msg.t_send;
          rttDisplay.textContent = rtt + " ms";
        }
      } catch(e) {}
    };

    ws.onclose = () => {
      wsDot.classList.remove("connected");
      wsStatus.textContent = "Disconnected";
      addLog(">> Disconnected — reconnecting in 2s...");
      stopSending();
      setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      addLog(">> WebSocket error");
    };
  }

  // --- Sending ---
  function buildPacket() {
    seq++;
    return {
      seq: seq,
      s: parseFloat(steerSlider.value),
      t: parseFloat(throttleSlider.value),
      b: parseFloat(brakeSlider.value)
    };
  }

  function sendPacket() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const pkt = buildPacket();
    ws.send(JSON.stringify(pkt));
    seqDisplay.textContent = pkt.seq;
    addLog(JSON.stringify(pkt));
  }

  function sendPing() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "ping", t_send: Date.now() }));
  }

  function startSending() {
    stopSending();
    const ms = parseInt(rateSelect.value);
    if (ms > 0) {
      sendInterval = setInterval(sendPacket, ms);
    }
    // Ping every 2 seconds for RTT
    setInterval(sendPing, 2000);
  }

  function stopSending() {
    if (sendInterval) {
      clearInterval(sendInterval);
      sendInterval = null;
    }
  }

  // --- UI Updates ---
  function updateUI() {
    const s = parseFloat(steerSlider.value);
    const t = parseFloat(throttleSlider.value);
    const b = parseFloat(brakeSlider.value);

    steerVal.textContent = s.toFixed(1);
    throttleVal.textContent = t.toFixed(2);
    brakeVal.textContent = b.toFixed(2);

    // Steer bar: center-out
    const sPct = ((s + 90) / 180) * 100;
    if (s >= 0) {
      steerBar.style.width = ((s / 90) * 50) + "%";
      steerBar.style.marginLeft = "50%";
    } else {
      const w = ((-s / 90) * 50);
      steerBar.style.width = w + "%";
      steerBar.style.marginLeft = (50 - w) + "%";
    }

    throttleBar.style.width = (t * 100) + "%";
    brakeBar.style.width = (b * 100) + "%";
  }

  function addLog(line) {
    logLines.push(line);
    if (logLines.length > MAX_LOG_LINES) logLines.shift();
    logContent.textContent = logLines.join("\\n");
    logContent.scrollTop = logContent.scrollHeight;
  }

  // --- Event Listeners ---
  steerSlider.addEventListener("input", () => {
    updateUI();
    if (parseInt(rateSelect.value) === 0) sendPacket();
  });
  throttleSlider.addEventListener("input", () => {
    updateUI();
    if (parseInt(rateSelect.value) === 0) sendPacket();
  });
  brakeSlider.addEventListener("input", () => {
    updateUI();
    if (parseInt(rateSelect.value) === 0) sendPacket();
  });

  rateSelect.addEventListener("change", () => {
    if (ws && ws.readyState === WebSocket.OPEN) startSending();
  });

  // --- Init ---
  updateUI();
  connect();
})();
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ #
#  Routes                                                             #
# ------------------------------------------------------------------ #

@app.get("/", response_class=HTMLResponse)
async def get_index_page():
    """Serve the main racing wheel cockpit client, or dummy page if missing."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(
            index_file.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse(DUMMY_HTML)


@app.get("/dummy", response_class=HTMLResponse)
async def get_dummy_page():
    """Serve the dummy slider page (Milestone 2)."""
    return HTMLResponse(DUMMY_HTML)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Handle WebSocket connections from the client.

    Features:
      - Token authentication for non-localhost (Wi-Fi) connections
      - Low latency TCP_NODELAY
      - Recenter commands (sync with signal pipeline & PC hotkey)
      - Dynamic config synchronization
      - High-rate input packet processing through SignalPipeline
      - Failsafe: zeroes all inputs upon disconnection
    """
    # Verify token ONLY if specifically required (--require-token)
    if require_token:
        host = ws.client.host if ws.client else ""
        is_local = host in ("127.0.0.1", "localhost", "::1")
        if not is_local:
            token = ws.query_params.get("token")
            if not verify_token(token):
                logger.warning(f"Unauthorized connection attempt from {host}")
                await ws.close(code=4001, reason="Unauthorized: invalid or missing token")
                return

    await ws.accept()

    # Enable TCP_NODELAY for low latency
    try:
        raw_socket = ws.scope.get("transport")
        if raw_socket and hasattr(raw_socket, "get_extra_info"):
            sock = raw_socket.get_extra_info("socket")
            if sock:
                import socket
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                logger.info("TCP_NODELAY enabled")
    except Exception as e:
        logger.debug(f"Could not set TCP_NODELAY: {e}")

    client_info = f"{ws.client.host}:{ws.client.port}" if ws.client else "unknown"
    logger.info(f"Client connected: {client_info}")
    active_connections.add(ws)

    # Immediately send current server config to client & pulse Button A asynchronously to wake browser Gamepad API
    try:
        await ws.send_json({"type": "config", "config": signal_pipeline.config.snapshot()})
        if controller:
            asyncio.create_task(controller.async_pulse_button_a())
    except Exception as e:
        logger.warning(f"Could not send initial config: {e}")

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {client_info}")
                continue

            # Handle ping/pong for RTT measurement
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong", "t_send": msg.get("t_send")})
                continue

            # Handle virtual gamepad button press (e.g. Horn / Button A)
            if msg.get("type") == "button":
                btn = msg.get("btn", "A")
                action = msg.get("action", "pulse")
                if controller:
                    if action == "down":
                        controller.press_button(btn)
                    elif action == "up":
                        controller.release_button(btn)
                    else:
                        asyncio.create_task(controller.async_pulse_button_a())
                continue

            # Handle recenter request from UI
            if msg.get("type") == "recenter":
                angle = msg.get("angle")
                if angle is None:
                    angle = input_state.get_raw_steer()
                signal_pipeline.recenter(float(angle))
                logger.info("Recenter requested by client: %.2f deg", float(angle))
                await broadcast_message({"type": "recenter_ack", "recenter_offset": float(angle)})
                continue

            # Handle config updates from client
            if msg.get("type") == "config":
                cfg = msg.get("config", {})
                if isinstance(cfg, dict):
                    signal_pipeline.config.update(**cfg)
                    signal_pipeline.update_filter_params()
                    logger.info("Config updated by client: %s", signal_pipeline.config.snapshot())
                    await broadcast_message({"type": "config", "config": signal_pipeline.config.snapshot()})
                continue

            # Handle input packets: {seq, s, t, b}
            try:
                seq = int(msg.get("seq", 0))
                raw_s = float(msg.get("s", 0.0))
                t = max(0.0, min(1.0, float(msg.get("t", 0.0))))
                b = max(0.0, min(1.0, float(msg.get("b", 0.0))))
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid packet data: {e}")
                continue

            # Process raw steering angle through the full signal pipeline
            processed_s = signal_pipeline.process(raw_s)

            accepted = input_state.set(
                steer=processed_s,
                throttle=t,
                brake=b,
                seq=seq,
                raw_steer=raw_s,
            )
            if not accepted:
                logger.debug(f"Dropped stale packet seq={seq}")

    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {client_info}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        active_connections.discard(ws)
        # Failsafe: if all clients disconnect, immediately reset virtual controller inputs to zero
        if len(active_connections) == 0:
            input_state.reset_inputs()
            logger.info("Failsafe engaged: all inputs reset to zero")


# ------------------------------------------------------------------ #
#  Main entry point                                                   #
# ------------------------------------------------------------------ #

def main():
    import argparse
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Gyro Racing Wheel Server")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--adb", action="store_true", help="Setup ADB reverse port forwarding (verbose)")
    parser.add_argument("--require-token", action="store_true", help="Require authentication token for Wi-Fi connections")
    parser.add_argument("--qr", action="store_true", help="Show QR code for Wi-Fi connection")
    parser.add_argument("--https", action="store_true", help="Enable HTTPS with mkcert certificates")
    parser.add_argument("--firewall", action="store_true", help="Show firewall setup instructions")
    args = parser.parse_args()

    port = args.port
    global require_token, server_port
    require_token = args.require_token
    server_port = port

    # Auto setup ADB reverse on startup (silent info if no device, verbose if --adb passed)
    setup_adb_reverse(port, silent_fail=not args.adb)

    if args.firewall:
        print_firewall_instructions()

    ssl_certfile = None
    ssl_keyfile = None
    if args.https:
        certs = setup_mkcert(port)
        if certs:
            ssl_certfile, ssl_keyfile = certs

    ip = get_local_ip()
    scheme = "https" if ssl_certfile else "http"

    print("=" * 50)
    print("  Gyro Racing Wheel - Server")
    print(f"  USB:  http://localhost:{port}")
    print(f"  WiFi: {scheme}://{ip}:{port}")
    print("=" * 50)
    print()

    if args.qr:
        generate_qr_code(port, use_https=bool(ssl_certfile))

    uvicorn.run(
        "server.ws_server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )


if __name__ == "__main__":
    main()
