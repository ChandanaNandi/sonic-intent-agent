"""Eval harness for the SONiC intent-based agent.

Runs each prompt from prompts.py through agent.py --eval-mode in a
subprocess, parses the resulting JSON, and compares against expected
tool calls. Aggregates results and emits a JSON summary on stdout.

Use:
    python3 harness.py
    python3 harness.py > run_output.json

The Phase 7 README generator (see Substep 7-3d) consumes the JSON
output to render eval/results.md.

This harness exercises only the LLM-to-tool-call layer. It does not
apply changes, does not run Batfish, does not run post-apply
verification. Those are tested separately in the Phase 6 integration
test suite.
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = PROJECT_ROOT / "eval"
AGENT_DIR = PROJECT_ROOT / "phase6"
AGENT_SCRIPT = AGENT_DIR / "agent.py"
SUBPROCESS_TIMEOUT_SECONDS = 30

PASS = "PASS"
FAIL_WRONG_TOOL = "FAIL_WRONG_TOOL"
FAIL_WRONG_ARGS = "FAIL_WRONG_ARGS"
FAIL_TEXT_FALLBACK = "FAIL_TEXT_FALLBACK"
FAIL_NO_TOOL_CALL = "FAIL_NO_TOOL_CALL"
FAIL_SUBPROCESS_ERROR = "FAIL_SUBPROCESS_ERROR"


@dataclass
class PromptResult:
    """The outcome of running one prompt through the harness."""

    prompt: str
    expected_tool: str
    expected_args: dict
    actual_tool: str = ""
    actual_args: dict = field(default_factory=dict)
    verdict: str = ""
    reason: str = ""
    elapsed_seconds: float = 0.0
    raw_text: str = ""


@dataclass
class RunSummary:
    """Aggregate counts across all prompts."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate_pct: float = 0.0
    elapsed_seconds: float = 0.0


def _compare_call(
    actual_name: str,
    actual_args: dict,
    expected_name: str,
    expected_args: dict,
    args_match_mode: str,
) -> tuple[str, str]:
    """Compare an actual tool call to the expected one.

    Returns:
        Tuple of (verdict, reason). Verdict is PASS or one of the
        FAIL_* constants. Reason is a short human-readable explanation.
    """
    if actual_name != expected_name:
        return (
            FAIL_WRONG_TOOL,
            f"expected tool {expected_name!r}, got {actual_name!r}",
        )

    if args_match_mode == "exact":
        if dict(actual_args) != dict(expected_args):
            return (
                FAIL_WRONG_ARGS,
                f"expected args {expected_args!r}, got {dict(actual_args)!r}",
            )
        return PASS, "ok"

    if args_match_mode == "subset":
        for key, expected_value in expected_args.items():
            if key not in actual_args:
                return (
                    FAIL_WRONG_ARGS,
                    f"missing expected arg {key!r}",
                )
            if actual_args[key] != expected_value:
                return (
                    FAIL_WRONG_ARGS,
                    f"arg {key!r} expected {expected_value!r}, "
                    f"got {actual_args[key]!r}",
                )
        return PASS, "ok"

    return (
        FAIL_WRONG_ARGS,
        f"unknown args_match_mode {args_match_mode!r}",
    )


def _run_one_prompt(prompt_entry: dict) -> PromptResult:
    """Invoke agent --eval-mode for one prompt and return a PromptResult."""
    result = PromptResult(
        prompt=prompt_entry["prompt"],
        expected_tool=prompt_entry["expected_tool"],
        expected_args=dict(prompt_entry["expected_args"]),
    )

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(AGENT_SCRIPT),
                "--eval-mode",
                prompt_entry["prompt"],
            ],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
            cwd=str(AGENT_DIR),
        )
    except subprocess.TimeoutExpired:
        result.elapsed_seconds = time.monotonic() - start
        result.verdict = FAIL_SUBPROCESS_ERROR
        result.reason = (
            f"agent subprocess timed out after "
            f"{SUBPROCESS_TIMEOUT_SECONDS}s"
        )
        return result
    except OSError as exc:
        result.elapsed_seconds = time.monotonic() - start
        result.verdict = FAIL_SUBPROCESS_ERROR
        result.reason = f"failed to launch agent subprocess: {exc}"
        return result

    result.elapsed_seconds = time.monotonic() - start

    if proc.returncode != 0:
        result.verdict = FAIL_SUBPROCESS_ERROR
        result.reason = (
            f"agent exited with code {proc.returncode}. "
            f"stderr: {proc.stderr.strip()[:200]}"
        )
        return result

    stdout = proc.stdout.strip()
    if not stdout:
        result.verdict = FAIL_NO_TOOL_CALL
        result.reason = "agent stdout was empty"
        return result

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        result.verdict = FAIL_SUBPROCESS_ERROR
        result.reason = f"agent stdout was not valid JSON: {exc}"
        result.raw_text = stdout[:200]
        return result

    tool_calls = payload.get("tool_calls", [])
    raw_text = payload.get("raw_text", "")
    result.raw_text = raw_text

    if not tool_calls:
        if raw_text.strip():
            result.verdict = FAIL_TEXT_FALLBACK
            result.reason = (
                f"LLM emitted text instead of structured tool call: "
                f"{raw_text.strip()[:200]}"
            )
        else:
            result.verdict = FAIL_NO_TOOL_CALL
            result.reason = "no tool calls and no text in response"
        return result

    first_call = tool_calls[0]
    result.actual_tool = first_call.get("name", "")
    result.actual_args = first_call.get("arguments", {}) or {}

    verdict, reason = _compare_call(
        actual_name=result.actual_tool,
        actual_args=result.actual_args,
        expected_name=prompt_entry["expected_tool"],
        expected_args=prompt_entry["expected_args"],
        args_match_mode=prompt_entry.get("args_match_mode", "exact"),
    )
    result.verdict = verdict
    result.reason = reason
    return result


def run_eval(prompts_list: list[dict]) -> tuple[RunSummary, list[PromptResult]]:
    """Run every prompt and aggregate results."""
    summary = RunSummary(total=len(prompts_list))
    results: list[PromptResult] = []
    overall_start = time.monotonic()

    for index, prompt_entry in enumerate(prompts_list, start=1):
        print(
            f"[{index}/{len(prompts_list)}] {prompt_entry['prompt'][:60]}",
            file=sys.stderr,
            flush=True,
        )
        result = _run_one_prompt(prompt_entry)
        results.append(result)
        marker = "ok" if result.verdict == PASS else result.verdict
        print(
            f"    {marker} ({result.elapsed_seconds:.1f}s)",
            file=sys.stderr,
            flush=True,
        )

    summary.elapsed_seconds = time.monotonic() - overall_start
    summary.passed = sum(1 for r in results if r.verdict == PASS)
    summary.failed = summary.total - summary.passed
    if summary.total > 0:
        summary.pass_rate_pct = 100.0 * summary.passed / summary.total
    return summary, results


def main() -> int:
    """CLI entry point. Runs the eval and writes JSON output."""
    parser = argparse.ArgumentParser(
        description="Run the LLM tool-call eval harness."
    )
    parser.add_argument(
        "--output-json", default="-",
        help="path to write JSON output (default: stdout)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(EVAL_DIR))
    import prompts as prompts_module

    summary, results = run_eval(prompts_module.PROMPTS)

    payload = {
        "summary": asdict(summary),
        "results": [asdict(r) for r in results],
    }

    output_str = json.dumps(payload, indent=2)
    if args.output_json == "-":
        print(output_str)
    else:
        Path(args.output_json).write_text(output_str, encoding="utf-8")

    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
