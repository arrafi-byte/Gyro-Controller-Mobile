"""
connection.py — Milestone 3: ADB Reverse & Wi-Fi connection helpers.

Provides:
  - ADB reverse setup automation
  - QR code generation with auth token for Wi-Fi path
  - mkcert HTTPS certificate support
  - Windows Firewall rule guidance
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Auth Token                                                         #
# ------------------------------------------------------------------ #

TOKEN_FILE = Path(__file__).parent.parent / ".auth_token"


def generate_token() -> str:
    """Generate a secure random auth token and save it to disk."""
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    logger.info(f"Auth token generated and saved to {TOKEN_FILE}")
    return token


def load_or_create_token() -> str:
    """Load existing token or create a new one."""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    return generate_token()


def verify_token(provided: str | None) -> bool:
    """Verify that the provided token matches the stored one."""
    if not provided or not isinstance(provided, str):
        return False
    stored = load_or_create_token()
    return secrets.compare_digest(provided.strip(), stored)


# ------------------------------------------------------------------ #
#  ADB Reverse                                                       #
# ------------------------------------------------------------------ #

def find_adb() -> str | None:
    """Find the adb executable in PATH or common locations."""
    # Check PATH first
    adb = shutil.which("adb")
    if adb:
        return adb

    # Project root & local directories
    project_root = Path(__file__).parent.parent
    local_candidates = [
        project_root / "platform-tools" / "adb.exe",
        project_root / "platform-tools-latest-windows" / "platform-tools" / "adb.exe",
        project_root / "adb.exe",
        Path.cwd() / "platform-tools" / "adb.exe",
        Path.cwd() / "platform-tools-latest-windows" / "platform-tools" / "adb.exe",
        Path.cwd() / "adb.exe",
    ]
    for path in local_candidates:
        if path.exists():
            return str(path.resolve())

    # Common Windows locations & drive roots
    candidates = [
        Path("D:/platform-tools-latest-windows/platform-tools/adb.exe"),
        Path("D:/platform-tools/adb.exe"),
        Path("C:/platform-tools-latest-windows/platform-tools/adb.exe"),
        Path("C:/platform-tools/adb.exe"),
        Path(os.environ.get("USERPROFILE", "")) / "Downloads" / "platform-tools" / "adb.exe",
        Path(os.environ.get("USERPROFILE", "")) / "Downloads" / "platform-tools-latest-windows" / "platform-tools" / "adb.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path("C:/") / "Android" / "sdk" / "platform-tools" / "adb.exe",
        Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path.resolve())
    return None


def setup_adb_reverse(port: int = 8000, silent_fail: bool = False) -> bool:
    """Run `adb reverse tcp:<port> tcp:<port>`.

    Returns True if successful, False otherwise.
    """
    adb = find_adb()
    if not adb:
        if silent_fail:
            logger.info("ADB not found (skipping automatic USB reverse port forwarding)")
        else:
            logger.info(
                "ADB not found! To use USB connection, install Android SDK Platform Tools:\n"
                "  https://developer.android.com/tools/releases/platform-tools\n"
                "  Or add adb.exe to your PATH"
            )
        return False

    try:
        # Check if a device is connected
        result = subprocess.run(
            [adb, "devices"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.strip().split("\n")[1:] if l.strip()]
        devices = [l for l in lines if "device" in l and "offline" not in l]

        if not devices:
            if silent_fail:
                logger.info("No Android device connected via USB (skipping ADB reverse)")
            else:
                logger.info(
                    "No Android device connected via USB.\n"
                    "  1. Enable USB Debugging on your phone\n"
                    "  2. Connect your phone via USB cable\n"
                    "  3. Accept the USB debugging prompt on your phone"
                )
            return False

        logger.info(f"Found {len(devices)} USB device(s): {devices}")

        # Setup reverse port forwarding
        result = subprocess.run(
            [adb, "reverse", f"tcp:{port}", f"tcp:{port}"],
            capture_output=True, text=True, timeout=5
        )

        if result.returncode == 0:
            logger.info(f"ADB reverse port forwarding active: tcp:{port} -> tcp:{port}")
            logger.info(f"Open http://localhost:{port} in Chrome on your phone")
            return True
        else:
            logger.warning(f"ADB reverse returned non-zero: {result.stderr.strip()}")
            return False

    except FileNotFoundError:
        logger.info(f"ADB executable not found at: {adb}")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("ADB command timed out")
        return False


class ADBWatcher:
    """Continuously monitors for connected Android devices in the background
    and automatically executes `adb reverse tcp:<port> tcp:<port>` whenever
    a USB device is plugged in or reconnects.

    This ensures true plug-and-play: the user can launch the server FIRST,
    and plug the USB cable in at ANY TIME afterwards, without restarting the server.
    """

    def __init__(self, port: int = 8000, check_interval: float = 2.0):
        self.port = port
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_devices: set[str] = set()

    def start(self) -> None:
        """Start the background ADB watcher thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ADBWatcherThread")
        self._thread.start()
        logger.debug("ADBWatcher background service started (interval: %.1fs)", self.check_interval)

    def stop(self) -> None:
        """Stop the background ADB watcher thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.debug("ADBWatcher stopped")

    def _run(self) -> None:
        adb = find_adb()
        if not adb:
            return

        while not self._stop_event.is_set():
            try:
                result = subprocess.run(
                    [adb, "devices"],
                    capture_output=True, text=True, timeout=3
                )
                lines = [l.strip() for l in result.stdout.strip().split("\n")[1:] if l.strip()]
                current_devices = set(
                    l.split("\t")[0].strip() for l in lines if "\tdevice" in l
                )

                new_devices = current_devices - self._active_devices
                if new_devices:
                    # Execute reverse for newly connected device(s)
                    rev_result = subprocess.run(
                        [adb, "reverse", f"tcp:{self.port}", f"tcp:{self.port}"],
                        capture_output=True, text=True, timeout=3
                    )
                    if rev_result.returncode == 0:
                        logger.info(
                            f"USB Hotplug detected: {list(new_devices)} -> ADB reverse active! "
                            f"(Open http://localhost:{self.port} in Chrome on phone)"
                        )
                    self._active_devices = current_devices
                elif not current_devices and self._active_devices:
                    logger.info("USB device disconnected")
                    self._active_devices = set()

            except Exception as e:
                logger.debug(f"ADBWatcher poll error: {e}")

            # Sleep in small slices for instant responsiveness on shutdown
            steps = max(1, int(self.check_interval / 0.2))
            for _ in range(steps):
                if self._stop_event.is_set():
                    break
                time.sleep(0.2)


# ------------------------------------------------------------------ #
#  QR Code (Wi-Fi path with token)                                   #
# ------------------------------------------------------------------ #

def get_local_ip() -> str:
    """Get the local IP address of this machine."""
    import socket
    try:
        # Create a UDP socket to determine our local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def generate_qr_code(port: int = 8000, use_https: bool = False) -> str:
    """Generate a QR code with the connection URL and auth token.

    Prints the QR code to the terminal and returns the URL.
    """
    token = load_or_create_token()
    ip = get_local_ip()
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{ip}:{port}/?token={token}"

    try:
        import qrcode
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Print to terminal (handle Windows encoding)
        f = io.StringIO()
        qr.print_ascii(out=f, invert=True)
        qr_text = f.getvalue()

        output = "\n" + "=" * 50 + "\n"
        output += "  Scan this QR code with your phone:\n"
        output += "=" * 50 + "\n"
        output += qr_text + "\n"
        output += f"  URL: {url}\n"
        output += "=" * 50 + "\n"

        try:
            sys.stdout.buffer.write(output.encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            # Fallback: just print the URL
            print(f"\n  URL: {url}\n")

    except ImportError:
        print(f"\n  QR code library not available.")
        print(f"  Open this URL on your phone: {url}\n")

    return url


# ------------------------------------------------------------------ #
#  mkcert HTTPS                                                      #
# ------------------------------------------------------------------ #

CERT_DIR = Path(__file__).parent.parent / "certs"


def setup_mkcert(port: int = 8000) -> tuple[str, str] | None:
    """Generate HTTPS certificates using mkcert.

    Returns (cert_path, key_path) or None if mkcert is not available.
    """
    mkcert = shutil.which("mkcert")
    if not mkcert:
        logger.warning(
            "mkcert not found. Install it from:\n"
            "  https://github.com/FiloSottile/mkcert/releases\n"
            "  Then run: mkcert -install"
        )
        return None

    ip = get_local_ip()
    CERT_DIR.mkdir(exist_ok=True)
    cert_file = CERT_DIR / "cert.pem"
    key_file = CERT_DIR / "key.pem"

    try:
        # Install root CA if not done yet
        subprocess.run([mkcert, "-install"], capture_output=True, timeout=30)

        # Generate cert for localhost + LAN IP
        result = subprocess.run(
            [mkcert, "-cert-file", str(cert_file), "-key-file", str(key_file),
             "localhost", "127.0.0.1", ip],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            logger.info(f"Certificates generated for localhost, 127.0.0.1, {ip}")
            logger.info(f"  Cert: {cert_file}")
            logger.info(f"  Key:  {key_file}")
            return str(cert_file), str(key_file)
        else:
            logger.error(f"mkcert failed: {result.stderr}")
            return None

    except Exception as e:
        logger.error(f"mkcert error: {e}")
        return None


# ------------------------------------------------------------------ #
#  Firewall guidance                                                  #
# ------------------------------------------------------------------ #

FIREWALL_INSTRUCTIONS = """
================================================================================
  Windows Firewall Setup for Gyro Racing Wheel
