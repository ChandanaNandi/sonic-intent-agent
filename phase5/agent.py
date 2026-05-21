"""SONiC intent-based agent: read, propose-verify-approve-apply.

Phase 5 of the project. The agent extends Phase 4 with pre-apply
verification: every proposed change is run through Batfish (or treated
as "verification unavailable" if Batfish is unreachable) and the result
is included in the diff shown to the user before approval.

Usage:
    python3 agent.py "What IP is configured on Ethernet0?"
    python3 agent.py "Configure Ethernet12 with IP 192.168.1.1/24"

The model name can be overridden with the AGENT_MODEL environment variable.
Default is qwen2.5:7b-instruct.
"""

import argparse
import logging
import os
import sys
import time

from ollama import chat

import batfish_client
import diff_renderer
import sonic_client
import tools
import verifier
from change_plan import (
    ChangePlan,
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:7b-instruct"
MAX_TOOL_ROUND_TRIPS = 1
VERIFICATION_TIMEOUT_SECONDS = 60

SYSTEM_PROMPT = (
    "You are an agent that manages a SONiC network switch. "
    "Use the read tools to answer questions about live state. "
    "Use the propose_ tools when the user asks to configure, change, "
    "add, remove, or modify something on the switch. "
    "Never invent data. If a tool returns an error, tell the user honestly. "
    "Keep your answers short and direct."
)

AVAILABLE_TOOLS = [
    tools.get_interface_ip,
    tools.list_configured_interfaces,
    tools.get_bgp_status,
    tools.propose_add_interface_ip,
    tools.propose_remove_interface_ip,
    tools.propose_set_interface_admin_status,
]


def _execute_tool_call(tool_call) -> str:
    """Look up the tool by name and call it with the provided arguments."""
    tool_name = tool_call.function.name
    tool_args = dict(tool_call.function.arguments)
    logger.info("tool call: %s(%s)", tool_name, tool_args)

    tool_function = None
    for candidate in AVAILABLE_TOOLS:
        if candidate.__name__ == tool_name:
            tool_function = candidate
            break

    if tool_function is None:
        result = f"error: unknown tool {tool_name!r}"
        logger.warning(result)
        return result

    try:
        result = tool_function(**tool_args)
    except TypeError as exc:
        result = f"error: bad arguments to {tool_name}: {exc}"
        logger.warning(result)
        return result

    logger.info("tool result: %s", result)
    return result


def _prompt_for_approval() -> bool:
    """Ask the user whether to apply the proposed change.

    Reads a line from stdin. Returns True only if the response is "y" or
    "yes" (case-insensitive). Any other input, including EOF, is rejection.
    """
    try:
        response = input("Approve this change? [y/N]: ")
    except EOFError:
        print("(stdin closed; treating as rejection)", file=sys.stderr)
        return False
    return response.strip().lower() in ("y", "yes")


def _apply_plan(plan: ChangePlan) -> None:
    """Dispatch a ChangePlan to the appropriate sonic_client apply function."""
    if plan.operation == OPERATION_ADD_IP:
        sonic_client.apply_add_interface_ip(
            plan.target, plan.parameters["ip_address"]
        )
    elif plan.operation == OPERATION_REMOVE_IP:
        sonic_client.apply_remove_interface_ip(
            plan.target, plan.parameters["ip_address"]
        )
    elif plan.operation == OPERATION_SET_ADMIN:
        sonic_client.apply_set_interface_admin_status(
            plan.target, plan.parameters["admin_status"]
        )
    else:
        raise ValueError(f"unknown operation {plan.operation!r}")


def _report_post_apply_state(plan: ChangePlan) -> None:
    """Re-query CONFIG_DB after apply and print what is now configured."""
    try:
        current_ip = sonic_client.get_interface_ip(plan.target)
    except sonic_client.SonicClientError as exc:
        print(
            f"warning: could not verify post-apply state: {exc}",
            file=sys.stderr,
        )
        return
    if current_ip is None:
        print(f"Post-apply state: {plan.target} has no IP configured")
    else:
        print(f"Post-apply state: {plan.target} has IP {current_ip}")


def _verify_plan_safely(plan: ChangePlan) -> verifier.VerificationResult:
    """Run pre-apply verification, translating session failures to unavailable.

    Args:
        plan: the proposed change.

    Returns:
        A VerificationResult. Never raises; if Batfish session creation
        fails the result has status=unavailable.
    """
    session_start = time.monotonic()
    try:
        session = batfish_client.open_session()
    except batfish_client.BatfishClientError as exc:
        elapsed = time.monotonic() - session_start
        logger.warning("Batfish session not available: %s", exc)
        return verifier.VerificationResult(
            status=verifier.STATUS_UNAVAILABLE,
            new_issues=[],
            raw_message=(
                f"Batfish session could not be opened; "
                f"verification skipped: {exc}"
            ),
            elapsed_seconds=elapsed,
        )
    return verifier.verify_plan(
        plan, session, timeout_seconds=VERIFICATION_TIMEOUT_SECONDS
    )


def answer_question(question: str, model: str) -> str:
    """Run the agent loop and return the final user-facing answer.

    Read-only queries behave like Phase 3 (LLM tool call cycle, then
    final answer). Write requests run through propose -> verify ->
    diff -> approve -> apply.

    Args:
        question: the natural-language question or request.
        model: the Ollama model name.

    Returns:
        The final user-facing answer. May be empty if the agent only
        performed a write flow with its own console output.

    Raises:
        RuntimeError: if the LLM call itself fails.
    """
    tools.proposed_plans.clear()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    logger.info("question: %s", question)

    try:
        response = chat(model=model, messages=messages, tools=AVAILABLE_TOOLS)
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc

    messages.append(response.message)

    round_trips = 0
    while response.message.tool_calls and round_trips < MAX_TOOL_ROUND_TRIPS:
        round_trips += 1
        for tool_call in response.message.tool_calls:
            result = _execute_tool_call(tool_call)
            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_call.function.name,
                    "content": result,
                }
            )

        try:
            response = chat(
                model=model, messages=messages, tools=AVAILABLE_TOOLS
            )
        except Exception as exc:
            raise RuntimeError(
                f"LLM follow-up call failed after tool execution: {exc}"
            ) from exc
        messages.append(response.message)

    if tools.proposed_plans:
        if len(tools.proposed_plans) > 1:
            print(
                f"warning: {len(tools.proposed_plans)} plans proposed; "
                f"only the first will be considered",
                file=sys.stderr,
            )
        plan = tools.proposed_plans[0]

        print("Running pre-apply verification...", file=sys.stderr)
        verification = _verify_plan_safely(plan)
        if verification.status != verifier.STATUS_OK:
            print(
                f"verification status: {verification.status}: "
                f"{verification.raw_message}",
                file=sys.stderr,
            )

        print(diff_renderer.render(plan, verification))
        print()
        approved = _prompt_for_approval()
        if not approved:
            print("Change rejected. No modifications made.")
            return ""
        try:
            _apply_plan(plan)
        except (sonic_client.SonicClientError, ValueError) as exc:
            print(f"error: apply failed: {exc}", file=sys.stderr)
            return ""
        print("Change applied.")
        _report_post_apply_state(plan)
        return ""

    final_content = response.message.content or ""
    logger.info("final answer: %s", final_content)
    return final_content


def main() -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        description=(
            "Query or modify the SONiC switch with a natural-language "
            "request."
        )
    )
    parser.add_argument(
        "question", help="the question or request to send, in quotes"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="show tool calls and intermediate steps on stderr",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENT_MODEL", DEFAULT_MODEL),
        help=f"Ollama model name (default: {DEFAULT_MODEL}, "
        f"or AGENT_MODEL env var)",
    )
    args = parser.parse_args()

    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        answer = answer_question(args.question, args.model)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if answer:
        print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
