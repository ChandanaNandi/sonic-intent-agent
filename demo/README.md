SONiC Intent-Based Agent Demo Script

This is a written script for recording a demo of the project. It lists
the commands to run, the expected output, and brief speaker notes about
what to highlight at each step. Use it as a guide while screen-recording
your terminal.

Approximate total runtime: 5-7 minutes of demo, plus setup verification.

Audience assumption: a senior network engineer or engineering manager
who has not seen the project before. They understand SONiC, BGP,
Batfish at a high level, and have used local LLMs.


Section 0: Setup verification

Before recording, confirm the supporting services are healthy. Run
these commands and visually verify the output. Do not include this
section in the recorded demo unless you want to show "no surprises"
context.

    docker ps --filter "name=sonic" --format "{{.Names}}: {{.Status}}"
    docker ps --filter "name=batfish" --format "{{.Names}}: {{.Status}}"
    ollama list | grep qwen2.5

Expected:
    sonic-vs-fixed: Up X minutes/hours
    batfish: Up X minutes/hours
    qwen2.5:7b-instruct appears in the model list

If any of these are missing, the rest of the demo will fail. Start
those services first.


Section 1: Activate the venv

Speaker notes: this is the Phase 6 working directory where the
production-style code lives. Earlier phases (1-5) are preserved in
their own directories to show the engineering progression.

    cd ~/sonic-project/phase6
    source .venv/bin/activate

No output expected, the venv prompt changes to indicate activation.


Section 2: A natural-language read query

Speaker notes: the first thing to show is that the agent can answer
questions in plain English. This is Phase 3 behavior, the simplest
end-to-end demonstration. The query goes from English to a tool call,
into SONiC, and back as a plain-English answer in roughly one second.

Command:

    python3 agent.py "What IP is configured on Ethernet0?"

Expected output:

    The IP address configured on Ethernet0 is 10.0.0.1/24.

Speaker notes for this step:
    - One question, one answer, no ceremony.
    - Behind the scenes: LLM parsed intent, picked the right tool from
      a list of 6, called it, got the answer, and phrased the response.
    - This is the foundation. Everything else builds on top of this
      proven LLM-to-tool-call pattern.


Section 3: A write request, full pipeline

Speaker notes: now we show the full propose-verify-approve-apply-verify
chain. This is the headline feature of the project. The user asks the
agent to make a change. The agent does NOT just apply it. It builds a
proposal, verifies it through Batfish, shows the user a diff, asks for
approval, applies, and verifies the result.

Command:

    python3 agent.py "Configure Ethernet24 with IP 10.24.0.1/24"

Expected output:

    Running pre-apply verification...
    Proposed change:
      Add IP 10.24.0.1/24 to interface Ethernet24

    Commands that will run:
      config interface ip add Ethernet24 10.24.0.1/24

    Predicted CONFIG_DB changes:
      + INTERFACE|Ethernet24
      + INTERFACE|Ethernet24|10.24.0.1/24

    Pre-apply verification:
      no new issues introduced (1.7s)

    Approve this change? [y/N]:

Pause here while reading the rendered diff to camera. Speaker notes:
    - The agent never just runs commands. It proposes, then shows you
      exactly what would change.
    - "Pre-apply verification: no new issues introduced" means Batfish
      parsed the candidate state and did not flag any new critical
      issues compared to the current state.
    - That is formal verification, not just intent matching. The agent
      cannot trick itself; the verification is structural.

Type y and press Enter. Expected output:

    Change applied.
    Running post-apply verification...
    Post-apply verification:
      all 2 predicted CONFIG_DB change(s) verified (0.33s)
      Post-apply Batfish re-read: clean parse, no critical issues (0.63s)

Speaker notes:
    - Apply happened. The agent then re-read CONFIG_DB and confirmed
      both predicted database keys actually materialized.
    - One Batfish re-read at the end confirms parser invariants still
      hold after the change. That is post-apply structural
      verification, complementing the pre-apply analysis.
    - Total wall time from request to verified change: 5-8 seconds.

Confirmation step. Show that the change actually landed in CONFIG_DB:

    docker exec sonic-vs-fixed redis-cli -n 4 KEYS "INTERFACE|Ethernet24*"

Expected output:

    INTERFACE|Ethernet24
    INTERFACE|Ethernet24|10.24.0.1/24

Speaker notes:
    - Both keys present in live SONiC CONFIG_DB.
    - This is not a simulation. The change happened on a real SONiC
      virtual switch running in a Docker container.


Section 4: A write request, rejected

Speaker notes: now show what happens when the user rejects. The
verification still runs (it has already run by the time the prompt
appears), so the user has all the information they need. They just
choose not to proceed. Nothing applies.

Command:

    python3 agent.py "Configure Ethernet28 with IP 10.28.0.1/24"

