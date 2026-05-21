"""Data structure representing a proposed change to SONiC.

A ChangePlan is the intermediate representation between the LLM (which
decides what should change) and the agent (which shows the user a diff,
gets approval, and applies). The plan carries both a logical description
(what is changing, in human terms) and a concrete description (the exact
commands and the predicted CONFIG_DB delta).

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
        predicted_config_db_changes: list of expected CONFIG_DB key deltas.
            Each item is a string like "+ INTERFACE|Ethernet12|10.0.0.1/24"
            (addition) or "- INTERFACE|Ethernet12|10.0.0.1/24" (removal).
            This is a prediction; actual changes are verified post-apply.
    """

    operation: str
    target: str
    parameters: dict[str, str]
    commands: list[list[str]]
    description: str
    predicted_config_db_changes: list[str] = field(default_factory=list)

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
