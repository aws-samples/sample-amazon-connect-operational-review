# graceful_timeout.py — time budget pattern for all analyzer Lambdas
"""
Graceful timeout module implementing the internal time budget pattern.

Each analyzer Lambda uses this module to ensure it always has time to
serialize and upload partial results before the Lambda hard timeout fires.
The safety margin (default 60s) reserves time for S3 persistence.
"""

import time

# Maximum configurable time budget in seconds.
# Equals the Lambda hard timeout (900s) minus the first safety margin layer (60s).
# This is the ceiling enforced by PrepareContext on analyzerTimeouts values.
HARD_CEILING = 840

# Safety margin in seconds reserved for serialization and S3 upload.
# Subtracted by compute_time_budget() from the configured timeout.
SAFETY_MARGIN = 60


class TimeBudgetExceeded(Exception):
    """Raised when an analyzer's internal time budget has been exceeded.

    This signals the analyzer to stop data collection and persist whatever
    partial results have been gathered so far.
    """

    pass


def compute_time_budget(
    configured_timeout: int, safety_margin: int = SAFETY_MARGIN
) -> int:
    """Compute the internal time budget for an analyzer.

    Internal time budget = configuredTimeout - safetyMargin.
    The safety margin ensures the function always has time to serialize
    and upload results before the Lambda hard timeout fires.

    Args:
        configured_timeout: The Lambda's configured timeout in seconds.
        safety_margin: Seconds reserved for serialization + S3 upload.
            Must be at least 60 seconds per requirement 15.1.

    Returns:
        Positive integer representing the usable time budget in seconds.

    Raises:
        ValueError: If the resulting budget would not be positive.
    """
    budget = configured_timeout - safety_margin
    if budget <= 0:
        raise ValueError(
            f"Time budget must be positive: configured_timeout={configured_timeout}, "
            f"safety_margin={safety_margin} yields budget={budget}"
        )
    return budget


def check_time_budget(start_time: float, time_budget: int) -> None:
    """Check whether the elapsed time has exceeded the time budget.

    Should be called at the top of each iteration in data collection loops
    to allow graceful exit before the hard timeout.

    Args:
        start_time: The epoch timestamp (from time.time()) when execution began.
        time_budget: The allowed time budget in seconds (from compute_time_budget).

    Raises:
        TimeBudgetExceeded: If elapsed time >= time_budget.
    """
    elapsed = time.time() - start_time
    if elapsed >= time_budget:
        raise TimeBudgetExceeded(
            f"Time budget of {time_budget}s exceeded after {elapsed:.1f}s"
        )
