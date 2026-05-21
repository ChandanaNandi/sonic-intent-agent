Phase 6: Post-Apply Verification

This phase completes the propose-verify-approve-apply-verify chain. After
the user approves a change and the agent applies it, the agent now
verifies that the predicted CONFIG_DB-level effects of the change
actually materialized in live SONiC state. On a clean CONFIG_DB match,
a light Batfish re-read confirms parser invariants still hold.

Phase 6 sits at the end of the 7-phase project:

    Phase 1: SONiC virtual switch in Docker
    Phase 2: Local LLM via Ollama
    Phase 3: Connect LLM to SONiC, read-only queries
    Phase 4: Configuration changes with diff preview and approval
    Phase 5: Pre-apply Batfish verification
    Phase 6: Post-apply verification (this phase)
    Phase 7: Polish, demo, eval harness

For setup details on Docker, the SONiC container, Ollama, and the
Batfish service, see ../phase5/README.md. Those contracts have not
changed in Phase 6.


What changed from Phase 5

Phase 5's write-flow:

    propose -> pre-apply verify -> render diff -> approve -> apply
    -> _report_post_apply_state -> done

Phase 6's write-flow:

    propose -> pre-apply verify -> render diff -> approve -> apply
    -> wait_for_settled -> check_plan_applied
    -> (optional Batfish re-read on success only) -> render_post_apply

The Phase 4 helper _report_post_apply_state was removed entirely. It
only reported IP-add results meaningfully and had no useful output for
admin-status changes. The new flow handles all three operations
(add IP, remove IP, set admin status) with structured per-prediction
verdicts.

The pre-apply path is structurally untouched. Phase 5 integration tests
pass on Phase 6 code without modification, which is the regression
contract.


Module descriptions

post_apply_check.py (new, 268 lines)
    Pure-Python module that takes a ChangePlan and a live CONFIG_DB
    dict and produces a PostApplyResult with per-prediction verdicts.
    Three status constants (POST_APPLY_SUCCESS, POST_APPLY_PARTIAL_FAILURE,
    POST_APPLY_COMPLETE_FAILURE). Three verdict constants
    (VERDICT_PRESENT, VERDICT_ABSENT, VERDICT_UNEXPECTED_VALUE). Three
    frozen dataclasses (KeyVerdict, PostApplyResult, WaitResult).

    Two public functions:
        check_plan_applied(plan, live_config_db) -> PostApplyResult
        wait_for_settled(plan, config_db_fetcher, timeout, poll) -> WaitResult

    The check function is pure data transformation. The wait helper
    takes a callable for fetching, so the module has no SONiC dependency
    and is fully unit-testable with synthetic input.

change_plan.py (extended)
    Added the PredictedKey frozen dataclass (operation, table, key,
    field_name, expected_value). Added three operation constants
    (PREDICTED_KEY_ADDED, PREDICTED_KEY_MODIFIED, PREDICTED_KEY_REMOVED).
    Added predicted_keys field on ChangePlan, defaulting to an empty
    list for backward compatibility. Validation in __post_init__ rejects
    invalid combinations (added with field_name, modified without
    field_name, unknown operation strings).

tools.py (extended)
    Moved change_plan imports to the module top. The three propose
    functions (propose_add_interface_ip, propose_remove_interface_ip,
    propose_set_interface_admin_status) now populate the structured
    predicted_keys field on their ChangePlan output. The existing
    string-form predicted_config_db_changes is preserved unchanged for
    the renderer.

diff_renderer.py (extended)
    Added render_post_apply(result) -> str that produces the post-apply
    verification block. Success case uses lowercase terse phrasing
    matching Phase 5's verification renderer. Partial and complete
    failure cases use uppercase headers ("PARTIAL FAILURE",
    "COMPLETE FAILURE") and per-verdict lines with OK or FAIL prefixes.
    The table|key column is padded to a consistent width so failure
    detail strings line up.

agent.py (rewired)
    Replaced _report_post_apply_state with two new helpers:
        _post_apply_batfish_recheck: runs the light Batfish re-read
        _post_apply_verify: orchestrates wait -> check -> recheck -> render
    The write-flow branch now calls _post_apply_verify after a
    successful apply. Pre-apply verification and approval/rejection
    logic are unchanged from Phase 5.


Prerequisites

Same as Phase 5 (see ../phase5/README.md for Docker, SONiC fixture,
Ollama model, Batfish container setup). Phase 6 introduces no new
external services.

Phase 6 development venv lives in phase6/.venv. The dependencies are
the same as Phase 5: pybatfish, ollama, redis (for sonic_client), and
the standard library.


Setup

From the project root:

    cd phase6
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

The Phase 5 SONiC fixture, Batfish container, and Ollama model must be
running. Verify with:

    docker ps | grep -E "sonic-vs-fixed|batfish"
    ollama list | grep qwen2.5

