Intent-Based Network Configuration Agent for SONiC

This is a portfolio project that translates natural-language network
configuration requests into verified changes on a SONiC virtual switch.
The agent uses a local LLM for intent parsing and tool dispatch, formal
network analysis via Batfish for pre-apply verification, and structural
post-apply verification against live state. Human approval is required
between proposal and apply for every change.

The project is built in seven phases, each gated by a working end-to-end
demo against real switch software. Earlier phases are preserved in their
own subdirectories so the engineering progression is readable as a
history of decisions, not a single monolithic codebase.


The seven phases

1. SONiC virtual switch running in Docker. The base infrastructure: a
   real SONiC image with a working CONFIG_DB that the rest of the
   project builds on.

2. Local LLM via Ollama. Verifying tool-calling with qwen2.5:7b-instruct
   against the Ollama Python library.

3. Connect the LLM to SONiC, read-only queries. The agent answers
   questions about live switch state through tools that wrap the SONiC
   client.

4. Configuration changes with diff preview and approval. The agent
   proposes a change, renders a structured diff, asks the user for
   approval, then applies through the SONiC CLI.

5. Pre-apply Batfish verification. Before the approval prompt, the
   agent constructs a candidate snapshot and runs Batfish parser-level
   verification against it. New critical issues introduced by the
   candidate state surface in the diff.

6. Post-apply verification. After apply, the agent re-reads CONFIG_DB
   and confirms each predicted change actually materialized. On a clean
   match, a light Batfish re-read confirms parser invariants still
   hold. On mismatch, per-prediction verdicts surface which predictions
   failed and why.

7. Polish, eval harness, top-level README, demo. The phase you are
   reading the artifact of.


