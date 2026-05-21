"""Render a ChangePlan as a human-readable diff for approval.

The output has three sections: a one-line logical description of what is
changing, the concrete commands that will run inside the SONiC container,
and the predicted CONFIG_DB key deltas. Per Phase 4 design, both logical
and concrete details are shown so the user can review at the level of
abstraction they prefer.
"""

import logging

from change_plan import ChangePlan

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


def render(plan: ChangePlan) -> str:
    """Render a ChangePlan as multi-section text for user review.

    Args:
        plan: the proposed change to render.

    Returns:
        A multi-line string with three sections: logical description,
        commands that will run, and predicted CONFIG_DB key deltas.
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

    return "\n".join(lines)