If anything is missing, the relevant ../phase5/README.md setup section
covers the recovery steps.


Usage examples

Read query (Phase 3 behavior, unchanged):

    python3 agent.py "What IP is configured on Ethernet0?"

Output:
    The IP address configured on Ethernet0 is 10.0.0.1/24.

Write request with approval, successful add:

    python3 agent.py "Configure Ethernet20 with IP 10.20.0.1/24"

Output (abbreviated; pre-apply diff section omitted for brevity):

    Running pre-apply verification...
    Proposed change:
      Add IP 10.20.0.1/24 to interface Ethernet20
    [...]
    Pre-apply verification:
      no new issues introduced (1.7s)
    Approve this change? [y/N]: y
    Change applied.
    Running post-apply verification...
    Post-apply verification:
      all 2 predicted CONFIG_DB change(s) verified (0.33s)
      Post-apply Batfish re-read: clean parse, no critical issues (0.63s)

Write request with approval, successful remove:

    python3 agent.py "Remove the IP 10.20.0.1/24 from Ethernet20"

Output (post-apply section only):

    Post-apply verification:
      all 1 predicted CONFIG_DB change(s) verified (0.32s)
      Post-apply Batfish re-read: clean parse, no critical issues (0.58s)

The "1 predicted CONFIG_DB change(s)" phrasing keeps the plural-hedge
in parentheses for grammatical symmetry across counts. Special-casing
total=1 was rejected as over-engineering.

Write request with rejection (Phase 4 behavior, unchanged):

    python3 agent.py "Configure Ethernet24 with IP 10.24.0.1/24"
    [...]
    Approve this change? [y/N]: n
    Change rejected. No modifications made.

When the user rejects, no apply runs, no post-apply check runs.


Verification status references

Pre-apply verification (Phase 5, unchanged):
    STATUS_OK            no new issues introduced by the candidate state
    STATUS_WARNINGS      new non-critical issues found
    STATUS_CRITICAL      new critical issues found
    STATUS_TIMEOUT       Batfish parse exceeded timeout budget
    STATUS_UNAVAILABLE   Batfish session could not be opened

See ../phase5/README.md for the full Phase 5 status semantics.

Post-apply verification (Phase 6, new):
    POST_APPLY_SUCCESS            every predicted CONFIG_DB-level effect
                                  materialized as expected
    POST_APPLY_PARTIAL_FAILURE    some but not all predicted effects
                                  materialized
    POST_APPLY_COMPLETE_FAILURE   no predicted effects materialized

Per-key verdicts on individual PredictedKeys:
    VERDICT_PRESENT            the key exists in CONFIG_DB
    VERDICT_ABSENT             the key does not exist
    VERDICT_UNEXPECTED_VALUE   the key exists but the modified field
                               value does not match the expected value

The verdict labels are interpreted with respect to the predicted_key's
operation:
    operation == "added"     success means VERDICT_PRESENT
    operation == "removed"   success means VERDICT_ABSENT
    operation == "modified"  success means VERDICT_PRESENT (with the
                             field at the expected value)


Known limitations

LLM tool-call format flakiness
    The qwen2.5:7b-instruct model occasionally emits tool calls as text
    in its response rather than as structured function calls. Observed
    once during Phase 6 manual testing on a "Remove the IP X from Y"
    request that worked on retry with identical phrasing. The agent has
    no proposed_plan to process when this happens and exits silently.
    This is a model-side reliability issue, not an agent code defect.
    Larger models (qwen2.5:14b-instruct or 32B and above) would be more
    reliable at the cost of inference latency.

CONFIG_DB read-after-write lag
    Phase 6 Chunk 1 measured a 60-80ms delay between sonic-cfggen
    apply returning and the corresponding CONFIG_DB read reflecting the
    change. The wait_for_settled helper accommodates this with a
    bounded poll loop (2.0 second timeout, 20ms poll interval).
    Without the wait helper, post-apply checks would unreliably report
    POST_APPLY_PARTIAL_FAILURE for changes that did in fact apply.

Light vs deep Batfish re-read
    The post-apply Batfish re-read confirms the live state still parses
    without critical issues. It does NOT diff post-apply warnings
    against pre-apply warnings. The pre-apply verification already
    surfaces warnings before approval; the re-read is a sanity check on
    parser invariants after apply. A deep diff path is feasible but
    adds state-management complexity for marginal benefit.

Batfish does not flag overlapping IP assignments
    Inherited from Phase 5. Configuring two interfaces with overlapping
    subnets (e.g., Ethernet0 with 10.0.0.1/24 and Ethernet20 with
    10.0.0.99/24) parses cleanly through Batfish init. See
    ../phase5/README.md for the test evidence and discussion.

Integration tests cover success paths only
    Partial failure and complete failure scenarios cannot be
    synthesized reliably against live SONiC without breaking the
    container in ways that complicate the rest of the project. They are
    unit-tested in test_post_apply_check.py with synthetic CONFIG_DB
    dicts that exercise every status and verdict combination. The
    integration tests cover only the success paths, which is what live
    infrastructure produces in normal operation. This is a deliberate
    scope decision, not a coverage gap.


