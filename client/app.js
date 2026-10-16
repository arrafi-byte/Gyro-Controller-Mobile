/**
 * Gyro Racing Wheel — Client Application
 * 
 * Features:
 *  - Fullscreen & Wake Lock management
 *  - Gimbal-lock free gravity vector tilt calculation via atan2
 *  - Fallback to RelativeOrientationSensor
 *  - Fullscreen multi-touch pedals (Pointer Events) with Progressive & Binary Ramp modes
 *  - Recenter action (UI button + server synchronization)
 *  - Low-latency WebSocket streaming @ 60-120 Hz with ping/pong RTT measurement
 *  - Dynamic server config two-way synchronization
 */

(() => {
  'use strict';

  // --- DOM Elements ---
  const startOverlay = document.getElementById('start-overlay');
  const startBtn = document.getElementById('start-btn');
  const startStatusPill = document.getElementById('start-status-pill');
  const startStatusLabel = document.getElementById('start-status-label');
  const connectionTipText = document.getElementById('connection-mode-text');

  const statusPill = document.getElementById('status-pill');
  const statusLabel = document.getElementById('status-label');
  const sensorStatus = document.getElementById('sensor-status');
  const rttValue = document.getElementById('rtt-value');
  const rateValue = document.getElementById('rate-value');

  const recenterBtn = document.getElementById('recenter-btn');
  const hornBtn = document.getElementById('horn-btn');
  const fullscreenBtn = document.getElementById('fullscreen-btn');
  const settingsBtn = document.getElementById('settings-btn');

  const virtualWheel = document.getElementById('virtual-wheel');
  const gaugeArc = document.getElementById('gauge-arc');
  const steerDegreesText = document.getElementById('steer-degrees');

  const brakeZone = document.getElementById('brake-zone');
  const brakeFill = document.getElementById('brake-fill');
  const brakeValue = document.getElementById('brake-value');
  const brakeMeter = document.getElementById('brake-meter');

  const throttleZone = document.getElementById('throttle-zone');
  const throttleFill = document.getElementById('throttle-fill');
  const throttleValue = document.getElementById('throttle-value');
  const throttleMeter = document.getElementById('throttle-meter');

  // Modal elements
  const settingsModal = document.getElementById('settings-modal');
  const closeSettingsBtn = document.getElementById('close-settings-btn');
  const saveSettingsBtn = document.getElementById('save-settings-btn');
  const resetDefaultsBtn = document.getElementById('reset-defaults-btn');

  const cfgMaxAngle = document.getElementById('cfg-max-angle');
  const valMaxAngle = document.getElementById('val-max-angle');
  const cfgDeadzone = document.getElementById('cfg-deadzone');
  const valDeadzone = document.getElementById('val-deadzone');
  const cfgGamma = document.getElementById('cfg-gamma');
  const valGamma = document.getElementById('val-gamma');
  const cfgInvDeadzone = document.getElementById('cfg-inv-deadzone');
  const valInvDeadzone = document.getElementById('val-inv-deadzone');
  const cfgFilterCutoff = document.getElementById('cfg-filter-cutoff');
  const valFilterCutoff = document.getElementById('val-filter-cutoff');
  const cfgFilterBeta = document.getElementById('cfg-filter-beta');
  const valFilterBeta = document.getElementById('val-filter-beta');
  const cfgFilterEnabled = document.getElementById('cfg-filter-enabled');
  const cfgHaptics = document.getElementById('cfg-haptics');

  // --- State Variables ---
  let ws = null;
  let isConnected = false;
  let seq = 0;
  let wakeLock = null;

  // Steering & Pedals
  let rawSteering = 0.0;     // Current raw tilt angle in degrees
  let throttle = 0.0;        // 0.0 to 1.0
  let brake = 0.0;           // 0.0 to 1.0

  // Touch tracking for progressive / binary ramp pedals
  const touchState = {
    brakePointerId: null,
    brakeStartY: 0,
    brakeTarget: 0.0,
    throttlePointerId: null,
    throttleStartY: 0,
    throttleTarget: 0.0,
  };

  // Local settings & defaults
  const settings = {
    max_angle: 90.0,
    deadzone: 0.02,
    gamma: 1.0,
    inverse_deadzone: 0.0,
    filter_enabled: true,
    filter_min_cutoff: 1.0,
    filter_beta: 0.007,
    pedal_mode: 'progressive', // 'progressive' | 'binary'
    sensor_source: 'gravity',  // 'gravity' | 'relative'
    haptics: true,
    throw_distance: 140,       // px travel for full 100% pedal in progressive mode
  };

  // FPS & Hz counters
  let packetCount = 0;
  let lastRateTimestamp = performance.now();

  // --- Fullscreen & Wake Lock ---
  async function requestFullscreen() {
    try {
      if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      }
    } catch (err) {
      console.warn('Fullscreen request failed:', err);
    }
  }

  async function requestOrientationLock() {
    try {
      if (screen.orientation && screen.orientation.lock) {
        await screen.orientation.lock('landscape');
      }
    } catch (err) {
      console.warn('Orientation lock failed:', err);
    }
  }

  async function requestWakeLock() {
    try {
      if ('wakeLock' in navigator) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => {
          wakeLock = null;
        });
      }
    } catch (err) {
      console.warn('Wake Lock request failed:', err);
    }
  }

  // --- Vibration Helper ---
  function vibrate(pattern) {
    if (settings.haptics && navigator.vibrate) {
      try {
        navigator.vibrate(pattern);
      } catch (_) {}
    }
  }

  let motionEventsCount = 0;
  let orientationEventsCount = 0;
  let fallbackSensorActive = false;

  // Robust tilt calculation taking into account Auto-Rotate state and Android sensor coordinates
  function computeTiltAngle(ax, ay, screenAngle) {
    // 1. Browser reports Landscape Primary (90 deg)
    if (screenAngle === 90) {
      return Math.atan2(ay, ax) * (180.0 / Math.PI);
    }
    // 2. Browser reports Landscape Secondary (270 / -90 deg)
    if (screenAngle === 270 || screenAngle === -90) {
      return Math.atan2(-ay, -ax) * (180.0 / Math.PI);
    }
    // 3. Inverted Portrait
    if (screenAngle === 180) {
      return Math.atan2(ax, -ay) * (180.0 / Math.PI);
    }

    // 4. screenAngle === 0 (or undefined):
    // Auto-Rotate might be OFF on Android while user is holding phone horizontally!
    // Check gravity distribution: if |ax| > |ay|, phone is physically held landscape
    if (Math.abs(ax) > Math.abs(ay)) {
      if (ax > 0) {
        // Top of phone is to the left
        return Math.atan2(ay, ax) * (180.0 / Math.PI);
      } else {
        // Top of phone is to the right
        return Math.atan2(-ay, -ax) * (180.0 / Math.PI);
      }
    } else {
      // Physically held in portrait: ay is vertical (+9.8 upwards), ax is horizontal
      return Math.atan2(-ax, ay) * (180.0 / Math.PI);
    }
  }

  // --- Sensor: Gravity Vector (Gimbal-Lock Free) ---
  function onDeviceMotion(e) {
    if (settings.sensor_source !== 'gravity' && !fallbackSensorActive) return;

    const acc = e.accelerationIncludingGravity;
    if (!acc || acc.x === null || acc.y === null) return;
    motionEventsCount++;

    const ax = acc.x;
    const ay = acc.y;

    let angle = 0;
    if (screen.orientation && screen.orientation.angle !== undefined) {
      angle = screen.orientation.angle;
    } else if (window.orientation !== undefined) {
      angle = window.orientation;
    }

    rawSteering = computeTiltAngle(ax, ay, angle);

    if (sensorStatus && motionEventsCount % 20 === 0) {
      sensorStatus.textContent = 'GRAV ⚡';
      sensorStatus.style.color = '#34d399';
    }
  }

  // --- Fallback Sensor: DeviceOrientationEvent ---
  function onDeviceOrientation(e) {
    if (motionEventsCount > 5) return; // Prioritize gravity vector if active
    if (settings.sensor_source !== 'relative' && !fallbackSensorActive) return;
    if (e.gamma === null && e.beta === null) return;
    orientationEventsCount++;

    let angle = 0;
    if (screen.orientation && screen.orientation.angle !== undefined) {
      angle = screen.orientation.angle;
    } else if (window.orientation !== undefined) {
      angle = window.orientation;
    }

    let roll = 0;
    if (angle === 90) {
      roll = -e.beta;
    } else if (angle === -90 || angle === 270) {
      roll = e.beta;
    } else {
      if (Math.abs(e.gamma) > Math.abs(e.beta)) {
        roll = e.gamma;
      } else {
        roll = -e.beta;
      }
    }
    rawSteering = roll;

    if (sensorStatus && orientationEventsCount % 20 === 0) {
      sensorStatus.textContent = 'ORIENT 🧭';
      sensorStatus.style.color = '#38bdf8';
    }
  }

  // --- Sensor: RelativeOrientationSensor (Fallback) ---
  let relativeSensor = null;
  function setupRelativeSensor() {
    if ('RelativeOrientationSensor' in window) {
      try {
        relativeSensor = new window.RelativeOrientationSensor({ frequency: 60 });
        relativeSensor.addEventListener('reading', () => {
          if (motionEventsCount > 5) return; // Prioritize gravity vector if active
          if (settings.sensor_source !== 'relative' && !fallbackSensorActive) return;
          const q = relativeSensor.quaternion;
          if (!q) return;
          const x = q[0], y = q[1], z = q[2], w = q[3];
          const roll = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) * (180.0 / Math.PI);
          rawSteering = roll;
          if (sensorStatus && motionEventsCount === 0) {
            sensorStatus.textContent = 'REL ⚡';
            sensorStatus.style.color = '#34d399';
          }
        });
        relativeSensor.start();
      } catch (err) {
        console.warn('RelativeOrientationSensor error:', err);
      }
    }
  }

  // Request motion permissions if required (e.g. iOS)
  async function requestSensorPermissions() {
    if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
      try {
        const response = await DeviceMotionEvent.requestPermission();
        if (response !== 'granted') {
          console.warn('DeviceMotion permission was not granted.');
        }
      } catch (err) {
        console.warn('Permission request error:', err);
      }
    }
  }

  // --- Multi-Touch Pedal Controls (Pointer Events) ---
  function setupPedalTouch() {
    // Brake pedal events (Left half)
    brakeZone.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      brakeZone.setPointerCapture(e.pointerId);
      touchState.brakePointerId = e.pointerId;
      touchState.brakeStartY = e.clientY;

      vibrate(25);

      if (settings.pedal_mode === 'progressive') {
        // Initial touch: slight initial engagement
        updateBrakeProgressive(e.clientY);
      } else {
        // Binary mode: target full 100%
        touchState.brakeTarget = 1.0;
      }
    });

    brakeZone.addEventListener('pointermove', (e) => {
      if (touchState.brakePointerId === e.pointerId) {
        e.preventDefault();
        if (settings.pedal_mode === 'progressive') {
          updateBrakeProgressive(e.clientY);
        }
      }
    });

    const releaseBrake = (e) => {
      if (touchState.brakePointerId === e.pointerId) {
        e.preventDefault();
        try { brakeZone.releasePointerCapture(e.pointerId); } catch (_) {}
        touchState.brakePointerId = null;
        if (settings.pedal_mode === 'progressive') {
          brake = 0.0;
        } else {
          touchState.brakeTarget = 0.0;
        }
      }
    };
    brakeZone.addEventListener('pointerup', releaseBrake);
    brakeZone.addEventListener('pointercancel', releaseBrake);

    // Throttle pedal events (Right half)
    throttleZone.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      throttleZone.setPointerCapture(e.pointerId);
      touchState.throttlePointerId = e.pointerId;
      touchState.throttleStartY = e.clientY;

      vibrate(25);

      if (settings.pedal_mode === 'progressive') {
        updateThrottleProgressive(e.clientY);
      } else {
        touchState.throttleTarget = 1.0;
      }
    });

    throttleZone.addEventListener('pointermove', (e) => {
      if (touchState.throttlePointerId === e.pointerId) {
        e.preventDefault();
        if (settings.pedal_mode === 'progressive') {
          updateThrottleProgressive(e.clientY);
        }
      }
    });

    const releaseThrottle = (e) => {
      if (touchState.throttlePointerId === e.pointerId) {
        e.preventDefault();
        try { throttleZone.releasePointerCapture(e.pointerId); } catch (_) {}
        touchState.throttlePointerId = null;
        if (settings.pedal_mode === 'progressive') {
          throttle = 0.0;
        } else {
          touchState.throttleTarget = 0.0;
        }
      }
    };
    throttleZone.addEventListener('pointerup', releaseThrottle);
    throttleZone.addEventListener('pointercancel', releaseThrottle);
  }

  function updateBrakeProgressive(clientY) {
    const deltaY = touchState.brakeStartY - clientY;
    const normalized = Math.min(1.0, Math.max(0.0, deltaY / settings.throw_distance));
    // Provide a small baseline (0.15) on direct press so tapping gives immediate partial braking
    brake = Math.max(normalized, 0.15);
  }

  function updateThrottleProgressive(clientY) {
    const deltaY = touchState.throttleStartY - clientY;
    const normalized = Math.min(1.0, Math.max(0.0, deltaY / settings.throw_distance));
    throttle = Math.max(normalized, 0.15);
  }

  // Binary smooth ramp update loop (120ms ramp up/down)
  let lastRampTime = performance.now();
  function updateBinaryRamp() {
    const now = performance.now();
    const dt = (now - lastRampTime) / 1000.0;
    lastRampTime = now;

    if (settings.pedal_mode === 'binary') {
      const rampSpeed = 1.0 / 0.12; // 0 to 1 in 120 ms

      // Brake ramp
      if (brake < touchState.brakeTarget) {
        brake = Math.min(touchState.brakeTarget, brake + rampSpeed * dt);
      } else if (brake > touchState.brakeTarget) {
        brake = Math.max(touchState.brakeTarget, brake - rampSpeed * dt);
      }

      // Throttle ramp
      if (throttle < touchState.throttleTarget) {
        throttle = Math.min(touchState.throttleTarget, throttle + rampSpeed * dt);
      } else if (throttle > touchState.throttleTarget) {
        throttle = Math.max(touchState.throttleTarget, throttle - rampSpeed * dt);
      }
    }
  }

  // --- WebSocket Connection & Streaming ---
  function connectWebSocket() {
    updateStatus('connecting', 'CONNECTING...');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Forward token if present in query parameters
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');
    const wsUrl = `${protocol}//${location.host}/ws${token ? `?token=${encodeURIComponent(token)}` : ''}`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      isConnected = true;
      updateStatus('connected', 'CONNECTED');
      // Start ping loop
      sendPing();
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'pong') {
          const rtt = Math.round(performance.now() - msg.t_send);
          rttValue.textContent = `${rtt} ms`;
          if (rtt < 25) {
            rttValue.style.color = '#34d399';
          } else if (rtt < 50) {
            rttValue.style.color = '#fbbf24';
          } else {
            rttValue.style.color = '#f87171';
          }
          if (startStatusLabel && isConnected) {
            startStatusLabel.textContent = `CONNECTED TO PC (⚡ ${rtt} ms)`;
          }
        } else if (msg.type === 'config') {
          syncConfigFromServer(msg.config);
        } else if (msg.type === 'recenter_ack') {
          vibrate([30, 40, 30]);
        }
      } catch (err) {
        console.error('WS parse error:', err);
      }
    };

    ws.onclose = () => {
      isConnected = false;
      updateStatus('disconnected', 'DISCONNECTED');
      rttValue.textContent = '-- ms';
      // Attempt reconnection after 1.5 seconds
      setTimeout(connectWebSocket, 1500);
    };

    ws.onerror = (err) => {
      console.warn('WS error:', err);
      ws.close();
    };
  }

  function sendPing() {
    if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping', t_send: performance.now() }));
    }
    setTimeout(sendPing, 1000);
  }

  function updateStatus(stateClass, label) {
    if (statusPill) statusPill.className = `status-pill ${stateClass}`;
    if (statusLabel) statusLabel.textContent = label;
    if (startStatusPill) startStatusPill.className = `status-pill ${stateClass}`;
    if (startStatusLabel) {
      if (stateClass === 'connected') {
        startStatusLabel.textContent = 'CONNECTED TO PC (READY)';
      } else if (stateClass === 'connecting') {
        startStatusLabel.textContent = 'CONNECTING TO PC...';
      } else {
        startStatusLabel.textContent = 'OFFLINE — RECONNECTING...';
      }
    }
  }

  // Recenter Wheel
  function triggerRecenter() {
    vibrate(40);
    if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'recenter', angle: rawSteering }));
    }
  }

  // --- High Frequency Input Packet Sender ---
  function sendInputPacket() {
    if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
      const packet = {
        seq: seq++,
        s: Math.round(rawSteering * 100) / 100,
        t: Math.round(throttle * 1000) / 1000,
        b: Math.round(brake * 1000) / 1000,
      };
      ws.send(JSON.stringify(packet));
      packetCount++;
    }
  }

  // --- Animation & Rendering Loop ---
  function renderLoop() {
    updateBinaryRamp();

    // Send packet
    sendInputPacket();

    // Update UI Elements
    // Steering wheel angle display
    steerDegreesText.textContent = `${rawSteering > 0 ? '+' : ''}${rawSteering.toFixed(1)}°`;
    virtualWheel.style.transform = `rotate(${rawSteering}deg)`;

    // Arc gauge calculation
    const maxA = settings.max_angle || 90.0;
    const clampedRatio = Math.max(-1.0, Math.min(1.0, rawSteering / maxA));
    // Total circumference of r=85 is 2 * PI * 85 ≈ 534
    // Fill up to 267 px from top center
    const arcOffset = 534 - Math.abs(clampedRatio) * 267;
    gaugeArc.style.strokeDashoffset = arcOffset;
    gaugeArc.style.stroke = clampedRatio >= 0 ? 'var(--accent-cyan)' : '#38bdf8';

    // Brake HUD
    const brakePct = Math.round(brake * 100);
    brakeValue.textContent = `${brakePct}%`;
    brakeFill.style.height = `${brakePct}%`;
    brakeMeter.style.width = `${brakePct}%`;

    // Throttle HUD
    const throttlePct = Math.round(throttle * 100);
    throttleValue.textContent = `${throttlePct}%`;
    throttleFill.style.height = `${throttlePct}%`;
    throttleMeter.style.width = `${throttlePct}%`;

    // Packet transmission rate (Hz)
    const now = performance.now();
    if (now - lastRateTimestamp >= 1000) {
      rateValue.textContent = packetCount;
      packetCount = 0;
      lastRateTimestamp = now;
    }

    requestAnimationFrame(renderLoop);
  }

  // --- Settings Synchronization ---
  function syncConfigFromServer(cfg) {
    if (!cfg) return;
    if (cfg.max_angle !== undefined) {
      settings.max_angle = cfg.max_angle;
      cfgMaxAngle.value = cfg.max_angle;
      valMaxAngle.textContent = `${cfg.max_angle}°`;
    }
    if (cfg.deadzone !== undefined) {
      settings.deadzone = cfg.deadzone;
      cfgDeadzone.value = cfg.deadzone;
      valDeadzone.textContent = `${(cfg.deadzone * 100).toFixed(1)}%`;
    }
    if (cfg.gamma !== undefined) {
      settings.gamma = cfg.gamma;
      cfgGamma.value = cfg.gamma;
      valGamma.textContent = Number(cfg.gamma).toFixed(2);
    }
    if (cfg.inverse_deadzone !== undefined) {
      settings.inverse_deadzone = cfg.inverse_deadzone;
      cfgInvDeadzone.value = cfg.inverse_deadzone;
      valInvDeadzone.textContent = `${(cfg.inverse_deadzone * 100).toFixed(1)}%`;
    }
    if (cfg.filter_min_cutoff !== undefined) {
      settings.filter_min_cutoff = cfg.filter_min_cutoff;
      cfgFilterCutoff.value = cfg.filter_min_cutoff;
      valFilterCutoff.textContent = `${cfg.filter_min_cutoff} Hz`;
    }
    if (cfg.filter_beta !== undefined) {
      settings.filter_beta = cfg.filter_beta;
      cfgFilterBeta.value = cfg.filter_beta;
      valFilterBeta.textContent = cfg.filter_beta;
    }
    if (cfg.filter_enabled !== undefined) {
      settings.filter_enabled = Boolean(cfg.filter_enabled);
      cfgFilterEnabled.checked = settings.filter_enabled;
    }
  }

  function applySettings() {
    settings.max_angle = parseFloat(cfgMaxAngle.value);
    settings.deadzone = parseFloat(cfgDeadzone.value);
    settings.gamma = parseFloat(cfgGamma.value);
    settings.inverse_deadzone = parseFloat(cfgInvDeadzone.value);
    settings.filter_min_cutoff = parseFloat(cfgFilterCutoff.value);
    settings.filter_beta = parseFloat(cfgFilterBeta.value);
    settings.filter_enabled = cfgFilterEnabled.checked;
    settings.haptics = cfgHaptics.checked;

    const pedalModeRadio = document.querySelector('input[name="pedal-mode"]:checked');
    if (pedalModeRadio) settings.pedal_mode = pedalModeRadio.value;

    const sensorRadio = document.querySelector('input[name="sensor-source"]:checked');
    if (sensorRadio) {
      settings.sensor_source = sensorRadio.value;
      if (settings.sensor_source === 'relative' && !relativeSensor) {
        setupRelativeSensor();
      }
    }

    // Send updated config to server via WebSocket
    if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
      const serverConfig = {
        max_angle: settings.max_angle,
        deadzone: settings.deadzone,
        gamma: settings.gamma,
        inverse_deadzone: settings.inverse_deadzone,
        filter_min_cutoff: settings.filter_min_cutoff,
        filter_beta: settings.filter_beta,
        filter_enabled: settings.filter_enabled,
      };
      ws.send(JSON.stringify({ type: 'config', config: serverConfig }));
    }
  }

  function setupSettingsControls() {
    cfgMaxAngle.addEventListener('input', () => { valMaxAngle.textContent = `${cfgMaxAngle.value}°`; });
    cfgDeadzone.addEventListener('input', () => { valDeadzone.textContent = `${(parseFloat(cfgDeadzone.value) * 100).toFixed(1)}%`; });
    cfgGamma.addEventListener('input', () => { valGamma.textContent = parseFloat(cfgGamma.value).toFixed(2); });
    cfgInvDeadzone.addEventListener('input', () => { valInvDeadzone.textContent = `${(parseFloat(cfgInvDeadzone.value) * 100).toFixed(1)}%`; });
    cfgFilterCutoff.addEventListener('input', () => { valFilterCutoff.textContent = `${cfgFilterCutoff.value} Hz`; });
    cfgFilterBeta.addEventListener('input', () => { valFilterBeta.textContent = cfgFilterBeta.value; });

    settingsBtn.addEventListener('click', () => {
      settingsModal.classList.remove('hidden');
    });

    closeSettingsBtn.addEventListener('click', () => {
      settingsModal.classList.add('hidden');
    });

    saveSettingsBtn.addEventListener('click', () => {
      applySettings();
      settingsModal.classList.add('hidden');
    });

    resetDefaultsBtn.addEventListener('click', () => {
      cfgMaxAngle.value = 90; valMaxAngle.textContent = '90°';
      cfgDeadzone.value = 0.02; valDeadzone.textContent = '2.0%';
      cfgGamma.value = 1.0; valGamma.textContent = '1.00';
      cfgInvDeadzone.value = 0.0; valInvDeadzone.textContent = '0.0%';
      cfgFilterCutoff.value = 1.0; valFilterCutoff.textContent = '1.0 Hz';
      cfgFilterBeta.value = 0.007; valFilterBeta.textContent = '0.007';
      cfgFilterEnabled.checked = true;
      cfgHaptics.checked = true;
      applySettings();
    });
  }

  // --- Initialization & Event Listeners ---
  function init() {
    const isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    if (!isLocal) {
      connectionTipText.textContent = `Wi-Fi Mode (${location.hostname})`;
    } else {
      connectionTipText.textContent = `USB Cable Mode (http://localhost:8000)`;
    }

    // Horn / Test Button (A)
    if (hornBtn) {
      hornBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        vibrate(35);
        if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'button', btn: 'A', action: 'down' }));
        }
      });
      const releaseHorn = (e) => {
        e.preventDefault();
        if (isConnected && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'button', btn: 'A', action: 'up' }));
        }
      };
      hornBtn.addEventListener('pointerup', releaseHorn);
      hornBtn.addEventListener('pointercancel', releaseHorn);
    }

    // Setup controls & immediately connect WebSocket on page load
    setupSettingsControls();
    setupPedalTouch();
    if (recenterBtn) recenterBtn.addEventListener('click', triggerRecenter);
    if (fullscreenBtn) fullscreenBtn.addEventListener('click', requestFullscreen);

    // Auto connect WebSocket immediately
    connectWebSocket();

    startBtn.addEventListener('click', () => {
      vibrate(50);
      try { requestFullscreen().catch(() => {}); } catch (_) {}
      try { requestOrientationLock().catch(() => {}); } catch (_) {}
      try { requestWakeLock().catch(() => {}); } catch (_) {}
      try { requestSensorPermissions().catch(() => {}); } catch (_) {}

      // Listen to both gravity vector motion and Euler orientation
      window.addEventListener('devicemotion', onDeviceMotion, { passive: true });
      window.addEventListener('deviceorientation', onDeviceOrientation, { passive: true });

      setupRelativeSensor();

      if (!isConnected && (!ws || ws.readyState !== WebSocket.OPEN)) {
        connectWebSocket();
      }

      startOverlay.classList.add('hidden');

      // 1-second fallback check: if motion events (devicemotion) not flowing, allow fallback sensors
      setTimeout(() => {
        if (motionEventsCount === 0) {
          fallbackSensorActive = true;
          console.warn('DeviceMotion not received after 1s; enabling fallback sensors');
          if (sensorStatus && orientationEventsCount > 0) {
            sensorStatus.textContent = 'ORIENT 🧭';
            sensorStatus.style.color = '#38bdf8';
          }
        }
      }, 1000);

      requestAnimationFrame(renderLoop);
    });

    // Re-acquire wake lock on visibility change
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && !wakeLock) {
        requestWakeLock();
      }
    });

    // Prevent default context menus and zoom gestures
    document.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  document.addEventListener('DOMContentLoaded', init);
})();
