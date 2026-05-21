"""Phase 2 Test 3: Basic tool calling reliability.

Defines a simple add_numbers tool and verifies that the local model invokes
it correctly when asked an arithmetic question. The tool is not actually
executed; we only check that the model proposes the right call with the
right arguments.

Pass criterion: 10 of 10 attempts produce a tool call with name
"add_numbers" and integer arguments a=47, b=91.
"""

import os
from dataclasses import dataclass
from typing import Any

from ollama import chat

MODEL_NAME = os.environ.get("PHASE2_MODEL", "qwen2.5:7b-instruct")
TRIAL_COUNT = 10
EXPECTED_TOOL_NAME = "add_numbers"
EXPECTED_ARGS = {"a": 47, "b": 91}

PROMPT = "What is 47 plus 91? Use the add_numbers tool to find the answer."

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_numbers",
            "description": "Add two integers and return the sum.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {
                        "type": "integer",
                        "description": "The first integer.",
                    },
                    "b": {
                        "type": "integer",
                        "description": "The second integer.",
                    },
                },
                "required": ["a", "b"],
            },
        },
    }
]


@dataclass
class TrialResult:
    """Result of a single tool-calling trial."""

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
        On success failure_reason is None. On failure tool_name and tool_args
        may still be populated to help diagnose what the model did instead.
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

    for key, expected_value in EXPECTED_ARGS.items():
        if key not in tool_args:
            reason = f"missing arg: {key}"
            return False, reason, tool_name, tool_args
        actual_value = tool_args[key]
        if actual_value != expected_value:
            reason = (
                f"arg {key}: expected {expected_value!r}, "
                f"got {actual_value!r}"
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
    print(f"successes: {success_count}/{TRIAL_COUNT}")
    if success_count == TRIAL_COUNT:
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL")


if __name__ == "__main__":
    main()
