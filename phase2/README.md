Phase 2: Local LLM via Ollama

This phase verifies that a local Ollama instance running
qwen2.5:7b-instruct can be driven from Python and that the model handles
tool calling reliably enough to support the agent loop in later phases.
No SONiC interaction yet; this is an Ollama-only phase.

Goal: prove out the LLM-side stack independently before connecting it to
real switch state. If tool calling at this scale is unreliable, the rest
of the project would have been a different shape.


Prerequisites

    Ollama installed and running
    qwen2.5:7b-instruct pulled (ollama pull qwen2.5:7b-instruct)
    Python 3.11 or later
    ollama Python package installed in the active venv


Test scripts

smoke_test.py
    The minimal end-to-end check. Sends one prompt to the local Ollama
    server through the official Python client and prints the response.
    Run this first; if it fails, nothing else in this phase will work.

test_responsiveness.py
    Measures latency for short prompts. Confirms that inference is fast
    enough to make the later interactive agent loop usable. The
    measurements informed the choice to stay on the 7B model rather
    than move to 14B or larger.

test_tool_calling.py
    Defines a simple add_numbers tool and verifies that the model
    invokes it correctly when asked an arithmetic question. The tool is
    not actually executed; this checks only that the model produces a
    structured tool call with the right name and arguments. This is the
    foundational test for everything in Phases 3 onward.

test_domain_tool_calling.py
    Same pattern as test_tool_calling.py but with networking-domain
    tools resembling what later phases will use (get_interface_ip,
    list_interfaces). Verifies the model picks the right tool for the
    right intent in the project's actual domain, not just abstract
    arithmetic.

test_json_output.py
    Checks the model's ability to produce JSON in response to structured
    requests. Not directly used by later phases (the Ollama tool-call
    interface bypasses this), but documented as a fallback if tool
    calling ever proved unreliable.


How to run

    cd phase2
    python3 smoke_test.py
    python3 test_responsiveness.py
    python3 test_tool_calling.py
    python3 test_domain_tool_calling.py
    python3 test_json_output.py


What was learned

Tool calling at the 7B scale is generally reliable but not deterministic.
The model occasionally emits a tool call as text rather than as a
structured call. This finding shaped the agent's design in later phases:
the agent retries-on-flakiness rather than assuming tool calls always
arrive in the structured format. See phase6/README.md known limitations
for the full discussion.

Latency on the M4 Pro for the 7B model is acceptable for an interactive
agent (single-digit seconds per turn). The 14B model adds noticeable
delay without proportionally improving tool-call reliability at this
scale, so the project stays on 7B.
