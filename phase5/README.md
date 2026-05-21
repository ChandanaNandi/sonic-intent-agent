# Phase 5: Pre-apply verification with Batfish

## Project context

This is Phase 5 of a seven-phase project building an intent-based network configuration agent for SONiC. The agent accepts natural-language instructions like "Configure Ethernet12 with IP 192.168.1.1/24", translates them into structured change plans through a local LLM, and either reads live state or proposes-verifies-approves-applies a change. The full phase order is Phase 1 (SONiC virtual switch in Docker), Phase 2 (local LLM via Ollama), Phase 3 (read-only queries via LLM tool calling against live CONFIG_DB), Phase 4 (configuration changes with diff preview and human approval), Phase 5 (Batfish pre-apply verification, this phase), Phase 6 (post-apply verification), and Phase 7 (polish, evaluation harness, demo).

Phase 5 adds a verification step between the proposed change and the approval prompt. Before the user is asked to approve a configuration change, the agent submits the current SONiC state and the candidate state (current plus the proposed change applied) to a Batfish container, computes the diff of init issues between them, and shows the result inside the diff that the user sees.

## Architecture overview

Phase 4 established the propose-approve-apply pipeline. See `../phase4/README.md` for that flow. Phase 5 inserts a verification step into the write-flow branch only. The sequence is as follows.

When the user issues a write request, the LLM calls one of the propose tools (propose_add_interface_ip, propose_remove_interface_ip, propose_set_interface_admin_status). The propose tool returns a ChangePlan in memory but does not modify SONiC. The agent then calls verify_plan on that ChangePlan. The verifier uses snapshot_builder to extract the live SONiC config via sonic-cfggen and write a Batfish-format snapshot directory. It then builds a second snapshot with the proposed change applied to the in-memory config. Both snapshots are submitted to Batfish through batfish_client. The verifier reads init issues for both, computes the set difference (candidate issues minus current issues), classifies the outcome into one of five statuses, and returns a frozen VerificationResult dataclass. The agent passes that result to diff_renderer, which produces a 4-section diff: proposed change description, commands that will run, predicted CONFIG_DB changes, and the new pre-apply verification section. The user sees the diff and is prompted to approve. The approval prompt itself is unchanged from Phase 4.

Phase 3 read-only queries (see `../phase3/README.md`) do not run verification. Only the write flow goes through verifier.py. Read queries continue to behave exactly as in Phase 3.

## Module descriptions

Phase 5 adds three new modules and modifies two.

`batfish_client.py` is the wrapper around the pybatfish API. It hides pybatfish exception classes behind a single BatfishClientError and exposes a small surface: open_session, get_service_version, init_snapshot, get_parse_status, get_init_issues, summarize_issues. The summarize function reduces a pandas DataFrame of init issues to two lists of short strings (critical and warnings), suitable for diffing.

`snapshot_builder.py` is in two halves. The first half is a pure Python transformation: apply_plan_to_config_db takes a SONiC CONFIG_DB dict and a ChangePlan, returns a new dict with the change applied, never mutates the input. The second half is the I/O layer: _fetch_live_config_db runs sonic-cfggen -d --print-data inside the SONiC container to get the live CONFIG_DB as JSON rather than the stale /etc/sonic/config_db.json on disk, _write_snapshot writes the Batfish-required directory layout, and the two public functions build_current_snapshot and build_candidate_snapshot compose these. The split is intentional: the pure transformation has 13 unit tests that run in milliseconds with no SONiC or Batfish dependency.

`verifier.py` is the orchestrator. The public function verify_plan builds both snapshots, submits them to Batfish, computes the new-issue diff, and returns a frozen VerificationResult dataclass. The verifier classifies outcomes into five status constants: STATUS_OK, STATUS_WARNINGS, STATUS_CRITICAL, STATUS_TIMEOUT, STATUS_UNAVAILABLE. It uses signal.SIGALRM for the 60-second timeout (main-thread only, documented limitation) and a heuristic _looks_like_unreachable to translate transport-level pybatfish errors into the unavailable status.

`diff_renderer.py` extends from 3 sections to 4. The new render signature accepts an optional verification_result argument and is backward-compatible: with None, output is identical to the Phase 4 3-section diff; with a result, a fourth pre-apply verification section is appended with status-specific formatting (one-line for ok/timeout/unavailable, bulleted list of new issues for warnings, exclamation-prefixed list for critical).

