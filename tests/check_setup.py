"""
check_setup.py — Pre-flight check for all Milestone 1 dependencies.

Usage:
    python -m tests.check_setup
"""

from __future__ import annotations

import sys
import os

# Force UTF-8 output on Windows
if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def check_python_version() -> bool:
    v = sys.version_info
    ok = v >= (3, 10)
    status = "OK" if ok else "FAIL (need 3.10+)"
    print(f"  Python version: {v.major}.{v.minor}.{v.micro} [{status}]")
    return ok


def check_pyvjoystick() -> bool:
    try:
        import pyvjoystick.vigem  # noqa: F401
        print("  pyvjoystick: installed [OK]")
        return True
    except ImportError:
        print("  pyvjoystick: NOT INSTALLED [FAIL]")
        print("    Fix: pip install pyvjoystick")
        return False


def check_vigembus() -> bool:
    try:
        import pyvjoystick.vigem as vg
        pad = vg.VX360Gamepad()
        pad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        pad.update()
        print("  ViGEmBus driver: working [OK]")
        return True
    except ImportError:
        print("  ViGEmBus driver: skipped (pyvjoystick not installed)")
        return False
    except Exception as e:
        if "BUS_NOT_FOUND" in str(e):
            print("  ViGEmBus driver: NOT INSTALLED [FAIL]")
            print("    Fix: Download from https://github.com/nefarius/ViGEmBus/releases")
            print("    Install v1.22.0 (the final release)")
        else:
            print(f"  ViGEmBus driver: ERROR — {e}")
        return False


def check_fastapi() -> bool:
    try:
        import fastapi  # noqa: F401
        print(f"  FastAPI: installed (v{fastapi.__version__}) [OK]")
        return True
    except ImportError:
        print("  FastAPI: NOT INSTALLED [FAIL]")
        print("    Fix: pip install fastapi uvicorn[standard]")
        return False


def main() -> None:
    print("=" * 50)
    print("  Gyro Racing Wheel - Setup Checker")
    print("=" * 50)
    print()

    checks = [
        ("Python", check_python_version),
        ("pyvjoystick", check_pyvjoystick),
        ("ViGEmBus Driver", check_vigembus),
        ("FastAPI (Milestone 2+)", check_fastapi),
    ]

    results = []
    for name, check_fn in checks:
        print(f"[{name}]")
        results.append(check_fn())
        print()

    print("=" * 50)
    if all(results):
        print("  All checks passed!")
        print("  Run: python -m tests.test_controller")
    else:
        failed = [name for (name, _), ok in zip(checks, results) if not ok]
        print(f"  Some checks failed: {', '.join(failed)}")
        print("  Fix the issues above and re-run this script.")
    print("=" * 50)


if __name__ == "__main__":
    main()
