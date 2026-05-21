"""Render a ChangePlan and its verification outcomes for the user.

Two top-level functions:
    render(plan, verification_result=None) -> str
        Phase 4/5: the pre-apply diff shown before the approval prompt.
        Up to four sections: description, commands, predicted CONFIG_DB
        changes, and optionally the pre-apply Batfish verification result.

    render_post_apply(post_apply_result) -> str
        Phase 6: the post-apply verdict block shown after the apply step.
        Reports whether the predicted CONFIG_DB changes actually
        materialized.

The two functions answer different user questions at different points
in the flow, so they are intentionally separate rather than overloading
a single render signature.
"""

import logging

from change_plan import (
    ChangePlan,
    PREDICTED_KEY_ADDED,
    PREDICTED_KEY_MODIFIED,
    PREDICTED_KEY_REMOVED,
)
from post_apply_check import (
    POST_APPLY_COMPLETE_FAILURE,
    POST_APPLY_PARTIAL_FAILURE,
    POST_APPLY_SUCCESS,
    PostApplyResult,
    VERDICT_ABSENT,
    VERDICT_PRESENT,
    VERDICT_UNEXPECTED_VALUE,
)
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
    """Format one command (list of args) as a single shell-style line."""
    return " ".join(command)


def _render_verification(result: VerificationResult) -> list[str]:
    """Render the pre-apply Batfish verification section as text lines."""
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

    Returns:
        A multi-line string with three or four sections.
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


def _format_key_label(table: str, key: str) -> str:
    """Format the table|key label used in per-verdict lines."""
    return f"{table}|{key}"


def _format_verdict_detail(verdict) -> str:
    """Compose the trailing detail string for a non-OK verdict line.

    Args:
        verdict: a KeyVerdict whose verdict label is NOT the expected one.

    Returns:
        A short string like "(absent in CONFIG_DB)" or
        "(expected 'down', got 'up')".
    """
    predicted = verdict.predicted_key
    if predicted.operation == PREDICTED_KEY_ADDED:
        return "(absent in CONFIG_DB)"
    if predicted.operation == PREDICTED_KEY_REMOVED:
        return "(still present in CONFIG_DB)"
    if predicted.operation == PREDICTED_KEY_MODIFIED:
        if verdict.verdict == VERDICT_ABSENT:
            return "(row absent in CONFIG_DB)"
        actual_repr = (
            repr(verdict.actual_value)
            if verdict.actual_value is not None
            else "None"
        )
        return (
            f"(expected {predicted.expected_value!r}, got {actual_repr})"
        )
    return f"(unexpected verdict {verdict.verdict!r})"


def _verdict_is_ok(verdict) -> bool:
    """Return True if a verdict represents the expected outcome."""
    predicted = verdict.predicted_key
    if predicted.operation == PREDICTED_KEY_REMOVED:
        return verdict.verdict == VERDICT_ABSENT
    return verdict.verdict == VERDICT_PRESENT


def render_post_apply(result: PostApplyResult) -> str:
    """Render a PostApplyResult as a multi-line block.

    Args:
        result: the outcome of post-apply verification.

    Returns:
        A multi-line string. Format depends on the overall status:
            success         lowercase one-line summary
            partial failure uppercase header plus per-verdict lines
            complete failure uppercase header plus per-verdict lines
    """
    elapsed = f"({result.elapsed_seconds:.2f}s)"
    lines: list[str] = ["Post-apply verification:"]

    if result.overall_status == POST_APPLY_SUCCESS:
        total = len(result.verdicts)
        if total == 0:
            lines.append(f"  no predicted changes to verify {elapsed}")
        else:
            lines.append(
                f"  all {total} predicted CONFIG_DB change(s) "
                f"verified {elapsed}"
            )
        return "\n".join(lines)

    if result.overall_status == POST_APPLY_PARTIAL_FAILURE:
        matches = sum(1 for v in result.verdicts if _verdict_is_ok(v))
        total = len(result.verdicts)
        lines.append(
            f"  PARTIAL FAILURE: {matches} of {total} predicted "
            f"CONFIG_DB change(s) materialized {elapsed}"
        )
    elif result.overall_status == POST_APPLY_COMPLETE_FAILURE:
        total = len(result.verdicts)
        lines.append(
            f"  COMPLETE FAILURE: none of the {total} predicted "
            f"CONFIG_DB change(s) materialized {elapsed}"
        )
    else:
        lines.append(
            f"  status={result.overall_status!r} {elapsed}"
        )
        if result.raw_message:
            lines.append(f"  {result.raw_message}")
        return "\n".join(lines)

    labels = [
        _format_key_label(v.predicted_key.table, v.predicted_key.key)
        for v in result.verdicts
    ]
    max_label_width = max(len(label) for label in labels) if labels else 0

    for verdict, label in zip(result.verdicts, labels):
        prefix = "OK  " if _verdict_is_ok(verdict) else "FAIL"
        op = verdict.predicted_key.operation
        if _verdict_is_ok(verdict):
            detail = ""
        else:
            detail = f" {_format_verdict_detail(verdict)}"
        padded_label = label.ljust(max_label_width)
        lines.append(f"    {prefix} {op} {padded_label}{detail}")

    return "\n".join(lines)
