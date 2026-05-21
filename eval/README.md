Eval harness

This directory contains the LLM tool-call evaluation harness for the
SONiC intent-based agent. It measures whether the LLM produces the
correct structured tool call for a fixed suite of natural-language
prompts.

The harness is intentionally narrow. It runs the agent's
LLM-to-tool-call layer only, with no tool execution, no SONiC changes,
no Batfish verification, and no approval prompts. End-to-end behavior
(apply, post-apply, verification) is tested by the integration tests
in phase6/.


Files

prompts.py
    The test suite as data. 20 prompts distributed across read tools
    (6), propose-add (6), propose-remove (4), and propose-set-admin
    (4). Each prompt declares the expected tool name and arguments.

harness.py
    The runner. For each prompt, invokes agent.py --eval-mode in a
    subprocess, parses the JSON result, and compares to expected.
    Aggregates results and emits structured JSON output suitable for
    machine consumption.

render_results.py
    The reporter. Reads a JSON output from harness.py and writes a
    human-readable markdown report. Run after harness.py to refresh
    results.md.

results.md
    The latest run results. Committed to the repo so a reviewer can
    see the numbers without running anything. Regenerate by running
    harness.py and then render_results.py.


How to run

From the project root:

    cd phase6
    source .venv/bin/activate
    cd ../eval

    python3 harness.py --output-json /tmp/eval_run.json
    python3 render_results.py \
        --input /tmp/eval_run.json \
        --output results.md

Total runtime: roughly 20-30 seconds for the 20-prompt suite. The
harness uses the same Phase 6 venv as agent.py.


Honest scope

This harness measures only LLM-to-tool-call accuracy. It does NOT
measure: tool execution correctness (that is covered by Phase 6 unit
tests on sonic_client and tools), end-to-end agent flow (Phase 6
integration tests), or Batfish verification behavior (Phase 5 tests).

The harness is designed to surface LLM-side issues honestly:
non-deterministic tool-call formatting, wrong argument extraction, and
misinterpretation of intent. The verdict labels distinguish these
failure modes so a reader can see exactly what went wrong.

The harness is not part of CI. It runs on demand. Re-running may
produce different results due to LLM non-determinism, which is a
documented project limitation.
