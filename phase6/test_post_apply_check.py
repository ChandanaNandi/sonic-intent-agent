"""Unit tests for post_apply_check.

Two test groups:
    - TestCheckPlanApplied: 9 happy-path tests (3 operations x 3 outcomes)
      plus 3 edge case tests covering empty predicted_keys, missing tables,
      and modified keys with wrong actual values.
    - TestWaitForSettled: 4 tests using a fake fetcher to simulate
      immediate settle, late settle, timeout, and the no-predicted-keys
      shortcut.

All tests are pure Python: no SONiC, no Docker, no live system.

Run:
    python3 -m unittest test_post_apply_check.py -v
"""

import unittest

from change_plan import (
    ChangePlan,
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
    PREDICTED_KEY_ADDED,
    PREDICTED_KEY_MODIFIED,
    PREDICTED_KEY_REMOVED,
    PredictedKey,
)
from post_apply_check import (
    POST_APPLY_COMPLETE_FAILURE,
    POST_APPLY_PARTIAL_FAILURE,
    POST_APPLY_SUCCESS,
    VERDICT_ABSENT,
    VERDICT_PRESENT,
    VERDICT_UNEXPECTED_VALUE,
    check_plan_applied,
    wait_for_settled,
)


def _make_add_plan(interface: str, ip: str) -> ChangePlan:
    """Build a ChangePlan for adding an IP, with structured predicted_keys."""
    return ChangePlan(
        operation=OPERATION_ADD_IP,
        target=interface,
        parameters={"ip_address": ip},
        commands=[["config", "interface", "ip", "add", interface, ip]],
        description=f"Add IP {ip} to {interface}",
        predicted_keys=[
            PredictedKey(
                operation=PREDICTED_KEY_ADDED,
                table="INTERFACE",
                key=interface,
            ),
            PredictedKey(
                operation=PREDICTED_KEY_ADDED,
                table="INTERFACE",
                key=f"{interface}|{ip}",
            ),
        ],
    )


def _make_remove_plan(interface: str, ip: str) -> ChangePlan:
    """Build a ChangePlan for removing an IP."""
    return ChangePlan(
        operation=OPERATION_REMOVE_IP,
        target=interface,
        parameters={"ip_address": ip},
        commands=[["config", "interface", "ip", "remove", interface, ip]],
        description=f"Remove IP {ip} from {interface}",
        predicted_keys=[
            PredictedKey(
                operation=PREDICTED_KEY_REMOVED,
                table="INTERFACE",
                key=f"{interface}|{ip}",
            ),
        ],
    )


def _make_set_admin_plan(interface: str, status: str) -> ChangePlan:
    """Build a ChangePlan for setting admin status."""
    subcommand = "startup" if status == "up" else "shutdown"
    return ChangePlan(
        operation=OPERATION_SET_ADMIN,
        target=interface,
        parameters={"admin_status": status},
        commands=[["config", "interface", subcommand, interface]],
        description=f"Set {interface} admin status to {status}",
        predicted_keys=[
            PredictedKey(
                operation=PREDICTED_KEY_MODIFIED,
                table="PORT",
                key=interface,
                field_name="admin_status",
                expected_value=status,
            ),
        ],
    )


