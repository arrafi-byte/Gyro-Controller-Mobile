"""
hotkey.py — Global Windows hotkey listener for PC recentering.

Allows the user to press a hotkey (default: Ctrl+Alt+C or F9) while in-game
to instantly recenter the steering wheel without touching the phone screen.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# Windows API constants
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# Virtual Key codes
VK_F9 = 0x78
VK_C = 0x43


class GlobalHotkeyListener:
    """Background listener for global Windows hotkeys using RegisterHotKey."""

    def __init__(
        self,
        on_recenter: Callable[[], None],
        modifiers: int = MOD_CONTROL | MOD_ALT,
        vk_code: int = VK_C,
        secondary_vk: int | None = VK_F9,  # F9 as convenient single-key hotkey
    ) -> None:
        self._callback = on_recenter
        self._modifiers = modifiers | MOD_NOREPEAT
        self._vk_code = vk_code
        self._secondary_vk = secondary_vk
        self._running = False
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready_event = threading.Event()

    def start(self) -> None:
        """Start the background hotkey listener thread."""
        if self._running:
            return
        self._running = True
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="global-hotkey-listener",
        )
        self._thread.start()
        self._ready_event.wait(timeout=2.0)
        logger.info("Global hotkey listener started (Ctrl+Alt+C & F9)")

    def stop(self) -> None:
        """Stop the hotkey listener thread cleanly."""
        if not self._running:
            return
        self._running = False
        if self._thread_id:
            user32 = ctypes.windll.user32
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Global hotkey listener stopped")

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._thread_id = kernel32.GetCurrentThreadId()
        registered_ids = []

        # Register primary hotkey (e.g. Ctrl+Alt+C)
        hotkey_id_primary = 1
        res1 = user32.RegisterHotKey(
            None,
            hotkey_id_primary,
            self._modifiers,
            self._vk_code,
        )
        if res1:
            registered_ids.append(hotkey_id_primary)
            logger.info("Registered global hotkey #1: Ctrl+Alt+C")
        else:
            logger.warning("Could not register hotkey Ctrl+Alt+C (code: %s)", ctypes.GetLastError())

        # Register secondary hotkey (e.g. F9 without modifiers, easy during driving)
        if self._secondary_vk is not None:
            hotkey_id_secondary = 2
            res2 = user32.RegisterHotKey(
                None,
                hotkey_id_secondary,
                MOD_NOREPEAT,
                self._secondary_vk,
            )
            if res2:
                registered_ids.append(hotkey_id_secondary)
                logger.info("Registered global hotkey #2: F9")
            else:
                logger.warning("Could not register hotkey F9 (code: %s)", ctypes.GetLastError())

        self._ready_event.set()

        msg = wintypes.MSG()
        try:
            while self._running:
                # GetMessageW blocks until a message is received
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break

                if msg.message == WM_HOTKEY:
                    logger.info("Global recenter hotkey triggered!")
                    try:
                        self._callback()
                    except Exception as e:
                        logger.error("Error in hotkey callback: %s", e)
        finally:
            for hid in registered_ids:
                user32.UnregisterHotKey(None, hid)
