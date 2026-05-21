"""Unit tests for snapshot_builder.apply_plan_to_config_db.

These tests are pure Python: no SONiC, no Docker, no Batfish, no file
system. They run in milliseconds.

Run:
    python3 -m unittest test_snapshot_builder.py -v
"""

import copy
import unittest

from change_plan import (
    ChangePlan,
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
)
from snapshot_builder import apply_plan_to_config_db


def _make_add_ip_plan(interface: str, ip: str) -> ChangePlan:
    """Build a minimally-valid ChangePlan for add_interface_ip."""
    return ChangePlan(
        operation=OPERATION_ADD_IP,
        target=interface,
        parameters={"ip_address": ip},
        commands=[["config", "interface", "ip", "add", interface, ip]],
        description=f"Add IP {ip} to interface {interface}",
        predicted_config_db_changes=[
            f"+ INTERFACE|{interface}",
            f"+ INTERFACE|{interface}|{ip}",
        ],
    )


def _make_remove_ip_plan(interface: str, ip: str) -> ChangePlan:
    """Build a minimally-valid ChangePlan for remove_interface_ip."""
    return ChangePlan(
        operation=OPERATION_REMOVE_IP,
        target=interface,
        parameters={"ip_address": ip},
        commands=[["config", "interface", "ip", "remove", interface, ip]],
        description=f"Remove IP {ip} from interface {interface}",
        predicted_config_db_changes=[f"- INTERFACE|{interface}|{ip}"],
    )


def _make_set_admin_plan(interface: str, status: str) -> ChangePlan:
    """Build a minimally-valid ChangePlan for set_interface_admin_status."""
    subcommand = "startup" if status == "up" else "shutdown"
    return ChangePlan(
        operation=OPERATION_SET_ADMIN,
        target=interface,
        parameters={"admin_status": status},
        commands=[["config", "interface", subcommand, interface]],
        description=f"Set interface {interface} admin status to {status}",
        predicted_config_db_changes=[
            f"~ PORT|{interface} admin_status -> {status}",
        ],
    )


class TestAddIp(unittest.TestCase):
    """add_interface_ip operation."""

    def test_add_ip_to_empty_interface_table(self) -> None:
        config = {}
        plan = _make_add_ip_plan("Ethernet16", "192.168.16.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertIn("INTERFACE", result)
        self.assertEqual(result["INTERFACE"]["Ethernet16"], {})
        self.assertEqual(
            result["INTERFACE"]["Ethernet16|192.168.16.1/24"], {}
        )

    def test_add_ip_to_existing_interface_no_ip(self) -> None:
        config = {"INTERFACE": {"Ethernet16": {}}}
        plan = _make_add_ip_plan("Ethernet16", "192.168.16.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertIn("Ethernet16", result["INTERFACE"])
        self.assertIn("Ethernet16|192.168.16.1/24", result["INTERFACE"])

    def test_add_ip_idempotent(self) -> None:
        config = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        plan = _make_add_ip_plan("Ethernet0", "10.0.0.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertEqual(len(result["INTERFACE"]), 2)

    def test_add_second_ip_to_same_interface(self) -> None:
        config = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        plan = _make_add_ip_plan("Ethernet0", "10.0.0.2/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertIn("Ethernet0|10.0.0.1/24", result["INTERFACE"])
        self.assertIn("Ethernet0|10.0.0.2/24", result["INTERFACE"])


class TestRemoveIp(unittest.TestCase):
    """remove_interface_ip operation."""

    def test_remove_existing_ip(self) -> None:
        config = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        plan = _make_remove_ip_plan("Ethernet0", "10.0.0.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertNotIn("Ethernet0|10.0.0.1/24", result["INTERFACE"])

    def test_remove_ip_leaves_marker(self) -> None:
        config = {
            "INTERFACE": {
                "Ethernet0": {},
                "Ethernet0|10.0.0.1/24": {},
            }
        }
        plan = _make_remove_ip_plan("Ethernet0", "10.0.0.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertIn("Ethernet0", result["INTERFACE"])

    def test_remove_nonexistent_ip(self) -> None:
        config = {"INTERFACE": {"Ethernet0": {}}}
        plan = _make_remove_ip_plan("Ethernet0", "10.0.0.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertEqual(result["INTERFACE"], {"Ethernet0": {}})

    def test_remove_with_no_interface_table(self) -> None:
        config = {"PORT": {"Ethernet0": {"speed": "100000"}}}
        plan = _make_remove_ip_plan("Ethernet0", "10.0.0.1/24")
        result = apply_plan_to_config_db(config, plan)
        self.assertNotIn("INTERFACE", result)
        self.assertEqual(result["PORT"], {"Ethernet0": {"speed": "100000"}})


class TestSetAdminStatus(unittest.TestCase):
    """set_interface_admin_status operation."""

    def test_set_admin_status_on_existing_port(self) -> None:
        config = {
            "PORT": {
                "Ethernet0": {
                    "alias": "fortyGigE0/0",
                    "speed": "100000",
                }
            }
        }
        plan = _make_set_admin_plan("Ethernet0", "down")
        result = apply_plan_to_config_db(config, plan)
        self.assertEqual(result["PORT"]["Ethernet0"]["admin_status"], "down")
        self.assertEqual(
            result["PORT"]["Ethernet0"]["alias"], "fortyGigE0/0"
        )

    def test_set_admin_status_on_nonexistent_port(self) -> None:
        config = {}
        plan = _make_set_admin_plan("Ethernet99", "up")
        result = apply_plan_to_config_db(config, plan)
        self.assertIn("PORT", result)
        self.assertEqual(result["PORT"]["Ethernet99"]["admin_status"], "up")

    def test_set_admin_overwrites_existing_status(self) -> None:
        config = {
            "PORT": {
                "Ethernet0": {
                    "admin_status": "up",
                    "speed": "100000",
                }
            }
        }
        plan = _make_set_admin_plan("Ethernet0", "down")
        result = apply_plan_to_config_db(config, plan)
        self.assertEqual(result["PORT"]["Ethernet0"]["admin_status"], "down")


class TestPurity(unittest.TestCase):
    """Verify the transformation does not mutate its input."""

    def test_input_dict_not_mutated(self) -> None:
        config = {
            "INTERFACE": {"Ethernet0": {}, "Ethernet0|10.0.0.1/24": {}},
            "PORT": {"Ethernet0": {"speed": "100000"}},
        }
        snapshot_before = copy.deepcopy(config)
        plan = _make_add_ip_plan("Ethernet16", "192.168.16.1/24")
        _ = apply_plan_to_config_db(config, plan)
        self.assertEqual(config, snapshot_before)


class TestDispatch(unittest.TestCase):
    """Dispatch errors."""

    def test_unknown_operation_raises(self) -> None:
        # We construct a valid plan first, then mutate via dataclass.replace
        # is not possible (frozen). Instead build a fake object that mimics
        # ChangePlan but with a bogus operation. The dispatch should reject.
        class FakePlan:
            operation = "garbage_op"
            target = "Ethernet0"
            parameters = {}

        with self.assertRaises(ValueError):
            apply_plan_to_config_db({}, FakePlan())


if __name__ == "__main__":
    unittest.main()