`agent.py` adds one new step to the write-flow branch. After a propose tool produces a ChangePlan, the agent calls _verify_plan_safely which translates Batfish session-creation failures into STATUS_UNAVAILABLE rather than crashing. The verification result is passed to diff_renderer.render so the user sees the 4-section diff before the approval prompt. Read-flow behavior is unchanged.

## Prerequisites

This phase runs on macOS with Apple Silicon (ARM64). Hardware-specific notes for other platforms are not included.

Docker Desktop for Mac, verified on Apple Silicon, is required.

The Phase 1 SONiC container must be running as sonic-vs-fixed. See `../phase1/README.md` for the setup steps.

The Phase 3 fixture must be applied to give the switch a known baseline: Ethernet0 with 10.0.0.1/24, Ethernet4 with 10.0.4.1/24, Ethernet8 with 10.0.8.1/24. Run `python3 fixture.py` from the phase5 directory if the baseline is not present.

Ollama must be installed with the qwen2.5:7b-instruct model and the Ollama service running on localhost:11434. See `../phase2/README.md` for setup.

A Python 3.12 virtual environment must exist at phase5/.venv with the project dependencies installed. The Setup section below covers this.

The Batfish container must be running. The image tag must be `batfish/allinone:test-2026.04.01.3234` or newer to get a native ARM64 build. The `:latest` tag at the time of Phase 5 development was AMD64-only and ran under emulation, which was slow and unstable; using the test tag with native ARM64 is significantly faster.

## Setup

From the project root, create the Phase 5 virtual environment, install dependencies, and start the Batfish container.

Create the virtual environment:

    cd ~/sonic-project/phase5
    python3 -m venv .venv
    source .venv/bin/activate

Install dependencies. The pinned versions match what Phase 5 was developed and tested against. Newer versions of pybatfish may work but have not been verified.

    pip install pybatfish==2025.7.7.2423 ollama==0.6.2

Start Batfish. The `-v batfish-data:/data` flag uses a named Docker volume so Batfish state persists across container restarts. The two port mappings expose the worker (9996) and the older v1 service (8888); pybatfish uses 9996.

    docker run -d --name batfish -v batfish-data:/data -p 8888:8888 -p 9996:9996 batfish/allinone:test-2026.04.01.3234

Wait at least 30 seconds for Batfish to finish initializing, then verify the service is responding:

    nc -z -v -w 5 localhost 9996

Expected output: a line saying the connection succeeded. If the port is refused after 60 seconds, check `docker logs batfish` for startup errors.

## Usage examples

The agent CLI is the same as Phase 4 with the addition that write requests go through pre-apply verification automatically.

A read query against live SONiC state. No verification runs because no write was proposed.

    python3 agent.py "What IP is configured on Ethernet0?"

Expected output: a single line stating the IP, for example "The IP address configured on Ethernet0 is 10.0.0.1/24."

A write request that the user rejects. Verification runs, the 4-section diff is shown, and the rejection blocks the apply.

    echo "n" | python3 agent.py "Configure Ethernet20 with IP 10.20.0.1/24"

Expected output: "Running pre-apply verification..." on stderr, the 4-section diff on stdout including a "Pre-apply verification: no new issues introduced (X.Xs)" line, the approval prompt, then "Change rejected. No modifications made."

A write request that the user approves. Verification runs, the diff is shown, the approval triggers the apply, and the agent reports the post-apply state.

    echo "y" | python3 agent.py "Configure Ethernet20 with IP 10.20.0.1/24"

Expected output: similar to the rejection case but ending with "Change applied." and "Post-apply state: Ethernet20 has IP 10.20.0.1/24".

A write request while Batfish is unavailable. Verification cannot run, but the user is still in control.

    docker stop batfish
    echo "n" | python3 agent.py "Configure Ethernet20 with IP 10.20.0.1/24"

Expected output: pybatfish retry warnings on stderr (cosmetic noise from the underlying HTTP library), then the 4-section diff with "Pre-apply verification: SERVICE UNAVAILABLE (X.Xs) - Batfish unreachable; verification skipped". The approval prompt still appears so the user can choose to proceed or reject. Restart Batfish with `docker start batfish` after the test.

Verbose mode shows all internal logs (LLM calls, tool calls, snapshot writes, Batfish work status) on stderr. Useful for demos.

    python3 agent.py --verbose "Configure Ethernet20 with IP 10.20.0.1/24"

## Verification status reference

The verifier returns one of five statuses in the VerificationResult.status field. Each maps to a specific rendering in the diff.

