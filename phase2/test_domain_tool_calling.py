"""Phase 2 Test 4: Domain-relevant tool calling.

Defines a stub get_interface_ip tool and asks a network-state question
without naming the tool in the prompt. Verifies that the model chooses
to invoke the tool with the correct interface argument, rather than
fabricating an answer or asking for clarification.

The tool is a stub: it is not actually executed. We only check that the
model proposes a correct call.

Pass criterion: 9 of 10 or better trials produce a tool call with name
"get_interface_ip" and an interface_name argument that resolves to
"Ethernet0" (case-insensitive match).
"""

import os
from dataclasses import dataclass
from typing import Any

from ollama import chat

MODEL_NAME = os.environ.get("PHASE2_MODEL", "qwen2.5:7b-instruct")
TRIAL_COUNT = 10
PASS_THRESHOLD = 9
EXPECTED_TOOL_NAME = "get_interface_ip"
EXPECTED_INTERFACE_VALUE = "ethernet0"  # lowercase for case-insensitive compare

PROMPT = "What IP address is configured on Ethernet0?"

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_interface_ip",
            "description": (
                "Get the IP address configured on a SONiC switch interface. "
                "Use this when the user asks about the IP of a specific "
                "interface on the switch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "interface_name": {
                        "type": "string",
                        "description": (
                            "The name of the interface, e.g. Ethernet0, "
                            "Ethernet4, etc."
                        ),
                    },
                },
                "required": ["interface_name"],
            },
        },
    }
]


@dataclass
class TrialResult:
    """Result of a single domain tool-calling trial."""

    trial_index: int
    success: bool
    tool_name: str | None
    tool_args: dict[str, Any] | None
    raw_content: str
    failure_reason: str | None


def evaluate_response(
    tool_calls: list[Any] | None, content: str
) -> tuple[bool, str | None, str | None, dict[str, Any] | None]:
    """Inspect the model response and determine whether the tool call is correct.

    Args:
        tool_calls: the list under response.message.tool_calls, if any.
        content: the response.message.content field, for logging on failure.

    Returns:
        A four-tuple (success, failure_reason, tool_name, tool_args).
    """
    if not tool_calls:
        return False, "no tool call was made", None, None

    if len(tool_calls) != 1:
        reason = f"expected 1 tool call, got {len(tool_calls)}"
        return False, reason, None, None

    call = tool_calls[0]
    tool_name = call.function.name
    tool_args = dict(call.function.arguments)

    if tool_name != EXPECTED_TOOL_NAME:
        reason = f"wrong tool name: {tool_name!r}"
        return False, reason, tool_name, tool_args

    if "interface_name" not in tool_args:
        return False, "missing arg: interface_name", tool_name, tool_args

    interface_value = tool_args["interface_name"]
    if not isinstance(interface_value, str):
        reason = f"interface_name is not a string: {interface_value!r}"
        return False, reason, tool_name, tool_args

    normalized = interface_value.strip().lower().replace(" ", "")
    if normalized != EXPECTED_INTERFACE_VALUE:
        reason = (
            f"interface_name expected Ethernet0 (any case), "
            f"got {interface_value!r}"
        )
        return False, reason, tool_name, tool_args

    return True, None, tool_name, tool_args


def run_trial(trial_index: int) -> TrialResult:
    """Run a single trial: send the prompt with the tool, evaluate the call.

    Args:
        trial_index: zero-based index for display.

    Returns:
        A TrialResult describing what happened.
    """
    response = chat(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": PROMPT}],
        tools=TOOLS,
        stream=False,
    )
    message = response.message
    success, failure_reason, tool_name, tool_args = evaluate_response(
        message.tool_calls, message.content or ""
    )
    return TrialResult(
        trial_index=trial_index,
        success=success,
        tool_name=tool_name,
        tool_args=tool_args,
        raw_content=message.content or "",
        failure_reason=failure_reason,
    )


def main() -> None:
    """Run TRIAL_COUNT trials and report PASS or FAIL with detail."""
    results: list[TrialResult] = []
    for index in range(TRIAL_COUNT):
        result = run_trial(index)
        results.append(result)
        status = "OK" if result.success else "FAIL"
        print(f"trial {index}: {status}")
        if not result.success:
            print(f"  reason: {result.failure_reason}")
            print(f"  tool_name: {result.tool_name!r}")
            print(f"  tool_args: {result.tool_args!r}")
            if result.raw_content:
                print(f"  content: {result.raw_content!r}")

    success_count = sum(1 for r in results if r.success)
    print()
    print(f"successes: {success_count}/{TRIAL_COUNT} "
          f"(threshold {PASS_THRESHOLD}/{TRIAL_COUNT})")
    if success_count >= PASS_THRESHOLD:
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL")


if __name__ == "__main__":
    main()
