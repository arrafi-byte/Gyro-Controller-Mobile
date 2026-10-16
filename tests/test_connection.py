"""
tests/test_connection.py — Unit tests for Milestone 3 connection helpers.

Verifies:
  1. verify_token handles None, empty string, invalid strings safely (no TypeError).
  2. verify_token validates the actual stored auth token.
  3. find_adb discovers local project platform-tools / adb.exe.
  4. setup_adb_reverse with silent_fail runs safely without unhandled exceptions.
"""

import unittest
from server.connection import (
    find_adb,
    load_or_create_token,
    setup_adb_reverse,
    verify_token,
    ADBWatcher,
)


class TestConnectionHelpers(unittest.TestCase):
    def test_verify_token_null_safe(self):
        """None or empty string must return False without raising TypeError."""
        self.assertFalse(verify_token(None))  # type: ignore
        self.assertFalse(verify_token(""))
        self.assertFalse(verify_token("   "))
        self.assertFalse(verify_token("invalid_token_12345"))

    def test_verify_token_valid(self):
        """Stored token must match successfully."""
        actual = load_or_create_token()
        self.assertTrue(verify_token(actual))
        self.assertTrue(verify_token(f"  {actual}  "))  # strip whitespace

    def test_find_adb_local(self):
        """ADB should be discovered in the project platform-tools folder."""
        adb = find_adb()
        self.assertIsNotNone(adb)
        self.assertTrue(adb.lower().endswith("adb.exe"))

    def test_setup_adb_reverse_silent_fail(self):
        """setup_adb_reverse with silent_fail should not crash."""
        res = setup_adb_reverse(port=8000, silent_fail=True)
        self.assertIsInstance(res, bool)

    def test_adb_watcher_start_stop(self):
        """ADBWatcher starts and stops cleanly."""
        watcher = ADBWatcher(port=8000, check_interval=0.5)
        watcher.start()
        self.assertIsNotNone(watcher._thread)
        self.assertTrue(watcher._thread.is_alive())
        watcher.stop()
        self.assertFalse(watcher._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