class TestCheckPlanApplied(unittest.TestCase):
    """check_plan_applied covers all three operations and three outcomes."""

    def test_add_success(self) -> None:
        """Add IP: both predicted keys present -> success."""
        plan = _make_add_plan("Ethernet12", "192.168.1.1/24")
        live = {
            "INTERFACE": {
                "Ethernet12": {},
                "Ethernet12|192.168.1.1/24": {},
            }
        }
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_SUCCESS)
        self.assertEqual(len(result.verdicts), 2)
        for verdict in result.verdicts:
            self.assertEqual(verdict.verdict, VERDICT_PRESENT)

    def test_add_partial_failure(self) -> None:
        """Add IP: marker present but IP key absent -> partial failure."""
        plan = _make_add_plan("Ethernet12", "192.168.1.1/24")
        live = {"INTERFACE": {"Ethernet12": {}}}
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_PARTIAL_FAILURE)
        self.assertEqual(result.verdicts[0].verdict, VERDICT_PRESENT)
        self.assertEqual(result.verdicts[1].verdict, VERDICT_ABSENT)

    def test_add_complete_failure(self) -> None:
        """Add IP: neither key present -> complete failure."""
        plan = _make_add_plan("Ethernet12", "192.168.1.1/24")
        live = {"INTERFACE": {}}
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_COMPLETE_FAILURE)
        for verdict in result.verdicts:
            self.assertEqual(verdict.verdict, VERDICT_ABSENT)

    def test_remove_success(self) -> None:
        """Remove IP: target key absent -> success."""
        plan = _make_remove_plan("Ethernet0", "10.0.0.1/24")
        live = {"INTERFACE": {"Ethernet0": {}}}
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_SUCCESS)
        self.assertEqual(result.verdicts[0].verdict, VERDICT_ABSENT)

    def test_remove_partial_failure(self) -> None:
        """Remove IP with only one predicted key: complete failure if still present."""
        plan = _make_remove_plan("Ethernet0", "10.0.0.1/24")
        live = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        result = check_plan_applied(plan, live)
        # With one predicted key and zero matches, this is complete_failure,
        # not partial. partial_failure requires both matches > 0 and < total.
        self.assertEqual(result.overall_status, POST_APPLY_COMPLETE_FAILURE)

    def test_remove_complete_failure(self) -> None:
        """Remove IP: target key still present -> complete failure."""
        plan = _make_remove_plan("Ethernet0", "10.0.0.1/24")
        live = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_COMPLETE_FAILURE)
        self.assertEqual(result.verdicts[0].verdict, VERDICT_PRESENT)

    def test_modified_success(self) -> None:
        """Set admin status: PORT row has expected value -> success."""
        plan = _make_set_admin_plan("Ethernet0", "down")
        live = {
            "PORT": {
                "Ethernet0": {"admin_status": "down", "speed": "100000"},
            }
        }
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_SUCCESS)
        self.assertEqual(result.verdicts[0].verdict, VERDICT_PRESENT)

    def test_modified_partial_failure_with_multiple_keys(self) -> None:
        """A plan with mixed outcomes among multiple predicted keys."""
        plan = ChangePlan(
            operation=OPERATION_ADD_IP,
            target="Ethernet12",
            parameters={"ip_address": "192.168.1.1/24"},
            commands=[["config", "interface", "ip", "add",
                       "Ethernet12", "192.168.1.1/24"]],
            description="Add IP",
            predicted_keys=[
                PredictedKey(
                    operation=PREDICTED_KEY_ADDED,
                    table="INTERFACE",
                    key="Ethernet12",
                ),
                PredictedKey(
                    operation=PREDICTED_KEY_MODIFIED,
                    table="PORT",
                    key="Ethernet12",
                    field_name="admin_status",
                    expected_value="up",
                ),
            ],
        )
        live = {
            "INTERFACE": {"Ethernet12": {}},
            "PORT": {"Ethernet12": {"admin_status": "down"}},
        }
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_PARTIAL_FAILURE)
        self.assertEqual(result.verdicts[0].verdict, VERDICT_PRESENT)
        self.assertEqual(
            result.verdicts[1].verdict, VERDICT_UNEXPECTED_VALUE
        )
        self.assertEqual(result.verdicts[1].actual_value, "down")

    def test_modified_complete_failure_row_missing(self) -> None:
        """Set admin status: PORT row entirely missing -> complete failure."""
        plan = _make_set_admin_plan("Ethernet99", "up")
        live = {"PORT": {}}
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_COMPLETE_FAILURE)
        self.assertEqual(result.verdicts[0].verdict, VERDICT_ABSENT)

    def test_modified_unexpected_value(self) -> None:
        """Modified key with wrong actual value yields unexpected_value."""
        plan = _make_set_admin_plan("Ethernet0", "down")
        live = {"PORT": {"Ethernet0": {"admin_status": "up"}}}
        result = check_plan_applied(plan, live)
        self.assertEqual(
            result.verdicts[0].verdict, VERDICT_UNEXPECTED_VALUE
        )
        self.assertEqual(result.verdicts[0].actual_value, "up")

    def test_empty_predicted_keys_is_success(self) -> None:
        """A plan with no predicted_keys reports success trivially."""
        plan = ChangePlan(
            operation=OPERATION_ADD_IP,
            target="Ethernet0",
            parameters={"ip_address": "10.0.0.1/24"},
            commands=[["config", "true"]],
            description="No-op",
        )
        result = check_plan_applied(plan, {"INTERFACE": {}})
        self.assertEqual(result.overall_status, POST_APPLY_SUCCESS)
        self.assertEqual(result.verdicts, [])

    def test_missing_table_treated_as_absent(self) -> None:
        """Missing CONFIG_DB table is treated as no rows present."""
        plan = _make_add_plan("Ethernet12", "192.168.1.1/24")
        live: dict = {}
        result = check_plan_applied(plan, live)
        self.assertEqual(result.overall_status, POST_APPLY_COMPLETE_FAILURE)
        for verdict in result.verdicts:
            self.assertEqual(verdict.verdict, VERDICT_ABSENT)


