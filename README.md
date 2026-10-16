# Gyro Racing Wheel — Android → PC Virtual Controller

Turn your Android phone into an **ultra low-latency gyroscope racing wheel** with multi-touch pedals for PC racing simulators (*Assetto Corsa*, *Forza Horizon*, *Euro Truck Simulator 2*, *BeamNG.drive*, etc.).

---

## 🏎️ Architecture & Data Flow

```
┌─────────────────────────────────┐                 WebSocket                  ┌───────────────────────────────┐
│     Android Chrome Client       │ ─────────────────────────────────────────▸ │       Python PC Server        │
│                                 │       { seq, s, t, b } @ 60–120 Hz         │                               │
│  • Gimbal-Lock Free Tilt Sensor │                                            │  • FastAPI + Uvicorn          │
│    (Gravity vector via atan2)   │                                            │  • TCP_NODELAY (<40ms RTT)    │
│  • Multi-Touch Pedals           │ ◂───────────────────────────────────────── │  • One Euro Filter            │
│    (Pointer Events: Left=Brake) │       pong / config / recenter_ack         │  • Deadzone & Gamma Curves    │
│    (Right=Throttle)             │                                            │  • VirtualController (XInput) │
│  • Fullscreen & Wake Lock       │                                            │  • Global PC Hotkey (F9)      │
└─────────────────────────────────┘                                            └───────────────────────────────┘
```

---

## 📋 Prerequisites

