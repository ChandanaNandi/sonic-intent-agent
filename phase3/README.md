# Phase 3: SONiC read-only agent

A natural-language agent that answers questions about a running SONiC
virtual switch by calling read-only tools. The agent uses a local LLM
(via Ollama) to interpret questions, choose tools, and produce answers
grounded in live switch state.

## What this phase delivers

A working single-turn agent loop:
question -> LLM with tool catalog -> tool call -> SONiC query -> tool
result -> LLM final answer.

Three read-only tools are exposed to the LLM:
- get_interface_ip(interface_name): IP address of a given interface
- list_configured_interfaces(): names of interfaces with any L3 config
- get_bgp_status(): whether BGP is configured and its summary

## Architecture

- sonic_client.py: low-level wrapper around docker exec calls to the
  SONiC container. Returns structured Python data.
- tools.py: tool layer the LLM sees. Wraps sonic_client functions,
  catches exceptions, returns strings.
- agent.py: agent loop. Takes a question, runs one tool-call cycle if
  needed, returns the final answer.
- fixture.py: idempotent setup of known interface configs for testing.
- test_agent.py: integration tests for the four Phase 3 success
  criteria.

## Prerequisites

- The sonic-vs-fixed Docker container must be running. See Phase 1.
- The Ollama service must be running locally on port 11434.
- The qwen2.5:7b-instruct model must be available in Ollama.
- Python 3.10 or newer.

## Setup

From this directory:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install ollama==0.6.2
    python3 fixture.py

## Usage

    python3 agent.py "What IP is configured on Ethernet0?"
    python3 agent.py --verbose "Is BGP running on this switch?"
    AGENT_MODEL=qwen2.5:14b-instruct python3 agent.py "..."

## Running tests

    python3 -m unittest test_agent.py -v

The full suite takes around 15 seconds. Each test calls the real LLM
and the real switch. Tests assume the fixture has been applied.

## Known limitations

- Single-turn only. The agent cannot do multi-step reasoning (e.g.
  "check the IP, then ping it"). Multi-step is Phase 4+.
- No write operations. The agent has no tools to modify switch state.
  This is by design for Phase 3.
- LLM outputs are non-deterministic. Integration tests rely on
  substring assertions, which can be brittle if the model phrases
  things unexpectedly. A failing test should be re-run before assuming
  a real bug.
- The model choice (qwen2.5:7b-instruct) is what Phase 2 testing
  recommended. If the agent struggles in later phases, swapping to
  14b is one env var change.

## What is NOT in this phase

- No configuration changes (Phase 4)
- No diff preview or human approval workflow (Phase 4)
- No Batfish verification (Phase 5)
- No post-apply verification (Phase 6)
- No multi-turn or planning agents
- No agent framework (LangChain, etc.) - direct Ollama only
