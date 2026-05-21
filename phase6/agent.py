"""SONiC intent-based agent: read, propose-verify-approve-apply-verify.

Phase 6 of the project. The agent extends Phase 5 with post-apply
verification: after every applied change, the agent re-queries CONFIG_DB
and confirms the predicted CONFIG_DB-level changes actually materialized.
On a clean CONFIG_DB match, an optional Batfish re-read confirms parser
invariants still hold.

Usage:
    python3 agent.py "What IP is configured on Ethernet0?"
    python3 agent.py "Configure Ethernet12 with IP 192.168.1.1/24"

The model name can be overridden with the AGENT_MODEL environment variable.
Default is qwen2.5:7b-instruct.
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ollama import chat

import batfish_client
import diff_renderer
import post_apply_check
import snapshot_builder
import sonic_client
import tools
import verifier
from change_plan import (
    ChangePlan,
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
)
from post_apply_check import (
    POST_APPLY_SUCCESS,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen2.5:7b-instruct"
MAX_TOOL_ROUND_TRIPS = 1
VERIFICATION_TIMEOUT_SECONDS = 60
POST_APPLY_WAIT_TIMEOUT_SECONDS = 2.0
POST_APPLY_POLL_INTERVAL_SECONDS = 0.020

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
    """Ask the user whether to apply the proposed change."""
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


def _verify_plan_safely(plan: ChangePlan) -> verifier.VerificationResult:
    """Run pre-apply verification, translating session failures to unavailable."""
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


def _post_apply_batfish_recheck(plan: ChangePlan) -> str:
    """Run a light post-apply Batfish re-read on the now-changed live state.

    Builds a snapshot of the current SONiC state and parses it through
    Batfish. Returns a single-line status string suitable for appending
    to the rendered post-apply block. Catches all foreseeable failure
    modes (Batfish unreachable, snapshot extraction throws, parse fails)
    and returns a warning string rather than raising.

    Args:
        plan: the just-applied plan. Currently unused but accepted for
            future use (e.g., to scope the re-read to relevant tables).

    Returns:
        A one-line status string. Always non-empty.
    """
    del plan  # currently unused; kept in signature for symmetry
    start = time.monotonic()
    tmp_root = Path(tempfile.mkdtemp(prefix="post_apply_recheck_"))
    try:
        try:
            session = batfish_client.open_session()
        except batfish_client.BatfishClientError as exc:
            elapsed = time.monotonic() - start
            return (
                f"Post-apply Batfish re-read: unavailable "
                f"({elapsed:.2f}s) - {exc}"
            )

        try:
            snapshot_builder.build_current_snapshot(tmp_root)
        except snapshot_builder.SnapshotBuilderError as exc:
            elapsed = time.monotonic() - start
            return (
                f"Post-apply Batfish re-read: snapshot build failed "
                f"({elapsed:.2f}s) - {exc}"
            )

        try:
            batfish_client.init_snapshot(
                session,
                snapshot_dir=str(tmp_root),
                snapshot_name=f"post_apply_recheck_{int(start * 1000)}",
            )
            issues_frame = batfish_client.get_init_issues(session)
        except batfish_client.BatfishClientError as exc:
            elapsed = time.monotonic() - start
            return (
                f"Post-apply Batfish re-read: parse failed "
                f"({elapsed:.2f}s) - {exc}"
            )

        summary = batfish_client.summarize_issues(issues_frame)
        elapsed = time.monotonic() - start
        if summary["critical"]:
            issue_count = len(summary["critical"])
            return (
                f"Post-apply Batfish re-read: {issue_count} CRITICAL "
                f"issue(s) found ({elapsed:.2f}s)"
            )
        return (
            f"Post-apply Batfish re-read: clean parse, no critical "
            f"issues ({elapsed:.2f}s)"
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _post_apply_verify(plan: ChangePlan) -> None:
    """Run post-apply verification and print the result.

    Sequence:
        1. wait_for_settled: poll CONFIG_DB until predicted state is reached
           or a 2-second timeout fires.
        2. check_plan_applied: structural per-prediction verdict on the
           last observed CONFIG_DB.
        3. On POST_APPLY_SUCCESS only: a light Batfish re-read for parser
           invariants. Skipped on partial or complete failure.
        4. Render and print the post-apply block to stdout.

    All exceptions from snapshot_builder propagate up; this function is
    called only on the write-flow branch after a successful apply, where
    crashing on a snapshot extraction failure is appropriate.

    Args:
        plan: the just-applied plan whose predicted_keys define the
            target state.
    """
    print("Running post-apply verification...", file=sys.stderr)

    wait_result = post_apply_check.wait_for_settled(
        plan,
        snapshot_builder._fetch_live_config_db,
        timeout_seconds=POST_APPLY_WAIT_TIMEOUT_SECONDS,
        poll_interval=POST_APPLY_POLL_INTERVAL_SECONDS,
    )
    if not wait_result.settled:
        print(
            f"warning: post-apply state did not settle within "
            f"{POST_APPLY_WAIT_TIMEOUT_SECONDS:.1f}s; "
            f"reporting last observed state",
            file=sys.stderr,
        )

    check_result = post_apply_check.check_plan_applied(
        plan, wait_result.config_db
    )

    total_elapsed = wait_result.elapsed_seconds
    check_result_with_elapsed = post_apply_check.PostApplyResult(
        overall_status=check_result.overall_status,
        verdicts=check_result.verdicts,
        raw_message=check_result.raw_message,
        elapsed_seconds=total_elapsed,
    )

    rendered = diff_renderer.render_post_apply(check_result_with_elapsed)

    if check_result.overall_status == POST_APPLY_SUCCESS:
        recheck_line = _post_apply_batfish_recheck(plan)
        rendered = rendered + "\n  " + recheck_line

    print(rendered)


def answer_question(question: str, model: str) -> str:
    """Run the agent loop and return the final user-facing answer."""
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
        _post_apply_verify(plan)
        return ""

    final_content = response.message.content or ""
    logger.info("final answer: %s", final_content)
    return final_content


def _run_eval_mode(question: str, model: str) -> None:
    """Invoke the LLM once and emit tool calls as JSON to stdout.

    Used by the Phase 7 eval harness to measure LLM tool-call
    accuracy. The function does not execute any tools, does not
    apply any changes, does not run Batfish, and does not prompt
    the user. It only asks the LLM what tool it would call given
    the question, then writes the structured result to stdout as
    JSON.

    Output schema (one JSON object on a single line):
        {
          "tool_calls": [
            {"name": "<tool>", "arguments": { ... }},
            ...
          ],
          "raw_text": "<LLM response.message.content or empty>"
        }

    Args:
        question: the natural-language input.
        model: the Ollama model name.

    Raises:
        RuntimeError: if the LLM call itself fails.
    """
    import json as _json

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    try:
        response = chat(
            model=model, messages=messages, tools=AVAILABLE_TOOLS
        )
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}") from exc

    tool_calls_out: list[dict] = []
    if response.message.tool_calls:
        for tc in response.message.tool_calls:
            tool_calls_out.append(
                {
                    "name": tc.function.name,
                    "arguments": dict(tc.function.arguments),
                }
            )

    payload = {
        "tool_calls": tool_calls_out,
        "raw_text": response.message.content or "",
    }
    print(_json.dumps(payload))


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
    parser.add_argument(
        "--eval-mode", action="store_true",
        help="invoke the LLM once, dump tool calls as JSON to "
        "stdout, and exit. Used by the Phase 7 eval harness. "
        "Does not apply changes, does not run Batfish, does not "
        "prompt for approval.",
    )
    args = parser.parse_args()

    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.eval_mode:
        try:
            _run_eval_mode(args.question, args.model)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

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
