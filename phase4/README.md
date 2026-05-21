# Phase 4: SONiC agent with propose-approve-apply writes

A natural-language agent that can both query and modify a running SONiC
virtual switch. Every change goes through a mandatory diff preview and
explicit user approval before being applied.

## What this phase delivers

Phase 4 builds on Phase 3 by adding three write operations:
- Add an IP address to an interface
- Remove an IP address from an interface
- Set interface admin status (up or down)

Each write goes through the cycle:
1. User asks for a change in natural language
2. The LLM picks the right propose_ tool and builds a ChangePlan
3. The agent renders a diff with both logical and concrete details
4. The agent prompts for user approval via stdin (y to apply, anything else to reject)
5. On approval, the agent applies the change directly to SONiC and re-queries CONFIG_DB to verify

The LLM never has direct apply tools. It can only propose changes; the
agent code controls the approval gate and the actual apply.

## Architecture

- sonic_client.py: low-level client. Read functions from Phase 3 plus
  three apply_ functions for the write operations.
- change_plan.py: ChangePlan dataclass representing a proposed change.
  Immutable, validated at construction.
- diff_renderer.py: turns a ChangePlan into multi-section diff text.
- tools.py: tool layer the LLM sees. Three read tools from Phase 3
  plus three propose_ tools for writes. Propose tools build a
  ChangePlan and stash it in a module-level list for the agent.
- agent.py: agent loop. Calls the LLM, executes tool calls, detects
  proposed plans, renders the diff, prompts for approval, applies on
  approval, reports post-apply state.
- fixture.py: idempotent setup of known interface configs (copied from
  Phase 3, unchanged).
- test_agent_write.py: integration tests for the five Phase 4 success
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

Read queries (Phase 3 behavior, preserved):

    python3 agent.py "What IP is configured on Ethernet0?"
    python3 agent.py "Is BGP running on this switch?"

Write requests (Phase 4 new behavior):

    python3 agent.py "Configure Ethernet16 with IP 192.168.16.1/24"
    python3 agent.py "Shut down Ethernet8"
    python3 agent.py "Remove the IP from Ethernet4"

The agent will print a diff and wait for stdin. Type y to approve,
anything else to reject.

For non-interactive use (scripts, tests):

    echo "y" | python3 agent.py "Configure Ethernet16 with IP 192.168.16.1/24"
    echo "n" | python3 agent.py "..."

## Running tests

    python3 -m unittest test_agent_write.py -v

The full Phase 4 suite takes 15-30 seconds. Tests use Ethernet20 and
Ethernet24 to avoid conflict with fixture interfaces.

To also run the Phase 3 read tests, copy test_agent.py from phase3/
into this directory (or run them from phase3/ separately).

## Known limitations

- Single change per request. The LLM should propose exactly one change.
  If it proposes more, only the first is considered and a warning prints.
- Single tool round trip. The agent does not loop on the LLM (no
  multi-step planning). Phase 4 scope is single-shot.
- No undo. Approved-and-applied changes stay applied. To undo, ask the
  agent to remove or revert.
- No pre-apply verification beyond input validation. Whether a change
  is "correct" (does not break the network) is the user's responsibility
  in Phase 4. Pre-apply formal verification is Phase 5 (Batfish).
- Apply happens directly after approval; there is no transactional
  rollback if the apply step fails partway through a multi-command plan.
  Plans in Phase 4 use only one command each, so this is not a current
  risk.

## What is NOT in this phase

- No formal pre-apply verification (Phase 5)
- No post-apply impact analysis beyond direct CONFIG_DB inspection
- No transactional or atomic multi-step changes
- No undo or rollback
- No multi-turn agent reasoning
- No agent framework dependencies (LangChain, etc.)
