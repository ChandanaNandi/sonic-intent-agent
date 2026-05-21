"""Phase 2 Test 2: Structured JSON output reliability.

Sends the same JSON-output prompt to the local model multiple times and
checks how many responses parse as valid JSON with the expected schema.
Uses Ollama's format="json" parameter which constrains the model's output
at the token-generation level so it cannot produce non-JSON tokens.

Pass criterion: 10 of 10 attempts produce valid, schema-correct JSON.
"""

import json
import os
from dataclasses import dataclass

from ollama import chat

MODEL_NAME = os.environ.get("PHASE2_MODEL", "qwen2.5:7b-instruct")
TRIAL_COUNT = 10
EXPECTED_KEYS = ("interface", "ip_address")

PROMPT = (
    "Return a JSON object with the keys 'interface' and 'ip_address'. "
    "The interface is Ethernet0 and the IP is 10.0.0.1/24."
)


@dataclass
class TrialResult:
    """Result of a single JSON-output trial."""

    trial_index: int
    success: bool
    raw_response: str
    failure_reason: str | None


def evaluate_response(raw_response: str) -> tuple[bool, str | None]:
    """Check whether a raw model response is valid JSON with the expected keys.

    Args:
        raw_response: the full content string returned by the model.

    Returns:
        A pair (success, failure_reason). When success is True the second
        element is None. When success is False the second element explains
        which check failed.
    """
    stripped = raw_response.strip()
    if not stripped:
        return False, "empty response"

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return False, f"json parse error: {exc.msg}"

    if not isinstance(parsed, dict):
        return False, f"expected dict, got {type(parsed).__name__}"

    for key in EXPECTED_KEYS:
        if key not in parsed:
            return False, f"missing key: {key}"
        value = parsed[key]
        if not isinstance(value, str) or not value:
            return False, f"key {key} has empty or non-string value"

    return True, None


def run_trial(trial_index: int) -> TrialResult:
    """Run a single trial: send the prompt with JSON-format constraint.

    Args:
        trial_index: zero-based index for display purposes.

    Returns:
        A TrialResult describing what happened.
    """
    response = chat(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": PROMPT}],
        format="json",
        stream=False,
    )
    raw_response = response.message.content or ""
    success, failure_reason = evaluate_response(raw_response)
    return TrialResult(
        trial_index=trial_index,
        success=success,
        raw_response=raw_response,
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
            print(f"  response: {result.raw_response!r}")

    success_count = sum(1 for r in results if r.success)
    print()
    print(f"successes: {success_count}/{TRIAL_COUNT}")
    if success_count == TRIAL_COUNT:
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL")


if __name__ == "__main__":
    main()
