"""Unit tests for change_plan.PredictedKey and the propose-function changes.

These tests cover the Phase 6 Chunk 2 refactor: the PredictedKey
dataclass and the predicted_keys field on ChangePlan, plus the
populating logic in tools.py.

All tests are pure Python: no SONiC, no Docker, no Batfish, no file
system. They run in milliseconds.

Run:
    python3 -m unittest test_change_plan.py -v
"""

import unittest

import tools
from change_plan import (
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
    PREDICTED_KEY_ADDED,
    PREDICTED_KEY_MODIFIED,
    PREDICTED_KEY_REMOVED,
    PredictedKey,
)


class TestPredictedKey(unittest.TestCase):
    """Validation rules on PredictedKey."""

    def test_added_with_minimal_fields(self) -> None:
        """An 'added' key requires only operation, table, and key."""
        key = PredictedKey(
            operation=PREDICTED_KEY_ADDED,
            table="INTERFACE",
            key="Ethernet0|10.0.0.1/24",
        )
        self.assertEqual(key.operation, PREDICTED_KEY_ADDED)
        self.assertEqual(key.table, "INTERFACE")
        self.assertEqual(key.key, "Ethernet0|10.0.0.1/24")
        self.assertIsNone(key.field_name)
        self.assertIsNone(key.expected_value)

    def test_modified_requires_field_name_and_value(self) -> None:
        """A 'modified' key carries field_name and expected_value."""
        key = PredictedKey(
            operation=PREDICTED_KEY_MODIFIED,
            table="PORT",
            key="Ethernet0",
            field_name="admin_status",
            expected_value="down",
        )
        self.assertEqual(key.field_name, "admin_status")
        self.assertEqual(key.expected_value, "down")

    def test_modified_without_field_name_raises(self) -> None:
        """Modified must supply field_name."""
        with self.assertRaises(ValueError) as ctx:
            PredictedKey(
                operation=PREDICTED_KEY_MODIFIED,
                table="PORT",
                key="Ethernet0",
                expected_value="down",
            )
        self.assertIn("field_name", str(ctx.exception))

    def test_added_with_field_name_raises(self) -> None:
        """Added must not supply field_name."""
        with self.assertRaises(ValueError) as ctx:
            PredictedKey(
                operation=PREDICTED_KEY_ADDED,
                table="INTERFACE",
                key="Ethernet0",
                field_name="admin_status",
            )
        self.assertIn("must not set field_name", str(ctx.exception))

    def test_unknown_operation_raises(self) -> None:
        """Bogus operation string is rejected."""
        with self.assertRaises(ValueError) as ctx:
            PredictedKey(
                operation="garbage",
                table="INTERFACE",
                key="Ethernet0",
            )
        self.assertIn("unknown predicted key operation", str(ctx.exception))


class TestProposeFunctionsPopulatePredictedKeys(unittest.TestCase):
    """The three propose tools must populate predicted_keys correctly."""

    def setUp(self) -> None:
        tools.proposed_plans.clear()

    def test_add_ip_produces_two_added_keys(self) -> None:
        """propose_add_interface_ip emits marker key + IP key."""
        result = tools.propose_add_interface_ip(
            "Ethernet12", "192.168.1.1/24"
        )
        self.assertIn("Proposed", result)
        plan = tools.proposed_plans[0]
        self.assertEqual(plan.operation, OPERATION_ADD_IP)
        self.assertEqual(len(plan.predicted_keys), 2)

        marker_key, ip_key = plan.predicted_keys
        self.assertEqual(marker_key.operation, PREDICTED_KEY_ADDED)
        self.assertEqual(marker_key.table, "INTERFACE")
        self.assertEqual(marker_key.key, "Ethernet12")
        self.assertEqual(ip_key.operation, PREDICTED_KEY_ADDED)
        self.assertEqual(ip_key.table, "INTERFACE")
        self.assertEqual(ip_key.key, "Ethernet12|192.168.1.1/24")

    def test_remove_ip_produces_one_removed_key(self) -> None:
        """propose_remove_interface_ip emits only the IP key, not the marker."""
        result = tools.propose_remove_interface_ip(
            "Ethernet0", "10.0.0.1/24"
        )
        self.assertIn("Proposed", result)
        plan = tools.proposed_plans[0]
        self.assertEqual(plan.operation, OPERATION_REMOVE_IP)
        self.assertEqual(len(plan.predicted_keys), 1)

        ip_key = plan.predicted_keys[0]
        self.assertEqual(ip_key.operation, PREDICTED_KEY_REMOVED)
        self.assertEqual(ip_key.table, "INTERFACE")
        self.assertEqual(ip_key.key, "Ethernet0|10.0.0.1/24")

    def test_set_admin_produces_one_modified_key(self) -> None:
        """propose_set_interface_admin_status emits a modified key."""
        result = tools.propose_set_interface_admin_status("Ethernet8", "down")
        self.assertIn("Proposed", result)
        plan = tools.proposed_plans[0]
        self.assertEqual(plan.operation, OPERATION_SET_ADMIN)
        self.assertEqual(len(plan.predicted_keys), 1)

        admin_key = plan.predicted_keys[0]
        self.assertEqual(admin_key.operation, PREDICTED_KEY_MODIFIED)
        self.assertEqual(admin_key.table, "PORT")
        self.assertEqual(admin_key.key, "Ethernet8")
        self.assertEqual(admin_key.field_name, "admin_status")
        self.assertEqual(admin_key.expected_value, "down")


if __name__ == "__main__":
    unittest.main()
