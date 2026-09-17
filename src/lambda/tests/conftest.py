"""Ensure sys.path is set up so tests can import analyzer modules and shared test helpers."""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# src/lambda/ — for importing analyzer modules under test
sys.path.insert(0, str(_HERE.parent))
# src/lambda/tests/ — for importing shared test helpers (e.g. rc4_iac_parsers)
sys.path.insert(0, str(_HERE))