================================================================================

  To allow your Android phone to connect via Wi-Fi, you need to allow
  inbound connections on port 8000.

  OPTION 1: PowerShell (Run as Administrator)
  -------------------------------------------
  New-NetFirewallRule -DisplayName "Gyro Racing Wheel" `
    -Direction Inbound -Protocol TCP -LocalPort 8000 `
    -Action Allow -Profile Private

  OPTION 2: Windows Firewall GUI
  ------------------------------
  1. Open "Windows Defender Firewall with Advanced Security"
  2. Click "Inbound Rules" -> "New Rule..."
  3. Select "Port" -> Next
  4. Select "TCP", enter "8000" -> Next
  5. Select "Allow the connection" -> Next
  6. Check only "Private" -> Next
  7. Name it "Gyro Racing Wheel" -> Finish

  SECURITY NOTE:
  - Only enable for "Private" network profile
  - The server uses token-based authentication for Wi-Fi connections
  - USB/ADB connections (localhost) don't need firewall rules

================================================================================
"""


def print_firewall_instructions():
    """Print Windows Firewall setup instructions."""
    print(FIREWALL_INSTRUCTIONS)


def check_firewall_rule() -> bool:
    """Check if the firewall rule already exists."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-NetFirewallRule -DisplayName 'Gyro Racing Wheel' -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=10
        )
        return "Gyro Racing Wheel" in result.stdout
    except Exception:
        return False