Expected output (pre-apply portion identical to Section 3 but with
different interface/IP):

    Running pre-apply verification...
    Proposed change:
      Add IP 10.28.0.1/24 to interface Ethernet28
    [...]
    Pre-apply verification:
      no new issues introduced (1.7s)

    Approve this change? [y/N]:

Type n and press Enter. Expected output:

    Change rejected. No modifications made.

Confirm nothing changed in CONFIG_DB:

    docker exec sonic-vs-fixed redis-cli -n 4 KEYS "INTERFACE|Ethernet28*"

Expected output: nothing (Ethernet28 has no keys).

Speaker notes:
    - The verification ran. The diff was rendered. The user said no.
    - Zero state change on SONiC.
    - This is the human-in-the-loop guard rail. The agent never applies
      anything without explicit approval.


Section 5: Honest demonstration of what verification catches and does not

Speaker notes: this is the most important section for engineering
credibility. Pre-apply Batfish verification has limits. It catches
some classes of problems (unparseable configs, syntax errors, missing
features) but not all. Specifically, it does not flag overlapping IP
assignments at the parser level. The demo shows this honestly.

Setup: confirm Ethernet0 already has an IP assigned.

    python3 agent.py "What IP is configured on Ethernet0?"

Expected output:

    The IP address configured on Ethernet0 is 10.0.0.1/24.

Now propose a change that should logically conflict: another interface
with an overlapping subnet.

    python3 agent.py "Configure Ethernet32 with IP 10.0.0.99/24"

Expected output (pre-apply verification):

    Running pre-apply verification...
    [...]
    Pre-apply verification:
      no new issues introduced (1.7s)

    Approve this change? [y/N]:

Speaker notes (this is the critical talking point):
    - Ethernet0 already has 10.0.0.1/24. The proposed change adds
      10.0.0.99/24 on a different interface, in the same subnet.
    - Pre-apply verification reports "no new issues introduced."
    - This is honest: Batfish's parser-level analysis does not detect
      this kind of semantic overlap. It catches what it catches, and
      this is not in that set.
    - The verification layer is real and useful, but it is not a
      complete safety guarantee. Knowing the boundary of what
      verification covers is a senior engineering skill.
    - This finding is documented in phase5/README.md's known
      limitations section. A reviewer who reads the project will see
      that we tested this, documented the gap, and did not paper over
      it.

Reject the change (n + Enter) to leave the demo state clean.

Expected: "Change rejected. No modifications made."


Section 6: Brief architecture walkthrough

Speaker notes: now switch to showing the code itself. Open agent.py.
This is meant to be ~60 seconds of "here is the structure"  not a deep
read through. The goal is to show that the code is organized and clean,
not to teach Python.

    cat phase6/agent.py | head -80

Expected: the agent.py header, imports, constants, and start of the
read flow.

Points to highlight as you scroll:
    - SYSTEM_PROMPT: the LLM is told it manages a SONiC switch.
    - AVAILABLE_TOOLS: 6 functions exposed to the LLM (3 read, 3
      propose). Each one is a normal Python function with type hints
      and a docstring. The Ollama Python library auto-generates the
      tool schema from these.
    - _post_apply_verify: the function that runs the four-step
      sequence after apply. Wait for CONFIG_DB to settle, check
      predictions, optional Batfish re-read on success, render the
      result.
    - answer_question: the main agent loop. One LLM round-trip for
      reads, one round-trip plus apply for writes.

Total agent.py is around 460 lines. The architecture is intentionally
flat: there is no class hierarchy, no plugin system, no DI framework.
Just functions that compose.


Section 7: Cleanup and closing

Speaker notes: clean up any state added during the demo so the project
is in a known state at the end of the recording.

Commands:

    python3 agent.py "Remove the IP 10.24.0.1/24 from Ethernet24"
    # approve with y

After this runs, confirm cleanup:

    docker exec sonic-vs-fixed redis-cli -n 4 KEYS "INTERFACE|Ethernet24*"

Expected: nothing.

Closing speaker notes:
    - Everything you saw runs locally on this MacBook. No cloud APIs,
      no remote services. The LLM is qwen2.5:7b-instruct via Ollama,
      the switch is SONiC virtual switch in Docker, the verification
      engine is Batfish in Docker.
    - The project is organized in seven phases. Each has its own
      README explaining what was built, what was learned, and what was
      deliberately scoped out.
    - The eval harness in eval/ measures LLM tool-call accuracy on a
      fixed prompt suite. Latest run: 20/20 prompts passed.
    - Code is on GitHub at <repo URL>.


Total recording duration estimate

Section 1 (venv activation):           5 seconds
Section 2 (read query):               15 seconds
Section 3 (write with approval):    60-90 seconds
Section 4 (write with rejection):   30-45 seconds
Section 5 (IP overlap honest demo): 90-120 seconds
Section 6 (architecture):           60-90 seconds
Section 7 (cleanup and closing):    30-45 seconds

Total: 4.5 to 7 minutes.
