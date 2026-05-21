"""Phase 2 Test 1: Responsiveness.

Measures first-token latency and sustained tokens-per-second for the local
model across prompts of varying lengths. Streams the response so we can
distinguish time-to-first-token from total generation time.

Pass criteria:
    avg first-token latency < 2.0 seconds
    avg sustained tokens/sec > 15.0
"""

import os
import time
from dataclasses import dataclass

from ollama import chat

MODEL_NAME = os.environ.get("PHASE2_MODEL", "qwen2.5:7b-instruct")
FIRST_TOKEN_LATENCY_LIMIT_SEC = 2.0
SUSTAINED_TOKENS_PER_SEC_MIN = 15.0

PROMPTS: list[str] = [
    "Say the word READY and nothing else.",
    "What is 12 plus 7? Give only the number.",
    "List three common network interface speeds. One per line, no commentary.",
    (
        "Explain in two sentences what a routing table is. "
        "Keep it under 50 words."
    ),
    (
        "Describe in one paragraph what an Intent-Based Networking system does. "
        "Aim for about 100 words."
    ),
]


@dataclass
class PromptResult:
    """Captured measurements for a single prompt."""

    prompt_index: int
    first_token_latency_sec: float
    total_time_sec: float
    eval_count: int
    sustained_tokens_per_sec: float


def measure_prompt(prompt_index: int, prompt: str) -> PromptResult:
    """Send one streaming prompt and measure latency and throughput.

    Args:
        prompt_index: zero-based index used only for display.
        prompt: the user message content to send.

    Returns:
        A PromptResult with timing and token-count fields populated.
    """
    start_time = time.perf_counter()
    first_token_time: float | None = None
    final_chunk = None

    stream = chat(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )

    for chunk in stream:
        if first_token_time is None and chunk.message.content:
            first_token_time = time.perf_counter()
        final_chunk = chunk

    end_time = time.perf_counter()

    if first_token_time is None:
        raise RuntimeError("model produced no content for this prompt")
    if final_chunk is None:
        raise RuntimeError("stream ended without a final chunk")

    eval_count = getattr(final_chunk, "eval_count", 0) or 0
    generation_time = end_time - first_token_time
    if generation_time > 0 and eval_count > 0:
        sustained = eval_count / generation_time
    else:
        sustained = 0.0

    return PromptResult(
        prompt_index=prompt_index,
        first_token_latency_sec=first_token_time - start_time,
        total_time_sec=end_time - start_time,
        eval_count=eval_count,
        sustained_tokens_per_sec=sustained,
    )


def main() -> None:
    """Run all prompts, print per-prompt results, then summary and verdict."""
    results: list[PromptResult] = []
    for index, prompt in enumerate(PROMPTS):
        result = measure_prompt(index, prompt)
        results.append(result)
        print(
            f"prompt {index}: "
            f"first_token={result.first_token_latency_sec:.3f}s "
            f"total={result.total_time_sec:.3f}s "
            f"tokens={result.eval_count} "
            f"sustained={result.sustained_tokens_per_sec:.1f} tok/s"
        )

    avg_first_token = sum(r.first_token_latency_sec for r in results) / len(results)
    avg_sustained = sum(r.sustained_tokens_per_sec for r in results) / len(results)
    print()
    print(f"avg first-token latency: {avg_first_token:.3f}s "
          f"(limit {FIRST_TOKEN_LATENCY_LIMIT_SEC:.1f}s)")
    print(f"avg sustained throughput: {avg_sustained:.1f} tok/s "
          f"(minimum {SUSTAINED_TOKENS_PER_SEC_MIN:.1f} tok/s)")

    latency_ok = avg_first_token < FIRST_TOKEN_LATENCY_LIMIT_SEC
    throughput_ok = avg_sustained > SUSTAINED_TOKENS_PER_SEC_MIN
    if latency_ok and throughput_ok:
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL")


if __name__ == "__main__":
    main()