1. **Python 3.10+**
2. **ViGEmBus driver** — download and install from [ViGEmBus GitHub Releases](https://github.com/nefarius/ViGEmBus/releases) (v1.22+ recommended).
3. **ADB (Android Debug Bridge)** *(Recommended for USB)* — included with Android SDK or [Standalone Platform Tools](https://developer.android.com/tools/releases/platform-tools).

---

## ⚡ Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Verify Gamepad Simulation
```bash
python -m tests.test_controller --duration 10
```
Open **joy.cpl** (Run dialog `Win + R` → `joy.cpl`) or [gamepad-tester.com](https://gamepad-tester.com) to watch axes sweep automatically.

### 3. Run the Server

```bash
python -m server.ws_server
```

#### 🔌 Method A: USB Cable via ADB Reverse (Recommended — Ultra Low Latency ~1-3ms)
1. Enable **USB Debugging** in Android Developer Options on your phone.
2. Connect your phone via USB cable (can be plugged in **before or after** starting the server — hotplug is auto-detected!).
3. Open `http://localhost:8000` in Chrome on your phone.

#### 📶 Method B: Wireless / Wi-Fi Connection
1. Ensure your PC and Phone are connected to the **same Wi-Fi network** (or phone hotspot).
2. Start the server (optionally add `--qr` to print a QR code in the terminal):
   ```bash
   python -m server.ws_server --qr
   ```
3. Open the Wi-Fi URL printed in the terminal (e.g., `http://192.168.1.13:8000`) in Chrome on your phone.
4. **Important for Chrome Android on Wi-Fi:** Chrome blocks motion sensors on plain HTTP. To enable gyro over Wi-Fi (takes 30 seconds, one-time setup):
   - In Chrome on your phone, open: `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
   - Enter your PC Wi-Fi URL (e.g. `http://192.168.1.13:8000`).
   - Change dropdown to **Enabled** and tap **Relaunch**.

> **Note on Browser Gamepad Testers (e.g. hardwaretester.com):**
> Browsers enforce Gamepad Fingerprinting Protection and will not expose any gamepad until a button is pressed while the browser tab is focused. Once `hardwaretester.com` is open on your PC, click the browser window and tap **HORN (A)** on your phone to wake it up. Native Windows games and `joy.cpl` detect the controller immediately without this restriction.

---

## 🎮 Cockpit Controls & Features

* **Starting the Wheel:** Tap **"START ENGINE"** on the phone screen to enter Fullscreen, lock landscape orientation, enable screen Wake Lock, and activate motion sensors.
* **Steering:** Tilt your phone left or right like a real steering wheel.
  * Uses the **Gravity Vector** (`accelerationIncludingGravity`) projected onto screen coordinates via `atan2(xScreen, -yScreen)` — completely free of gimbal lock and yaw drift!
* **Pedals (Multi-Touch Pointer Events):**
  * **Left Half:** BRAKE pedal (red visual fill + percentage readout).
  * **Right Half:** THROTTLE pedal (green visual fill + percentage readout).
  * Supports **Progressive Mode** (vertical slide distance) and **Binary Ramp Mode** (120ms smooth attack/decay).
* **Instant Recenter:**
  * **On Phone:** Tap the **RECENTER** button at the top-center of the screen.
  * **On PC (In-Game):** Press **`F9`** or **`Ctrl + Alt + C`** on your PC keyboard without taking your hands off the virtual wheel!
* **In-Game Action Buttons (PC Keyboard):**
  * Shift Up / Down, Handbrake, and Camera views are mapped to PC keyboard keys for optimal physical feedback.

---

## ⚙️ Live Dynamic Settings

Tap the **Settings gear icon** on the top HUD to adjust parameters in real-time:
* **Max Steering Angle:** 30° to 180° (default 90° for full lock).
* **Steering Deadzone:** Center deadband with smooth rescaling (default 2.0%).
* **Non-Linear Gamma:** Curve shaping (1.0 = linear, >1 = smoother center control).
* **Inverse Deadzone:** 0% to 30% compensation for in-game deadzones.
* **One Euro Filter:** Min cutoff and beta parameters for smooth hand tremor filtering without added lag.
* **Failsafe:** All inputs automatically drop to zero if the connection is interrupted.

---

## 🧪 Testing the Project

Run the full automated test suite:
```bash
python -m unittest discover tests
```

Individual test modules:
* `python -m unittest tests.test_signal_pipeline` — Verifies One Euro filter, deadzone rescaling, gamma curve, and recenter offset.
* `python -m unittest tests.test_integration` — Validates HTTP routes, WebSocket latency ping/pong, dynamic config, and disconnect failsafe.
* `python -m tests.test_controller` — Live VirtualController hardware sweep.
* `python -m tests.test_update_loop` — 250 Hz background loop integration test.

---

## 📁 Project Structure

```
├── client/
│   ├── index.html            # Cockpit HUD interface & Start screen
│   ├── style.css             # Dark gaming cockpit styles & touch layout
│   └── app.js                # Gravity sensor atan2, pointer pedals, WS streaming
├── server/
│   ├── __init__.py
│   ├── __main__.py
│   ├── connection.py         # ADB reverse, Wi-Fi QR token auth, mkcert HTTPS
│   ├── hotkey.py             # Windows RegisterHotKey listener (F9, Ctrl+Alt+C)
│   ├── signal_pipeline.py    # One Euro filter, recenter, deadzone, gamma curve
│   ├── update_loop.py        # Fixed 250 Hz VirtualController update thread
│   ├── virtual_controller.py # vgamepad XInput abstraction
│   └── ws_server.py          # FastAPI/WebSocket server with TCP_NODELAY
├── tests/
│   ├── test_controller.py
│   ├── test_integration.py
│   ├── test_signal_pipeline.py
│   └── test_update_loop.py
├── requirements.txt
└── spec.md
```

---

## 🏆 Completed Milestones

- [x] **Milestone 1:** Server & Virtual Controller (vgamepad abstraction, 250 Hz fixed loop, joy.cpl sweep test)
- [x] **Milestone 2:** WebSocket & Dummy Web Page (FastAPI, TCP_NODELAY, low latency data flow)
- [x] **Milestone 3:** ADB Reverse & Wi-Fi Connection (automation, QR code with token, mkcert SSL, firewall docs)
- [x] **Milestone 4:** Gravity Sensor, Signal Pipeline, & Recenter (gimbal-lock free atan2, One Euro filter, PC hotkeys)
- [x] **Milestone 5:** Fullscreen Multi-Touch Pedals & Immersive UI (Pointer Events, progressive/binary ramp, haptics)
- [x] **Milestone 6:** Real-Time Settings, Failsafe, & Latency (dynamic sync, disconnect failsafe, ping/pong RTT)