# ------------------------------------------------------------------ #
#  CLI entry point                                                    #
# ------------------------------------------------------------------ #

def main():
    """CLI tool for connection setup."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Gyro Racing Wheel — Connection Setup")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("adb", help="Setup ADB reverse port forwarding")
    sub.add_parser("qr", help="Generate QR code for Wi-Fi connection")
    sub.add_parser("cert", help="Generate HTTPS certificates with mkcert")
    sub.add_parser("firewall", help="Show Windows Firewall setup instructions")
    sub.add_parser("status", help="Check connection readiness")

    args = parser.parse_args()

    if args.command == "adb":
        success = setup_adb_reverse(args.port)
        sys.exit(0 if success else 1)

    elif args.command == "qr":
        generate_qr_code(args.port)

    elif args.command == "cert":
        result = setup_mkcert(args.port)
        if result:
            print(f"Certificates ready: {result}")
        else:
            sys.exit(1)

    elif args.command == "firewall":
        print_firewall_instructions()
        if check_firewall_rule():
            print("  [OK] Firewall rule 'Gyro Racing Wheel' already exists.")
        else:
            print("  [!] Firewall rule not found. Follow the instructions above.")

    elif args.command == "status":
        print("Checking connection readiness...\n")
        ip = get_local_ip()
        print(f"  Local IP: {ip}")
        print(f"  Port: {args.port}")
        print(f"  ADB: {'found' if find_adb() else 'NOT FOUND'}")
        print(f"  mkcert: {'found' if shutil.which('mkcert') else 'NOT FOUND'}")
        print(f"  Firewall rule: {'OK' if check_firewall_rule() else 'NOT SET'}")
        print(f"  Auth token: {'exists' if TOKEN_FILE.exists() else 'will be generated'}")
        print()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