Coverage matrix

Total: 45 automated tests, 43.5 second combined runtime.

test_change_plan.py (8 unit tests)
    PredictedKey validation: 5 tests covering added/modified/removed
    happy paths and rejection of invalid combinations.
    Propose-function output: 3 tests verifying tools.py populates
    predicted_keys correctly for all three operations.

test_post_apply_check.py (16 unit tests)
    check_plan_applied: 12 tests covering 3 operations x 3 outcomes
    (success, partial failure, complete failure) plus edge cases
    (empty predicted_keys, missing tables, modified key with wrong
    actual value, mixed-key plans).
    wait_for_settled: 4 tests using a fake fetcher to simulate
    immediate settle, late settle, timeout, and the no-predicted-keys
    shortcut.

test_snapshot_builder.py (13 unit tests)
    Phase 5 regression: copied from phase5/ and run unchanged.
    Confirms Phase 6's ChangePlan refactor and predicted_keys field
    did not break Phase 5 snapshot construction.

test_agent_verify.py (4 integration tests)
    Phase 5 regression: copied from phase5/ and run unchanged.
    Confirms Phase 6's agent.py rewiring did not break Phase 5's
    pre-apply verification, approval prompt, or rejection paths.

test_agent_post_apply.py (4 integration tests)
    Post-apply section appears for write request.
    Successful add produces "all 2 predicted CONFIG_DB change(s)
    verified" message.
    Successful apply triggers the Batfish re-read one-liner.
    Successful remove produces "all 1 predicted CONFIG_DB change(s)
    verified" message.


Engineering notes

Post-apply semantics: state-check, not diff-since-apply
    check_plan_applied compares predicted_keys against the live
    CONFIG_DB as-observed-now. It does NOT snapshot CONFIG_DB before
    apply and compare delta. State-check is simpler, requires no extra
    state to manage, and is honest about what the user sees: "this is
    the state right now, here is what we predicted, here is the
    match." Diff-since-apply would require holding the pre-apply
    snapshot in memory and runs into edge cases (what if another
    process modifies CONFIG_DB between snapshot and apply?). The
    state-check approach trades one form of precision for substantial
    simplicity.

Light Batfish re-read fast-path
    The re-read runs only when check_plan_applied returns
    POST_APPLY_SUCCESS. On partial or complete failure, we already
    know the local state is wrong; paying 0.5 to 1.0 seconds for
    Batfish to confirm what we know would only delay the user seeing
    the failure verdict. The fast-path keeps successful-apply latency
    bounded (~0.6 second re-read) and skips it entirely on failure.

CONFIG_DB read-after-write timing measurement
    Phase 6 Chunk 1 ran 5 iterations of write-then-immediate-read
    against a fresh interface. wait_for_key returned in 61-79ms across
    all iterations (mean 72ms). This is SONiC-internal lag between
    sonic-cfggen completion and the CONFIG_DB key becoming readable.
    The 2.0 second wait_for_settled timeout is conservative by
    roughly 25x; in practice the wait completes in under 100ms on a
    healthy system. The 2.0 second budget protects against degraded
    states without slowing the happy path.

LLM nondeterminism on tool-call formatting
    During Phase 6 manual testing, qwen2.5:7b-instruct emitted a tool
    call as text once during a remove request, then handled the same
    phrasing correctly on immediate retry. Subsequently observed one
    additional occurrence during the Phase 7 polish verification
    (a regression test run). Both incidents resolved on retry without
    code changes. This kind of nondeterminism
    is documented in the Ollama and qwen2.5 model card discussions. A
    production agent would likely switch to a larger model or add a
    parser fallback to recover text-formatted tool calls. For this
    portfolio project, the rate of occurrence is low enough that
    retry-on-failure is an acceptable user experience.

Removal of _report_post_apply_state
    The Phase 4 helper produced a single line of human-readable text
    after apply (e.g., "Post-apply state: Ethernet20 has IP
    10.20.0.1/24"). It had three shortcomings: it only worked
    meaningfully for the IP-add case, it duplicated information the
    user could derive from the predicted_changes section, and it
    bypassed the structured verification that Phase 6 introduces.
    Removing it entirely was simpler than keeping two parallel
    post-apply paths. The equivalent information is now implicit in
    the structured post-apply verdict output.

Why integration tests cover only success paths
    See the corresponding entry under Known limitations. The short
    version: synthesizing partial-apply or complete-failure scenarios
    against live SONiC requires breaking the container in ways that
    interfere with the rest of the test suite and the demo. Unit tests
    in test_post_apply_check.py exercise every status and verdict
    combination with synthetic CONFIG_DB dicts, which gives stronger
    structural coverage than a flaky integration test could.
