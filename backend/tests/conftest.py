from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="financial-fee-system-tests-"))
os.environ["FINANCIAL_DATA_ROOT"] = str(TEST_DATA_ROOT)
os.environ["FINANCIAL_CODEX_HOME"] = str(TEST_DATA_ROOT / "luna-codex-home")
os.environ["FINANCIAL_TESTING"] = "1"