class _FakeFetcher:
    """Test helper: returns a sequence of CONFIG_DB dicts on each call.

    The last entry is sticky: once the sequence is exhausted, the last
    value is returned forever. Useful for simulating a system that
    starts stale and settles.
    """

    def __init__(self, sequence: list[dict]) -> None:
        self._sequence = list(sequence)
        self.call_count = 0

    def __call__(self) -> dict:
        self.call_count += 1
        if not self._sequence:
            return {}
        if self.call_count <= len(self._sequence):
            return self._sequence[self.call_count - 1]
        return self._sequence[-1]


class TestWaitForSettled(unittest.TestCase):
    """wait_for_settled polls a fetcher until predicted state is reached."""

    def test_immediate_settle(self) -> None:
        """If first fetch satisfies the plan, return immediately."""
        plan = _make_add_plan("Ethernet0", "10.0.0.1/24")
        satisfied = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        fetcher = _FakeFetcher([satisfied])
        result = wait_for_settled(
            plan, fetcher, timeout_seconds=1.0, poll_interval=0.010
        )
        self.assertTrue(result.settled)
        self.assertEqual(fetcher.call_count, 1)
        self.assertLess(result.elapsed_seconds, 0.1)

    def test_late_settle(self) -> None:
        """After several stale polls, the fetcher returns satisfied state."""
        plan = _make_add_plan("Ethernet0", "10.0.0.1/24")
        stale = {"INTERFACE": {}}
        satisfied = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        fetcher = _FakeFetcher([stale, stale, stale, satisfied])
        result = wait_for_settled(
            plan, fetcher, timeout_seconds=1.0, poll_interval=0.010
        )
        self.assertTrue(result.settled)
        self.assertGreaterEqual(fetcher.call_count, 4)
        self.assertGreater(result.elapsed_seconds, 0.0)
        self.assertLess(result.elapsed_seconds, 1.0)

    def test_timeout(self) -> None:
        """Fetcher never returns satisfied state; wait times out."""
        plan = _make_add_plan("Ethernet0", "10.0.0.1/24")
        stale = {"INTERFACE": {}}
        fetcher = _FakeFetcher([stale])
        result = wait_for_settled(
            plan, fetcher, timeout_seconds=0.1, poll_interval=0.010
        )
        self.assertFalse(result.settled)
        self.assertGreaterEqual(result.elapsed_seconds, 0.1)

    def test_empty_predicted_keys_no_wait(self) -> None:
        """A plan with no predicted_keys returns immediately, no polling."""
        plan = ChangePlan(
            operation=OPERATION_ADD_IP,
            target="Ethernet0",
            parameters={"ip_address": "10.0.0.1/24"},
            commands=[["config", "true"]],
            description="No-op",
        )
        fetcher = _FakeFetcher([{"INTERFACE": {}}])
        result = wait_for_settled(
            plan, fetcher, timeout_seconds=1.0, poll_interval=0.010
        )
        self.assertTrue(result.settled)
        self.assertEqual(result.elapsed_seconds, 0.0)
        self.assertEqual(fetcher.call_count, 1)


if __name__ == "__main__":
    unittest.main()