Architecture

    +-----------------+        +-------------------+
    | User (terminal) |        | qwen2.5:7b        |
    +--------+--------+        | (Ollama, local)   |
             |                 +---------+---------+
             |                           |
             |  question + tools         |  structured tool calls
             v                           |
    +-----------------+   <---tools-->   |
    |    agent.py     +------------------+
    +--------+--------+
             |
             |  read or write
             |
       +-----+------+--------------------+
       |            |                    |
       v            v                    v
    +-----+    +----+----+        +------+------+
    |SONiC|    | Batfish |        | snapshot    |
    |VS   |    | (parser |        | builder     |
    |     |    | analysis|        | (pure xform)|
    +-----+    +---------+        +-------------+

Read path: agent calls SONiC client tools, returns answers as plain text.

Write path: agent calls a propose tool, which constructs a ChangePlan.
Pre-apply verification builds a candidate snapshot via snapshot_builder
and submits it to Batfish. The user sees the rendered diff plus
verification result and approves or rejects. On approval, the change
applies through the SONiC CLI. Post-apply verification re-reads
CONFIG_DB, checks per-prediction verdicts, and runs a light Batfish
re-read if the structural check passed.


Engineering story

What was built. An agent that takes natural-language requests like
"Configure Ethernet12 with IP 192.168.1.1/24" and produces three things:
a structured proposal of what would change, formal verification of the
candidate state via Batfish, and post-apply confirmation that the
predicted CONFIG_DB-level effects materialized. The agent runs entirely
local on a MacBook M4 Pro using a SONiC virtual switch in Docker, a
Batfish container, and a 7B-parameter LLM via Ollama. No cloud APIs,
no external services.

What was verified. The propose-verify-approve-apply-verify chain is
exercised end-to-end by 45 automated tests in Phase 6, including 8
integration tests that run the agent as a subprocess against live SONiC
and live Batfish. Pre-apply Batfish verification surfaces new critical
issues introduced by a candidate state. Post-apply structural
verification compares predicted CONFIG_DB keys against the now-changed
live state. A light post-apply Batfish re-read confirms parser
invariants on success.

What was learned. Three findings worth surfacing:

Batfish does not flag overlapping IP assignments at the parser level.
Two interfaces configured with overlapping subnets parse cleanly. This
matters for honest scope of what "formal verification" means here:
Batfish catches certain classes of errors (unparseable configs, missing
features, syntax problems) but not all classes (semantic overlaps,
intent violations). Documented in Phase 5 README.

SONiC has a measurable read-after-write lag in CONFIG_DB. The sonic-cfggen
apply call returns before the corresponding CONFIG_DB key is readable.
Phase 6 Chunk 1 measured 60-80ms across 5 iterations. The post-apply
check uses a bounded poll loop (2.0 second timeout, 20ms interval) to
accommodate this. Documented in Phase 6 README.

LLM tool-call formatting is non-deterministic at the 7B scale. The
qwen2.5:7b-instruct model occasionally emits tool calls as text instead
of structured function calls. Observed during Phase 6 manual testing and
in one Phase 7 automated test run. Retry resolves it. Larger models
would be more reliable at the cost of latency. Documented in Phase 6
README.

What was deliberately scoped out. This is a portfolio project, not
production code. The agent is read-only or single-step write-only. There
is no multi-turn conversation, no transaction batching across multiple
changes, no rollback on post-apply failure, no support for the full
SONiC config surface (only IP add, IP remove, admin status set), no
web UI, no HTTP API. The scoping is intentional: depth over breadth.


Prerequisites

Hardware: MacBook with Apple Silicon (the project was built on an M4
Pro). The Docker images used are ARM64-native.

Software:
    Docker Desktop with at least 8GB RAM available to containers
    Ollama with qwen2.5:7b-instruct pulled
    Python 3.11 or later
    SONiC virtual switch image (docker.io/sonic/sonic-vs:latest works
        as of the build date; pinning to a specific image hash is
        recommended for reproducibility)
    Batfish container (batfish/allinone:test-2026.04.01.3234 was used;
        any recent ARM64-compatible build should work)

The per-phase READMEs each list the exact setup steps for their
contribution. Phase 1 covers the SONiC container. Phase 2 covers Ollama.
Phase 5 covers Batfish. Phase 6 documents the end-to-end venv setup.


Quickstart

Clone the repo and bring up the supporting containers (one-time setup
documented in phase1/README.md and phase5/README.md), then:

    cd phase6
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

    python3 agent.py "What IP is configured on Ethernet0?"
    python3 agent.py "Configure Ethernet12 with IP 192.168.1.1/24"

A read query returns a one-line answer. A write request triggers
proposal, pre-apply verification, approval prompt, apply, and
post-apply verification.


Per-phase READMEs

Most phases have their own README documenting what was built, what was
verified, what was learned, and what was deliberately scoped out. They
read as commits in a continuous engineering story.

    phase1              SONiC virtual switch setup (no source
                        files; the artifact is a running container
                        named sonic-vs-fixed from a locally-built
                        docker-sonic-vs-fixed:latest image; see
                        the official SONiC docs at
                        github.com/sonic-net/sonic-buildimage for
                        VS image build instructions)
    phase2/README.md   Local LLM via Ollama, tool-calling smoke
                        tests against qwen2.5:7b-instruct
    phase3/README.md   Read-only agent: LLM connected to SONiC
                        via the read tools
    phase4/README.md   Propose-approve-apply flow with diff
                        preview and human approval
    phase5/README.md   Pre-apply Batfish verification
    phase6/README.md   Post-apply structural verification

The phase7/ directory contains the eval harness, demo script, and
final-pass polish artifacts. The top-level README you are reading is
the entry point for first-time visitors.


Known limitations

The per-phase READMEs document phase-specific limitations in detail. The
following are the cross-cutting ones worth surfacing at the top level
before someone reads the code.

Batfish does not flag overlapping IP assignments. Two interfaces with
overlapping subnets parse cleanly through Batfish init. The pre-apply
verification will report no critical issues even when overlap exists.
This bounds what "verified" means here: parser-level correctness, not
intent-level correctness. See phase5/README.md for the test evidence.

CONFIG_DB read-after-write lag is real and measurable. The post-apply
verification accommodates this with a bounded poll loop. The lag has
not been observed to exceed 100ms in practice but the timeout is set
to 2.0 seconds defensively. See phase6/README.md.

LLM tool-call format flakiness. The qwen2.5:7b-instruct model
occasionally emits tool calls as text rather than as structured function
calls. Observed in roughly 3 of dozens of invocations across the
project. Retry resolves it. A larger model or a parser fallback would
reduce the rate at the cost of latency or complexity. See phase6/README.md.

Read-only or single-step writes only. The agent does not batch multiple
changes into a transaction, does not support multi-turn conversation,
and does not roll back on post-apply failure. Each user request is a
single read or single write.

Coverage of the SONiC config surface. The agent supports adding IP
addresses, removing IP addresses, and setting interface admin status.
That is three of the dozens of SONiC config tables. The project
demonstrates the propose-verify-approve-apply-verify pattern; extending
to additional tables is straightforward but out of scope here.

Not production-ready. This is a portfolio project. Code is illustrative,
the demo runs on a single virtual switch, and operational concerns
(authentication, audit logging, concurrent operators, multi-switch
coordination) are not addressed.


Evaluation results

The phase7/ directory contains an eval harness that measures LLM
tool-call accuracy on a fixed prompt suite. See eval/results.md for the
latest run results.


Author and contact

    Author: Chandana Nandi
    Contact: https://github.com/ChandanaNandi


License

MIT License. See the LICENSE file at the project root for full text.


About this project

Built to explore whether the propose-verify-approve-apply-verify pattern
can connect a local LLM to network configuration safely. Built end-to-end
on consumer hardware (a MacBook M4 Pro) with no cloud dependencies. The
engineering story is documented per phase rather than condensed into a
single narrative; that structure was a deliberate choice to preserve the
sequence of decisions and trade-offs.


