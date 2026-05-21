"""Phase 4 integration tests for the write flow.

Tests the five Phase 4 success criteria by launching agent.py as a
subprocess, piping stdin for approval/rejection, and asserting on stdout
and on CONFIG_DB state.

Preconditions:
    The SONiC container sonic-vs-fixed must be running.
    The Ollama service must be running with qwen2.5:7b-instruct.
    Tests use Ethernet20 and Ethernet24 (interfaces not touched by the
    fixture) so they do not interfere with read-test state.

Each test is slow (LLM call + subprocess overhead). The full suite
takes 30-60 seconds. Tests clean up their own state on success; if a
test fails mid-flight, manual cleanup of test interfaces may be needed.
"""

import subprocess
import sys
import unittest

import sonic_client

AGENT_TEST_INTERFACE_A = "Ethernet20"
AGENT_TEST_INTERFACE_B = "Ethernet24"
AGENT_TEST_IP_A = "10.20.0.1/24"
AGENT_TEST_IP_B = "10.24.0.1/24"
SUBPROCESS_TIMEOUT_SECONDS = 60


def _run_agent(question: str, stdin_input: str) -> subprocess.CompletedProcess:
    """Run agent.py as a subprocess with given question and stdin.

    Args:
        question: the agent question, passed as a CLI argument.
        stdin_input: text to pipe to the agent's stdin (e.g. "y\n").

    Returns:
        The completed subprocess result. Callers can inspect stdout,
        stderr, and returncode.
    """
    return subprocess.run(
        [sys.executable, "agent.py", question],
        input=stdin_input,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


def _cleanup_interface(interface_name: str, ip_address: str) -> None:
    """Remove an IP from an interface if present. Best-effort, no raise."""
    try:
        current = sonic_client.get_interface_ip(interface_name)
    except sonic_client.SonicClientError:
        return
    if current == ip_address:
        try:
            sonic_client.apply_remove_interface_ip(interface_name, ip_address)
        except sonic_client.SonicClientError:
            pass


class TestCriterion1ProposesChange(unittest.TestCase):
    """Criterion 1: agent proposes a change in response to a write request."""

    def test_add_ip_request_produces_proposal(self) -> None:
        """A 'configure with IP' request triggers a Proposed change section."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="n\n",
        )
        self.assertIn("Proposed change:", result.stdout)
        self.assertIn(AGENT_TEST_INTERFACE_A, result.stdout)
        self.assertIn(AGENT_TEST_IP_A, result.stdout)


class TestCriterion2DiffShownFirst(unittest.TestCase):
    """Criterion 2: diff appears before any modification."""

    def test_diff_contains_logical_and_concrete_sections(self) -> None:
        """The output diff has both logical and concrete sections."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="n\n",
        )
        self.assertIn("Proposed change:", result.stdout)
        self.assertIn("Commands that will run:", result.stdout)
        self.assertIn("Predicted CONFIG_DB changes:", result.stdout)


class TestCriterion3NoWriteWithoutApproval(unittest.TestCase):
    """Criterion 3: no write happens without explicit approval."""

    def test_rejection_does_not_modify_state(self) -> None:
        """Piping 'n' results in no CONFIG_DB change."""
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)
        before_keys = set(sonic_client.list_interface_keys())

        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="n\n",
        )

        after_keys = set(sonic_client.list_interface_keys())
        self.assertEqual(before_keys, after_keys)
        self.assertIn("Change rejected", result.stdout)

    def test_eof_treated_as_rejection(self) -> None:
        """Empty stdin (EOF) results in no CONFIG_DB change."""
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)
        before_keys = set(sonic_client.list_interface_keys())

        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="",
        )

        after_keys = set(sonic_client.list_interface_keys())
        self.assertEqual(before_keys, after_keys)
        self.assertIn("Change rejected", result.stdout)


class TestCriterion4ApprovalApplies(unittest.TestCase):
    """Criterion 4: after approval, change applies and is verified."""

    def setUp(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_B, AGENT_TEST_IP_B)

    def tearDown(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_B, AGENT_TEST_IP_B)

    def test_approval_applies_change(self) -> None:
        """Piping 'y' applies the change and reports post-apply state."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_B} with IP {AGENT_TEST_IP_B}",
            stdin_input="y\n",
        )
        self.assertIn("Change applied", result.stdout)
        self.assertIn("Post-apply state", result.stdout)
        actual_ip = sonic_client.get_interface_ip(AGENT_TEST_INTERFACE_B)
        self.assertEqual(actual_ip, AGENT_TEST_IP_B)


class TestCriterion5ErrorsHandledCleanly(unittest.TestCase):
    """Criterion 5: apply failures and rejections both handled cleanly."""

    def test_remove_nonexistent_ip_reports_error(self) -> None:
        """Trying to remove an IP that isn't configured fails gracefully."""
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)
        result = _run_agent(
            f"Remove IP {AGENT_TEST_IP_A} from {AGENT_TEST_INTERFACE_A}",
            stdin_input="y\n",
        )
        # Either the LLM declined to propose (no proposal section) OR
        # the apply step failed cleanly. Both are acceptable graceful
        # handling. What we MUST NOT see is a Python traceback.
        self.assertNotIn("Traceback", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
