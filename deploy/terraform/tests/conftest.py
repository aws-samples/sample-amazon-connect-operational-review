"""
Shared fixtures for IaC property tests.

This conftest provides module-scoped fixtures for loading file content.
Helper functions live in _helpers.py and are imported directly by test modules
(via a bare ``from _helpers import ...``); we prepend this directory to
``sys.path`` below so those imports resolve whether pytest is invoked from the
repo root or from ``deploy/terraform/``.
"""

import sys
from pathlib import Path

import pytest

# Make _helpers.py importable via `from _helpers import ...` regardless of the
# pytest invocation directory (repo root vs. deploy/terraform/).
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---------------------------------------------------------------------------
# Path Constants
# ---------------------------------------------------------------------------

PARALLEL_TF_PATH = Path(__file__).parent.parent / "parallel.tf"
CFT_PATH = (
    Path(__file__).parent.parent.parent
    / "cloudformation"
    / "CFT-AmazonConnectOperationsReview.yml"
)
AI_ANALYZER_PATH = (
    Path(__file__).parent.parent.parent.parent / "src" / "lambda" / "ai_analyzer.py"
)
RESILIENCE_ANALYZER_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "lambda"
    / "resilience_analyzer.py"
)
ANALYZER_COMMON_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "lambda"
    / "analyzer_common.py"
)
PREPARE_CONTEXT_PATH = (
    Path(__file__).parent.parent.parent.parent / "src" / "lambda" / "prepare_context.py"
)

# ---------------------------------------------------------------------------
# Shared Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parallel_tf_content() -> str:
    """Load the parallel.tf file content."""
    assert PARALLEL_TF_PATH.exists(), f"parallel.tf not found at {PARALLEL_TF_PATH}"
    return PARALLEL_TF_PATH.read_text()


@pytest.fixture(scope="module")
def cft_content() -> str:
    """Load the CloudFormation template content."""
    assert CFT_PATH.exists(), f"CFT not found at {CFT_PATH}"
    return CFT_PATH.read_text()


@pytest.fixture(scope="module")
def ai_analyzer_content() -> str:
    """Load the ai_analyzer.py file content."""
    assert AI_ANALYZER_PATH.exists(), (
        f"ai_analyzer.py not found at {AI_ANALYZER_PATH}"
    )
    return AI_ANALYZER_PATH.read_text()


@pytest.fixture(scope="module")
def resilience_analyzer_content() -> str:
    """Load the resilience_analyzer.py file content."""
    assert RESILIENCE_ANALYZER_PATH.exists(), (
        f"resilience_analyzer.py not found at {RESILIENCE_ANALYZER_PATH}"
    )
    return RESILIENCE_ANALYZER_PATH.read_text()


@pytest.fixture(scope="module")
def analyzer_common_content() -> str:
    """Load the analyzer_common.py file content."""
    assert ANALYZER_COMMON_PATH.exists(), (
        f"analyzer_common.py not found at {ANALYZER_COMMON_PATH}"
    )
    return ANALYZER_COMMON_PATH.read_text()


@pytest.fixture(scope="module")
def prepare_context_content() -> str:
    """Load the prepare_context.py file content."""
    assert PREPARE_CONTEXT_PATH.exists(), (
        f"prepare_context.py not found at {PREPARE_CONTEXT_PATH}"
    )
    return PREPARE_CONTEXT_PATH.read_text()