STATUS_OK means Batfish parsed both the current and candidate snapshots and the candidate did not introduce any new issues. The renderer shows a one-line "no new issues introduced (X.Xs)" message. This is the expected status for valid configuration changes.

STATUS_WARNINGS means Batfish reported new non-critical issues in the candidate snapshot that were not present in the current. The renderer shows the count and a bulleted list of the new issue descriptions. The user is still prompted to approve; warnings are informational.

STATUS_CRITICAL means Batfish reported new critical errors in the candidate. The renderer shows the count and an exclamation-prefixed list of the new errors. The user is still prompted to approve; the agent does not auto-reject. This is a deliberate design choice: the user is the final decision-maker, and there are legitimate cases where a "critical" parser output is a false positive on an intentional change.

STATUS_TIMEOUT means the verifier exceeded the 60-second time budget. The renderer shows a "TIMED OUT (60.0s) - proceed at your own risk" message. The user is still prompted.

STATUS_UNAVAILABLE means the Batfish service could not be reached. This typically happens when the Batfish container is stopped or has not finished starting. The renderer shows a "SERVICE UNAVAILABLE - Batfish unreachable; verification skipped" message. The user is still prompted.

In all five cases the user retains full control of whether to approve or reject the change. Verification is advisory, not gating.

## Known limitations

Phase 5 has several honest limitations that are worth surfacing. None of these block the design from working; they are realistic boundaries of the underlying tools.

Batfish does not flag IP address overlap. During Phase 5 testing (Chunk 5 Test B), an IP overlap scenario was constructed: Ethernet0 had 10.0.0.1/24 in the fixture, and a candidate change proposed adding 10.0.0.99/24 to Ethernet20. Both addresses are in the same 10.0.0.0/24 subnet, which is a classic network misconfiguration. Batfish's SONiC parser on the test-2026.04.01.3234 image returned STATUS_OK and did not flag this as a new issue. This bounds the actual value of the Batfish integration for this project: it validates parse syntax, reference integrity, and surfaces unimplemented features, but it does not catch overlap-class semantic errors against this version's SONiC parser. Production deployments would want additional Python-side checks for cases like this.

The SONiC VS image without BGP configured does not produce a real frr.conf. Batfish's SONiC parser requires both config_db.json and frr.conf in the snapshot directory; the file we provide is a one-line comment-only stub. Batfish accepts it and parses both files as SONIC format, but reports five baseline warnings about unimplemented features (WRED_PROFILE, MAP_PFC_PRIORITY_TO_QUEUE, CABLE_LENGTH tables and missing hostname). The same five warnings appear in both current and candidate snapshots, so the new-issue diff correctly ignores them. This is documented honestly because it reflects what Batfish's "initial SONiC support" actually is on the current image.

When the Batfish container is stopped, pybatfish retries the connection three times before giving up. This produces three urllib3 warning lines on stderr before the agent's own message and adds about 4.8 seconds of latency to the unavailable case. The retry behavior is internal to pybatfish; suppressing it would require either patching urllib3's logger or contributing a configuration option upstream. Out of scope for Phase 5; candidate for Phase 7 polish work.

The 60-second timeout is implemented with signal.SIGALRM and so only fires when verify_plan is called from the main thread. The current CLI agent always calls from the main thread. If verifier.py is ever invoked from a worker thread, the timeout will not fire and the call could hang for as long as Batfish takes. Documented in the verifier.py module docstring.

The pure transformation section and the I/O glue section of snapshot_builder.py have their imports in two separate locations rather than all at the top of the file. This is non-PEP-8 but intentional: it preserves the conceptual separation between the dependency-free pure function and the SONiC/Docker-dependent I/O. A reasonable PEP 8 cleanup would move all imports to the top with explanatory comments. Candidate for Phase 7 polish.

The automated test suite covers four of the five VerificationResult statuses indirectly but does not have automated coverage for STATUS_UNAVAILABLE or STATUS_CRITICAL. STATUS_UNAVAILABLE requires stopping the Batfish container during the test, which would race with parallel test runs and is hard to clean up reliably; it is verified manually in Chunk 5 Test A. STATUS_CRITICAL cannot be synthesized against a clean live SONiC without breaking the container; its rendering path is covered by unit tests on diff_renderer.

## Coverage matrix

Behavior, Test type, Where.

apply_plan_to_config_db pure transformation, unit test, test_snapshot_builder.py with 13 tests.

