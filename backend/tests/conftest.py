from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("APP_ENV", "test")

import pytest  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden_cases"


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return GOLDEN_DIR
