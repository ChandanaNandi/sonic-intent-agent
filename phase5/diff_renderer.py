"""Render a ChangePlan as a human-readable diff for approval.

The output has up to four sections: a one-line logical description of
what is changing, the concrete commands that will run inside the SONiC
container, the predicted CONFIG_DB key deltas, and (optionally) the
pre-apply verification result from Batfish.

Per Phase 4 design, both logical and concrete details are shown so the
user can review at the level of abstraction they prefer. Per Phase 5
design, verification results are appended when available; if no
verification was performed (or it is unavailable), the renderer omits
the section or includes a clear unavailable notice.
"""

import logging

from change_plan import ChangePlan
from verifier import (
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    STATUS_WARNINGS,
    VerificationResult,
)

logger = logging.getLogger(__name__)


def _format_command(command: list[str]) -> str:
    """Format one command (list of args) as a single shell-style line.

    Args:
        command: command and arguments, e.g. ["config", "interface",
            "ip", "add", "Ethernet12", "192.168.1.1/24"].

    Returns:
        A single string with the args joined by spaces. This is for
        display only; the actual subprocess call uses the list form.
    """
    return " ".join(command)


def _render_verification(result: VerificationResult) -> list[str]:
    """Render the pre-apply verification section as text lines.

    Args:
        result: the verification outcome.

    Returns:
        A list of strings, one per line (no trailing newlines).
    """
    lines: list[str] = ["Pre-apply verification:"]
    elapsed = f"({result.elapsed_seconds:.1f}s)"
    if result.status == STATUS_OK:
        lines.append(f"  no new issues introduced {elapsed}")
        return lines
    if result.status == STATUS_TIMEOUT:
        lines.append(f"  TIMED OUT {elapsed} - proceed at your own risk")
        return lines
    if result.status == STATUS_UNAVAILABLE:
        lines.append(
            f"  SERVICE UNAVAILABLE {elapsed} - "
            f"Batfish unreachable; verification skipped"
        )
        return lines
    if result.status == STATUS_WARNINGS:
        lines.append(
            f"  WARNINGS {elapsed} - {len(result.new_issues)} new issue(s):"
        )
        for issue in result.new_issues:
            lines.append(f"    - {issue}")
        return lines
    if result.status == STATUS_CRITICAL:
        lines.append(
            f"  CRITICAL {elapsed} - {len(result.new_issues)} new issue(s):"
        )
        for issue in result.new_issues:
            lines.append(f"    ! {issue}")
        return lines
    # Unknown status - render literally so the user sees the truth
    lines.append(f"  status={result.status!r} {elapsed}")
    if result.raw_message:
        lines.append(f"  {result.raw_message}")
    return lines


def render(
    plan: ChangePlan,
    verification_result: VerificationResult | None = None,
) -> str:
    """Render a ChangePlan as multi-section text for user review.

    Args:
        plan: the proposed change to render.
        verification_result: optional pre-apply verification outcome.
            When provided, a fourth section is appended showing the
            Batfish verification result.

    Returns:
        A multi-line string with three or four sections: logical
        description, commands that will run, predicted CONFIG_DB key
        deltas, and (if verification_result is given) the verification
        outcome.
    """
    lines: list[str] = []

    lines.append("Proposed change:")
    lines.append(f"  {plan.description}")
    lines.append("")

    lines.append("Commands that will run:")
    for command in plan.commands:
        lines.append(f"  {_format_command(command)}")
    lines.append("")

    if plan.predicted_config_db_changes:
        lines.append("Predicted CONFIG_DB changes:")
        for change in plan.predicted_config_db_changes:
            lines.append(f"  {change}")
    else:
        lines.append("Predicted CONFIG_DB changes: (none specified)")

    if verification_result is not None:
        lines.append("")
        lines.extend(_render_verification(verification_result))

    return "\n".join(lines)
