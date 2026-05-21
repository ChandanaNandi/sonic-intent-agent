"""Post-apply state verification for ChangePlans.

Given a ChangePlan and a live SONiC CONFIG_DB dict, this module determines
whether each predicted CONFIG_DB-level effect of the plan actually
materialized after the apply. The check is structural and per-prediction:

    - added keys must be present in the table
    - removed keys must be absent from the table
    - modified keys must have the expected value for their field

The module is pure Python in its check function: it takes a dict and a
plan, returns a structured result. No SONiC, no Docker, no Batfish.

The wait_for_settled helper polls a caller-supplied config_db_fetcher
callable until the predicted state is reached or a timeout fires. This
accommodates the 60-80ms SONiC-internal lag between apply returning and
CONFIG_DB reflecting the change (measured in Phase 6 Chunk 1).
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from change_plan import (
    ChangePlan,
    PREDICTED_KEY_ADDED,
    PREDICTED_KEY_MODIFIED,
    PREDICTED_KEY_REMOVED,
    PredictedKey,
)

logger = logging.getLogger(__name__)

POST_APPLY_SUCCESS = "success"
POST_APPLY_PARTIAL_FAILURE = "partial_failure"
POST_APPLY_COMPLETE_FAILURE = "complete_failure"

VERDICT_PRESENT = "present"
VERDICT_ABSENT = "absent"
VERDICT_UNEXPECTED_VALUE = "unexpected_value"

DEFAULT_WAIT_TIMEOUT_SECONDS = 2.0
DEFAULT_WAIT_POLL_INTERVAL_SECONDS = 0.020


@dataclass(frozen=True)
class KeyVerdict:
    """The verdict for a single predicted CONFIG_DB-level effect.

    Attributes:
        predicted_key: the PredictedKey that was checked.
        verdict: VERDICT_PRESENT, VERDICT_ABSENT, or
            VERDICT_UNEXPECTED_VALUE.
        actual_value: for modified keys with VERDICT_UNEXPECTED_VALUE,
            the actual value found in CONFIG_DB. None otherwise.
    """

    predicted_key: PredictedKey
    verdict: str
    actual_value: Optional[str] = None


@dataclass(frozen=True)
class PostApplyResult:
    """The aggregate outcome of checking all predicted keys for a plan.

    Attributes:
        overall_status: POST_APPLY_SUCCESS if every predicted key matched
            expectations, POST_APPLY_PARTIAL_FAILURE if some but not all
            matched, POST_APPLY_COMPLETE_FAILURE if none matched.
        verdicts: per-key verdicts in the same order as plan.predicted_keys.
        raw_message: short human-readable summary.
        elapsed_seconds: total elapsed time including any wait_for_settled
            polling plus the check itself.
    """

    overall_status: str
    verdicts: list[KeyVerdict] = field(default_factory=list)
    raw_message: str = ""
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class WaitResult:
    """The outcome of waiting for CONFIG_DB to settle to the predicted state.

    Attributes:
        config_db: the final CONFIG_DB dict observed when the wait ended,
            either because the predicted state was reached or because the
            timeout fired.
        elapsed_seconds: how long the wait took.
        settled: True if the predicted state was reached before timeout,
            False if the timeout fired with the state still unsettled.
    """

    config_db: dict
    elapsed_seconds: float
    settled: bool


def _expected_verdict(key: PredictedKey) -> str:
    """Return the verdict label that indicates success for a predicted key.

    For added keys, success means the key is present. For removed keys,
    success means absent. For modified keys, success means present (with
    the right value); we check the value separately.
    """
    if key.operation == PREDICTED_KEY_REMOVED:
        return VERDICT_ABSENT
    return VERDICT_PRESENT


def _evaluate_key(
    predicted: PredictedKey, live_config_db: dict
) -> KeyVerdict:
    """Compute the verdict for a single predicted key against live config.

    Args:
        predicted: the PredictedKey to evaluate.
        live_config_db: the live CONFIG_DB dict as returned by
            sonic-cfggen -d --print-data (or a synthetic equivalent).

    Returns:
        A KeyVerdict with the resolved verdict label and (for modified
        keys with a wrong value) the actual value found in CONFIG_DB.
    """
    table = live_config_db.get(predicted.table, {})
    row = table.get(predicted.key)

    if predicted.operation == PREDICTED_KEY_ADDED:
        if row is not None:
            return KeyVerdict(predicted_key=predicted, verdict=VERDICT_PRESENT)
        return KeyVerdict(predicted_key=predicted, verdict=VERDICT_ABSENT)

    if predicted.operation == PREDICTED_KEY_REMOVED:
        if row is None:
            return KeyVerdict(predicted_key=predicted, verdict=VERDICT_ABSENT)
        return KeyVerdict(predicted_key=predicted, verdict=VERDICT_PRESENT)

    if predicted.operation == PREDICTED_KEY_MODIFIED:
        if row is None or not isinstance(row, dict):
            return KeyVerdict(predicted_key=predicted, verdict=VERDICT_ABSENT)
        actual = row.get(predicted.field_name)
        if actual == predicted.expected_value:
            return KeyVerdict(predicted_key=predicted, verdict=VERDICT_PRESENT)
        return KeyVerdict(
            predicted_key=predicted,
            verdict=VERDICT_UNEXPECTED_VALUE,
            actual_value=str(actual) if actual is not None else None,
        )

    raise ValueError(
        f"unknown predicted key operation: {predicted.operation!r}"
    )


def check_plan_applied(
    plan: ChangePlan, live_config_db: dict
) -> PostApplyResult:
    """Evaluate every predicted key in a plan against live CONFIG_DB.

    Args:
        plan: the ChangePlan whose predicted_keys will be checked.
        live_config_db: the live CONFIG_DB dict.

    Returns:
        A PostApplyResult summarizing per-key verdicts and the overall
        status (success, partial_failure, or complete_failure). The
        elapsed_seconds field is 0.0 here; callers can add wait time
        from a prior wait_for_settled call.
    """
    if not plan.predicted_keys:
        return PostApplyResult(
            overall_status=POST_APPLY_SUCCESS,
            verdicts=[],
            raw_message="plan has no predicted keys; nothing to check",
            elapsed_seconds=0.0,
        )

    verdicts: list[KeyVerdict] = []
    matches = 0
    for predicted in plan.predicted_keys:
        verdict = _evaluate_key(predicted, live_config_db)
        verdicts.append(verdict)
        if verdict.verdict == _expected_verdict(predicted):
            matches += 1

    total = len(plan.predicted_keys)
    if matches == total:
        status = POST_APPLY_SUCCESS
        message = (
            f"all {total} predicted CONFIG_DB change(s) verified"
        )
    elif matches == 0:
        status = POST_APPLY_COMPLETE_FAILURE
        message = (
            f"none of the {total} predicted CONFIG_DB change(s) "
            f"materialized"
        )
    else:
        status = POST_APPLY_PARTIAL_FAILURE
        message = (
            f"{matches} of {total} predicted CONFIG_DB change(s) "
            f"materialized; {total - matches} did not"
        )

    return PostApplyResult(
        overall_status=status,
        verdicts=verdicts,
        raw_message=message,
        elapsed_seconds=0.0,
    )


def wait_for_settled(
    plan: ChangePlan,
    config_db_fetcher: Callable[[], dict],
    timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_WAIT_POLL_INTERVAL_SECONDS,
) -> WaitResult:
    """Poll CONFIG_DB until the plan's predicted state is reached or timeout.

    Args:
        plan: the ChangePlan whose predicted_keys define the target state.
        config_db_fetcher: zero-argument callable that returns the current
            CONFIG_DB as a dict. Injected so this function stays pure for
            testing. In production, this is a thin wrapper around
            snapshot_builder._fetch_live_config_db.
        timeout_seconds: hard upper bound on total wait time.
        poll_interval: how long to sleep between polls.

    Returns:
        A WaitResult containing the last observed CONFIG_DB, the elapsed
        time, and a settled flag (True if the predicted state was reached,
        False if the timeout fired).
    """
    if not plan.predicted_keys:
        config_db = config_db_fetcher()
        return WaitResult(
            config_db=config_db, elapsed_seconds=0.0, settled=True
        )

    start = time.monotonic()
    last_config_db: dict = {}
    while True:
        last_config_db = config_db_fetcher()
        result = check_plan_applied(plan, last_config_db)
        if result.overall_status == POST_APPLY_SUCCESS:
            elapsed = time.monotonic() - start
            return WaitResult(
                config_db=last_config_db,
                elapsed_seconds=elapsed,
                settled=True,
            )
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            logger.warning(
                "wait_for_settled: timed out after %.2fs; "
                "predicted state not reached",
                elapsed,
            )
            return WaitResult(
                config_db=last_config_db,
                elapsed_seconds=elapsed,
                settled=False,
            )
        time.sleep(poll_interval)
