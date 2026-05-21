"""Data structure representing a proposed change to SONiC.

A ChangePlan is the intermediate representation between the LLM (which
decides what should change) and the agent (which shows the user a diff,
gets approval, and applies). The plan carries both a logical description
(what is changing, in human terms) and a concrete description (the exact
commands, the display-formatted predicted CONFIG_DB delta, and a
structured list of predicted CONFIG_DB keys for programmatic checks).

Plans are immutable: once created they cannot be modified. To change a
plan, build a new one.
"""

from dataclasses import dataclass, field

OPERATION_ADD_IP = "add_interface_ip"
OPERATION_REMOVE_IP = "remove_interface_ip"
OPERATION_SET_ADMIN = "set_interface_admin_status"

ALL_OPERATIONS: tuple[str, ...] = (
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
)

PREDICTED_KEY_ADDED = "added"
PREDICTED_KEY_REMOVED = "removed"
PREDICTED_KEY_MODIFIED = "modified"

ALL_PREDICTED_KEY_OPERATIONS: tuple[str, ...] = (
    PREDICTED_KEY_ADDED,
    PREDICTED_KEY_REMOVED,
    PREDICTED_KEY_MODIFIED,
)


@dataclass(frozen=True)
class PredictedKey:
    """A single expected CONFIG_DB-level effect of a ChangePlan.

    Used by post_apply_check to verify that the predicted state actually
    materialized after the change was applied. This is the structured
    counterpart to ChangePlan.predicted_config_db_changes (which is a
    list of display strings).

    Attributes:
        operation: one of PREDICTED_KEY_ADDED, PREDICTED_KEY_REMOVED,
            PREDICTED_KEY_MODIFIED.
        table: the CONFIG_DB table name, e.g. "INTERFACE" or "PORT".
        key: the specific key within the table. For INTERFACE, this is
            either "<interface>" (the L3 marker) or
            "<interface>|<ip>/<prefix>" (the IP assignment). For PORT,
            this is the interface name like "Ethernet0".
        field_name: for PREDICTED_KEY_MODIFIED, the field within the
            table row that is expected to change, e.g. "admin_status".
            None for added/removed.
        expected_value: for PREDICTED_KEY_MODIFIED, the expected new
            value of field_name, e.g. "down". None for added/removed.
    """

    operation: str
    table: str
    key: str
    field_name: str | None = None
    expected_value: str | None = None

    def __post_init__(self) -> None:
        """Validate field combinations after construction."""
        if self.operation not in ALL_PREDICTED_KEY_OPERATIONS:
            raise ValueError(
                f"unknown predicted key operation {self.operation!r}; "
                f"must be one of {ALL_PREDICTED_KEY_OPERATIONS}"
            )
        if not self.table:
            raise ValueError("table must not be empty")
        if not self.key:
            raise ValueError("key must not be empty")
        if self.operation == PREDICTED_KEY_MODIFIED:
            if self.field_name is None:
                raise ValueError(
                    "modified operation requires field_name"
                )
            if self.expected_value is None:
                raise ValueError(
                    "modified operation requires expected_value"
                )
        else:
            if self.field_name is not None:
                raise ValueError(
                    f"{self.operation} operation must not set field_name"
                )
            if self.expected_value is not None:
                raise ValueError(
                    f"{self.operation} operation must not set expected_value"
                )


@dataclass(frozen=True)
class ChangePlan:
    """A proposed change to SONiC.

    Attributes:
        operation: which operation will be performed. One of the
            OPERATION_* constants in this module.
        target: the primary subject of the change, typically an interface
            name like "Ethernet12".
        parameters: additional parameters specific to the operation.
            For add/remove IP: {"ip_address": "<ip>/<prefix>"}.
            For set admin status: {"admin_status": "up" | "down"}.
        commands: the actual command(s) that will run inside the SONiC
            container, each as a list of args suitable for
            sonic_client._run_docker_exec. There is usually one command
            per plan, but a list is more flexible if we extend later.
        description: one-line human-readable summary, e.g.
            "Add IP 192.168.1.1/24 to interface Ethernet12".
        predicted_config_db_changes: list of expected CONFIG_DB key deltas
            as display strings, e.g. "+ INTERFACE|Ethernet12|10.0.0.1/24".
            For display in the diff renderer.
        predicted_keys: list of PredictedKey entries describing the
            expected post-apply state in structured form. Used by
            post_apply_check.py. Defaults to empty list for backward
            compatibility with plans constructed without it.
    """

    operation: str
    target: str
    parameters: dict[str, str]
    commands: list[list[str]]
    description: str
    predicted_config_db_changes: list[str] = field(default_factory=list)
    predicted_keys: list[PredictedKey] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate fields after construction."""
        if self.operation not in ALL_OPERATIONS:
            raise ValueError(
                f"unknown operation {self.operation!r}; "
                f"must be one of {ALL_OPERATIONS}"
            )
        if not self.target:
            raise ValueError("target must not be empty")
        if not self.commands:
            raise ValueError("commands must not be empty")
        if not self.description:
            raise ValueError("description must not be empty")
