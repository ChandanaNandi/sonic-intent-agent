"""Phase 5 integration tests for the verification flow.

Tests the four Phase 5 behaviors that can be exercised end-to-end against
live SONiC and a live Batfish service:
    1. Verification section appears in the diff for write requests
    2. Verification runs before the approval prompt
    3. Approval after clean verification applies the change
    4. Rejection after verification blocks the change

Preconditions:
    The SONiC container sonic-vs-fixed must be running with the Phase 3
    fixture applied.
    The Ollama service must be running with qwen2.5:7b-instruct.
    The Batfish container must be running (docker start batfish).
    Tests use Ethernet28 and Ethernet32 so they do not collide with
    fixture interfaces or Phase 4 test interfaces.

Each test launches agent.py as a subprocess. Verification is real (Batfish
parses both current and candidate snapshots). Total suite runtime is
roughly 30-60 seconds.

Coverage gaps that are NOT in this automated suite:
    - STATUS_UNAVAILABLE handling. Requires stopping Batfish, which would
      race with other test runs and is hard to clean up reliably. Verified
      manually in Chunk 5 Test A.
    - STATUS_CRITICAL handling. Cannot synthesize a critical result
      against a clean live SONiC without breaking the container. The
      rendering and classification paths are covered by unit tests on
      diff_renderer and verifier respectively. The agent integration
      path is structurally the same as STATUS_WARNINGS.

Run:
    python3 -m unittest test_agent_verify.py -v
"""

import subprocess
import sys
import unittest

import sonic_client

AGENT_TEST_INTERFACE_A = "Ethernet28"
AGENT_TEST_INTERFACE_B = "Ethernet32"
AGENT_TEST_IP_A = "10.28.0.1/24"
AGENT_TEST_IP_B = "10.32.0.1/24"
SUBPROCESS_TIMEOUT_SECONDS = 120


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


class TestVerificationInDiff(unittest.TestCase):
    """Verification section appears in the rendered diff for write requests."""

    def setUp(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)

    def tearDown(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)

    def test_verification_section_appears_in_diff(self) -> None:
        """The diff contains a 'Pre-apply verification:' header."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="n\n",
        )
        self.assertIn("Pre-apply verification:", result.stdout)


class TestVerificationOrdering(unittest.TestCase):
    """Verification result is shown before the approval outcome."""

    def setUp(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)

    def tearDown(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)

    def test_verification_runs_before_approval_prompt(self) -> None:
        """Pre-apply verification section appears before 'Change rejected'."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="n\n",
        )
        verification_index = result.stdout.find("Pre-apply verification:")
        rejection_index = result.stdout.find("Change rejected")
        self.assertGreaterEqual(
            verification_index,
            0,
            "Pre-apply verification section missing from stdout",
        )
        self.assertGreater(
            rejection_index,
            verification_index,
            "rejection message appeared before verification section",
        )


class TestCleanVerificationApplies(unittest.TestCase):
    """Approval after clean verification applies the change."""

    def setUp(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_B, AGENT_TEST_IP_B)

    def tearDown(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_B, AGENT_TEST_IP_B)

    def test_clean_verification_approval_applies(self) -> None:
        """y approval results in 'Change applied.' and the IP is configured."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_B} with IP {AGENT_TEST_IP_B}",
            stdin_input="y\n",
        )
        self.assertIn("Change applied", result.stdout)
        actual_ip = sonic_client.get_interface_ip(AGENT_TEST_INTERFACE_B)
        self.assertEqual(actual_ip, AGENT_TEST_IP_B)


class TestRejectionBlocksApply(unittest.TestCase):
    """Rejection after verification blocks the change."""

    def setUp(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)

    def tearDown(self) -> None:
        _cleanup_interface(AGENT_TEST_INTERFACE_A, AGENT_TEST_IP_A)

    def test_rejection_after_verification_blocks_apply(self) -> None:
        """n rejection results in 'Change rejected' and IP NOT configured."""
        result = _run_agent(
            f"Configure {AGENT_TEST_INTERFACE_A} with IP {AGENT_TEST_IP_A}",
            stdin_input="n\n",
        )
        self.assertIn("Change rejected", result.stdout)
        actual_ip = sonic_client.get_interface_ip(AGENT_TEST_INTERFACE_A)
        self.assertIsNone(
            actual_ip,
            f"IP should not be configured on {AGENT_TEST_INTERFACE_A} "
            f"after rejection, got {actual_ip}",
        )


if __name__ == "__main__":
    unittest.main()
