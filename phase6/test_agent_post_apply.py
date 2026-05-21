"""Phase 6 integration tests for the post-apply verification flow.

Tests the four Phase 6 behaviors that can be exercised end-to-end against
live SONiC and a live Batfish service:
    1. Post-apply verification section appears in agent output
    2. Successful apply produces the 'all N verified' message
    3. Successful apply includes the Batfish re-read one-liner
    4. Successful remove produces the matching 'all 1 verified' message

Preconditions:
    - sonic-vs-fixed container running with Phase 3 fixture
    - Ollama with qwen2.5:7b-instruct
    - Batfish container running

Test interfaces: Ethernet36 and Ethernet40 (no collision with Phase 4's
Ethernet20/24 or Phase 5's Ethernet28/32).

Each test runs the full pipeline: LLM tool call, pre-apply Batfish
verification, apply, wait_for_settled, check_plan_applied, post-apply
Batfish re-read, render_post_apply. Suite runtime is roughly 30-45
seconds.

Coverage gaps NOT in this suite, per Phase 6 scope:
    - Partial failure and complete failure cases. Cannot synthesize
      reliably against a live SONiC. Covered by unit tests in
      test_post_apply_check.py with synthetic input dicts.
    - Post-apply Batfish re-read failure cases. Same reasoning.
    - wait_for_settled timeout case. Same reasoning.

Run:
    python3 -m unittest test_agent_post_apply.py -v
"""

import subprocess
import sys
import unittest

import sonic_client

TEST_INTERFACE_ADD = "Ethernet36"
TEST_IP_ADD = "10.36.0.1/24"
TEST_INTERFACE_REMOVE = "Ethernet40"
TEST_IP_REMOVE = "10.40.0.1/24"
SUBPROCESS_TIMEOUT_SECONDS = 120


def _run_agent(question: str, stdin_input: str) -> subprocess.CompletedProcess:
    """Run agent.py as a subprocess with given question and stdin."""
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


def _seed_interface(interface_name: str, ip_address: str) -> None:
    """Add an IP to an interface so a subsequent remove test has work to do."""
    try:
        current = sonic_client.get_interface_ip(interface_name)
    except sonic_client.SonicClientError:
        current = None
    if current != ip_address:
        sonic_client.apply_add_interface_ip(interface_name, ip_address)


class TestPostApplySectionAppears(unittest.TestCase):
    """The post-apply verification block must appear in agent stdout."""

    def setUp(self) -> None:
        _cleanup_interface(TEST_INTERFACE_ADD, TEST_IP_ADD)

    def tearDown(self) -> None:
        _cleanup_interface(TEST_INTERFACE_ADD, TEST_IP_ADD)

    def test_post_apply_section_appears_for_write_request(self) -> None:
        """Approved write produces 'Post-apply verification:' in stdout."""
        result = _run_agent(
            f"Configure {TEST_INTERFACE_ADD} with IP {TEST_IP_ADD}",
            stdin_input="y\n",
        )
        self.assertIn("Post-apply verification:", result.stdout)


class TestPostApplySuccessForAdd(unittest.TestCase):
    """A clean add must produce the 'all N verified' success message."""

    def setUp(self) -> None:
        _cleanup_interface(TEST_INTERFACE_ADD, TEST_IP_ADD)

    def tearDown(self) -> None:
        _cleanup_interface(TEST_INTERFACE_ADD, TEST_IP_ADD)

    def test_post_apply_success_for_clean_add(self) -> None:
        """Add IP success: stdout shows all 2 predicted changes verified."""
        result = _run_agent(
            f"Configure {TEST_INTERFACE_ADD} with IP {TEST_IP_ADD}",
            stdin_input="y\n",
        )
        self.assertIn(
            "all 2 predicted CONFIG_DB change(s) verified",
            result.stdout,
            f"expected success message in stdout. Full stdout:\n{result.stdout}",
        )


class TestPostApplyIncludesBatfishRecheck(unittest.TestCase):
    """A successful post-apply check must trigger the Batfish re-read."""

    def setUp(self) -> None:
        _cleanup_interface(TEST_INTERFACE_ADD, TEST_IP_ADD)

    def tearDown(self) -> None:
        _cleanup_interface(TEST_INTERFACE_ADD, TEST_IP_ADD)

    def test_post_apply_includes_batfish_recheck(self) -> None:
        """Successful apply triggers the Batfish re-read one-liner."""
        result = _run_agent(
            f"Configure {TEST_INTERFACE_ADD} with IP {TEST_IP_ADD}",
            stdin_input="y\n",
        )
        self.assertIn("Post-apply Batfish re-read:", result.stdout)


class TestPostApplySuccessForRemove(unittest.TestCase):
    """A clean remove must produce 'all 1 verified' success message."""

    def setUp(self) -> None:
        _cleanup_interface(TEST_INTERFACE_REMOVE, TEST_IP_REMOVE)
        _seed_interface(TEST_INTERFACE_REMOVE, TEST_IP_REMOVE)

    def tearDown(self) -> None:
        _cleanup_interface(TEST_INTERFACE_REMOVE, TEST_IP_REMOVE)

    def test_post_apply_success_for_clean_remove(self) -> None:
        """Remove IP success: stdout shows all 1 predicted change verified."""
        result = _run_agent(
            f"Remove the IP {TEST_IP_REMOVE} from {TEST_INTERFACE_REMOVE}",
            stdin_input="y\n",
        )
        self.assertIn(
            "all 1 predicted CONFIG_DB change(s) verified",
            result.stdout,
            f"expected success message in stdout. Full stdout:\n{result.stdout}",
        )


if __name__ == "__main__":
    unittest.main()
