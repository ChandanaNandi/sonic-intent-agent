"""Verification orchestrator: run a ChangePlan through Batfish.

Builds two Batfish snapshots (current SONiC state and candidate state
with the proposed change applied), submits both, and computes the diff
of init issues. Returns a structured VerificationResult that the agent
can include in the diff shown to the user.

The result distinguishes five outcomes:
    ok           - candidate parses, no new issues vs current
    warnings     - candidate has new non-critical issues
    critical     - candidate has new critical errors
    timeout      - verification exceeded the time budget
    unavailable  - Batfish service is unreachable

This module does not catch unexpected exceptions silently; only the
expected failure modes (timeout, transport failure) are caught and
translated to result statuses. Programmer errors (e.g., bad plan
structure) propagate as exceptions.

Limitation: the timeout uses signal.SIGALRM and so only fires when
verify_plan is called from the main thread. The CLI agent always calls
from the main thread, so this is acceptable for the current scope.
"""

import logging
import shutil
import signal
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pybatfish.client.session import Session

import batfish_client
import snapshot_builder
from change_plan import ChangePlan

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60

STATUS_OK = "ok"
STATUS_WARNINGS = "warnings"
STATUS_CRITICAL = "critical"
STATUS_TIMEOUT = "timeout"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of running a ChangePlan through Batfish verification.

    Attributes:
        status: one of STATUS_OK, STATUS_WARNINGS, STATUS_CRITICAL,
            STATUS_TIMEOUT, STATUS_UNAVAILABLE.
        new_issues: list of issue descriptions present in the candidate
            snapshot but not the current snapshot.
        raw_message: a short human-readable description of the outcome.
        elapsed_seconds: how long verification took, end to end.
    """

    status: str
    new_issues: list[str] = field(default_factory=list)
    raw_message: str = ""
    elapsed_seconds: float = 0.0


class _TimeoutSentinel(Exception):
    """Internal: signal-handler raises this when SIGALRM fires."""


def _timeout_handler(signum: int, frame) -> None:
    """Signal handler that converts SIGALRM into a Python exception."""
    raise _TimeoutSentinel("verification deadline exceeded")


def _looks_like_unreachable(exc: BaseException) -> bool:
    """Heuristic: does this exception suggest the Batfish service is down?

    Pybatfish raises various exception types when the service is
    unreachable. We do not have a clean way to distinguish them from
    "service up but query failed" errors. This heuristic looks at the
    exception chain for known transport-level patterns.
    """
    candidates = [exc]
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        candidates.append(cause)

    for candidate in candidates:
        if isinstance(candidate, (ConnectionError, OSError)):
            return True
        message = str(candidate).lower()
        if "connection" in message and (
            "refused" in message or "reset" in message
            or "aborted" in message
        ):
            return True
        if "could not connect" in message:
            return True
    return False


def _issue_strings(session: Session) -> list[str]:
    """Return issue strings for the snapshot currently loaded in session.

    Uses summarize_issues to produce a stable, comparable representation
    of each issue. Returns critical and warning entries concatenated;
    the verifier separates them later by re-checking severity.
    """
    issues_frame = batfish_client.get_init_issues(session)
    summary = batfish_client.summarize_issues(issues_frame)
    return list(summary["critical"]) + list(summary["warnings"])


def _critical_strings(session: Session) -> set[str]:
    """Return only the critical issue strings for the current snapshot."""
    issues_frame = batfish_client.get_init_issues(session)
    summary = batfish_client.summarize_issues(issues_frame)
    return set(summary["critical"])


def _verify_inner(
    plan: ChangePlan, session: Session, tmp_root: Path
) -> VerificationResult:
    """Core verification logic, without timeout or exception translation.

    Builds current and candidate snapshots in tmp_root, submits both to
    Batfish, computes the new-issue diff, and returns a result.
    """
    start = time.monotonic()
    unique_id = uuid.uuid4().hex[:8]
    current_root = tmp_root / "current"
    candidate_root = tmp_root / "candidate"
    current_name = f"verify_current_{unique_id}"
    candidate_name = f"verify_candidate_{unique_id}"

    snapshot_builder.build_current_snapshot(current_root)
    snapshot_builder.build_candidate_snapshot(candidate_root, plan)

    batfish_client.init_snapshot(
        session, snapshot_dir=str(current_root), snapshot_name=current_name
    )
    current_issues = set(_issue_strings(session))
    current_critical = _critical_strings(session)

    batfish_client.init_snapshot(
        session,
        snapshot_dir=str(candidate_root),
        snapshot_name=candidate_name,
    )
    candidate_issues = set(_issue_strings(session))
    candidate_critical = _critical_strings(session)

    new_issues_set = candidate_issues - current_issues
    new_critical_set = candidate_critical - current_critical
    elapsed = time.monotonic() - start

    new_issues = sorted(new_issues_set)
    if new_critical_set:
        return VerificationResult(
            status=STATUS_CRITICAL,
            new_issues=new_issues,
            raw_message=(
                f"Batfish reported {len(new_critical_set)} new critical "
                f"issue(s) introduced by this change"
            ),
            elapsed_seconds=elapsed,
        )
    if new_issues:
        return VerificationResult(
            status=STATUS_WARNINGS,
            new_issues=new_issues,
            raw_message=(
                f"Batfish reported {len(new_issues)} new warning(s) "
                f"introduced by this change"
            ),
            elapsed_seconds=elapsed,
        )
    return VerificationResult(
        status=STATUS_OK,
        new_issues=[],
        raw_message="Batfish reports no new issues introduced by this change",
        elapsed_seconds=elapsed,
    )


def verify_plan(
    plan: ChangePlan,
    session: Session,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> VerificationResult:
    """Verify a ChangePlan through Batfish and return the outcome.

    Args:
        plan: the proposed change to verify.
        session: an open pybatfish Session. Caller is responsible for
            having previously confirmed the Batfish service is reachable
            (e.g., via batfish_client.open_session()).
        timeout_seconds: hard upper bound on verification time. On
            timeout, returns a VerificationResult with status=timeout
            rather than raising.

    Returns:
        A VerificationResult. The verifier never raises for expected
        failure modes (timeout, unreachable Batfish); those are
        reported via the status field.
    """
    start = time.monotonic()
    tmp_root = Path(tempfile.mkdtemp(prefix="verify_"))
    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_seconds)
    try:
        return _verify_inner(plan, session, tmp_root)
    except _TimeoutSentinel:
        elapsed = time.monotonic() - start
        logger.warning("verification timed out after %.1fs", elapsed)
        return VerificationResult(
            status=STATUS_TIMEOUT,
            new_issues=[],
            raw_message=(
                f"Verification timed out after {timeout_seconds}s; "
                f"proceed at your own risk"
            ),
            elapsed_seconds=elapsed,
        )
    except batfish_client.BatfishClientError as exc:
        elapsed = time.monotonic() - start
        if _looks_like_unreachable(exc):
            logger.warning("Batfish unreachable: %s", exc)
            return VerificationResult(
                status=STATUS_UNAVAILABLE,
                new_issues=[],
                raw_message=(
                    f"Batfish service unavailable; verification skipped: "
                    f"{exc}"
                ),
                elapsed_seconds=elapsed,
            )
        logger.warning("Batfish reported a critical failure: %s", exc)
        return VerificationResult(
            status=STATUS_CRITICAL,
            new_issues=[],
            raw_message=f"Batfish verification failed: {exc}",
            elapsed_seconds=elapsed,
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        shutil.rmtree(tmp_root, ignore_errors=True)