snapshot_builder file I/O and end-to-end snapshot writing, integration test, exercised by test_agent_verify.py through the real agent.

batfish_client.open_session and get_service_version, smoke test executed during Chunk 2 substep, no automated test file because the functions are thin wrappers tested indirectly.

verifier.verify_plan happy-path STATUS_OK, integration test, test_agent_verify.py test_clean_verification_approval_applies.

Diff renderer with verification_result=None, regression confirmed during Chunk 4 smoke tests, no separate test file (the Phase 4 tests still exercise this path).

Diff renderer with all five statuses, manually verified during Chunk 4 substep 4b with a six-case rendering test, results inspected by eye and discarded after confirmation.

Agent write-flow with rejection, integration test, test_agent_verify.py test_rejection_after_verification_blocks_apply.

Agent write-flow with approval, integration test, test_agent_verify.py test_clean_verification_approval_applies.

Agent read-flow regression, smoke test executed during Chunk 4, no automated test in this phase (Phase 3 test_agent.py still covers read behavior).

STATUS_UNAVAILABLE handling, manual smoke test, Chunk 5 Test A, not in automated suite per design.

STATUS_CRITICAL handling, manual smoke test for renderer plus unit-test-level coverage for classification logic, not in automated suite per design.

IP overlap detection by Batfish, manual smoke test, Chunk 5 Test B, documented as a finding rather than a capability.

Total automated tests: 13 unit tests in test_snapshot_builder.py plus 4 integration tests in test_agent_verify.py. Total automated runtime: under 20 seconds.

## Engineering notes

This section records key decisions made during Phase 5 development.

Path A versus Path B for the missing frr.conf. The SONiC VS container did not have a real frr.conf because BGP was never configured. Two paths were considered: Path A (provide a stub frr.conf to Batfish), Path B (skip Batfish entirely and write Python-only verification). Path A was tried first with a five-minute time budget. It succeeded: Batfish accepted the stub and parsed the snapshot. If it had failed, Path B was the agreed pivot. The honest engineering story is that the integration works with the tool's real capabilities rather than being a silent workaround. Documented in the Known Limitations section above.

Subprocess testing versus mocking. For test_agent_verify.py, three options were considered: monkeypatching the verifier module inside tests (Option A), pure subprocess tests against live Batfish (Option B), and refactoring the agent for dependency injection (Option C). Option B was chosen because mocking would test the agent against fake verification results rather than actual integration with Batfish, defeating the purpose of having Phase 5 tests. Option C is a real refactor and was deemed out of scope for late-phase work. The trade-off is that the automated suite cannot directly exercise STATUS_UNAVAILABLE or STATUS_CRITICAL, both of which require either breaking the live system or constructing a scenario that the live tool will not produce. Those gaps are documented in the coverage matrix and known limitations.

The ARM64 emulation history. The first attempt at running Batfish used the batfish/allinone:latest image. That tag was AMD64-only at the time of Phase 5 development; the container ran under Apple's Rosetta translation layer, which was slow and produced occasional segfaults under sustained load. Switching to batfish/allinone:test-2026.04.01.3234, which has a native ARM64 build, eliminated the emulation overhead. The :latest tag had been 10 months out of date and lacked the ARM64 manifest. Documented for future readers who might hit the same trap.

Pure transformation versus combined module for snapshot_builder. The snapshot_builder module was deliberately split into a pure transformation half (apply_plan_to_config_db and three private helpers, dependency-free) and an I/O half (sonic-cfggen, file system, stub frr.conf). The split lets the pure half have 13 fast unit tests that run in milliseconds without any container or Batfish dependency. The cost is mild stylistic awkwardness: the imports for the I/O half are not at the top of the file. The cost was judged acceptable in exchange for the testing isolation.

Five-status VerificationResult versus boolean. An earlier design considered returning just a boolean "ok or not" from the verifier. The five-status design was chosen because TIMEOUT and UNAVAILABLE are distinct from CRITICAL in user-facing meaning. A boolean would conflate "Batfish said this is broken" with "Batfish could not say anything." The five-status design also makes the diff renderer's job easier: each status has a distinct rendering rather than the renderer having to inspect a flag plus a message string.

What is NOT in Phase 5. BGP verification, multi-device topology analysis, custom Batfish questions beyond initIssues, automatic remediation of warnings, and Python-side overlap detection were all considered and explicitly deferred. The Phase 5 scope was pre-apply verification of single-switch interface-level changes, and adding any of the above would have expanded scope past what was promised in the original phase plan.
