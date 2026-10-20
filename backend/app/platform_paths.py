"""Platform locations; writable state must never live inside a macOS app bundle."""
from __future__ import annotations

import sys
from pathlib import Path


def is_macos() -> bool:
    return sys.platform == "darwin"


def macos_state_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "FinancialFeeSystem"
