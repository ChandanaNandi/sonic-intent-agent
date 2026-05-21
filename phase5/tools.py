"""Tools exposed to the LLM for querying SONiC state.

Each function in this module is a tool the LLM can invoke. Tools wrap the
low-level sonic_client functions, catch any SonicClientError, and return
strings (or simple JSON-serializable data) that the LLM can read.

Tools must never raise exceptions to the LLM. On failure they return a
human-readable error string that the LLM can incorporate into its answer.

The function name, docstring, parameter names, and type hints are all
visible to the LLM via the Ollama Python library's auto-generated tool
schema. Write them as if explaining the function to the model.
"""

import logging

import sonic_client

logger = logging.getLogger(__name__)


def get_interface_ip(interface_name: str) -> str:
    """Get the IP address configured on a SONiC switch interface.

    Use this when the user asks about the IP address of a specific
    interface on the switch. The interface name is something like
    Ethernet0, Ethernet4, Ethernet124, etc.

    Args:
        interface_name: the name of the interface to query, for example
            "Ethernet0".

    Returns:
        A short human-readable message stating the IP address and prefix
        length, e.g. "Ethernet0 has IP 10.0.0.1/24". If no IP is configured,
        returns a message saying so. If the query fails, returns an error
        message starting with "error:".
    """
    try:
        ip = sonic_client.get_interface_ip(interface_name)
    except ValueError as exc:
        return f"error: invalid interface name: {exc}"
    except sonic_client.SonicClientError as exc:
        logger.warning("sonic_client error in get_interface_ip: %s", exc)
        return f"error: could not query switch: {exc}"

    if ip is None:
        return f"{interface_name} has no IP address configured"
    return f"{interface_name} has IP {ip}"


def list_configured_interfaces() -> str:
    """List all interfaces on the SONiC switch that have any L3 configuration.

    Use this when the user asks "which interfaces are configured" or
    "what is set up on the switch" or similar questions about the
    overall switch configuration. This does not list all physical ports,
    only the ones that have been given Layer 3 configuration (IP address
    assignments or L3-enabled markers).

    Returns:
        A human-readable string listing the configured interface names,
        one per line, or a message saying none are configured. If the
        query fails, returns an error message starting with "error:".
    """
    try:
        names = sonic_client.list_configured_interfaces()
    except sonic_client.SonicClientError as exc:
        logger.warning(
            "sonic_client error in list_configured_interfaces: %s", exc
        )
        return f"error: could not query switch: {exc}"

    if not names:
        return "no interfaces have L3 configuration on this switch"

    header = f"interfaces with L3 configuration ({len(names)}):"
    body = "\n".join(f"  - {name}" for name in names)
    return f"{header}\n{body}"


def get_bgp_status() -> str:
    """Get the status of BGP routing on the SONiC switch.

    Use this when the user asks about BGP, routing protocols, BGP peers,
    BGP neighbors, or whether routing is configured. This queries the
    FRR routing daemon inside the switch for BGP state.

    Returns:
        A human-readable string describing whether BGP is configured and
        what its status is. If BGP is not configured, says so clearly.
        If the query fails, returns an error message starting with "error:".
    """
    try:
        result = sonic_client.get_bgp_summary()
    except sonic_client.SonicClientError as exc:
        logger.warning("sonic_client error in get_bgp_status: %s", exc)
        return f"error: could not query switch: {exc}"

    if not result["configured"]:
        return "BGP is not configured on this switch (no BGP instance exists)"

    return (
        f"BGP is configured on this switch. {result['summary']}. "
        f"Raw vtysh output: {result['raw']}"
    )


# Module-level state: plans proposed by the LLM during the current
# agent invocation. The agent clears this list before each user query
# and reads it after the LLM call to pick up any proposals.
proposed_plans: list = []


def _normalize_admin_status(value: str) -> str:
    """Map a variety of natural-language values to 'up' or 'down'.

    Accepts "up", "down", "enable", "disable", "shutdown", "startup",
    case-insensitively. Falls through to the original value on no match
    (validation in sonic_client will then reject).
    """
    normalized = value.strip().lower()
    if normalized in ("up", "enable", "enabled", "startup", "start"):
        return "up"
    if normalized in ("down", "disable", "disabled", "shutdown", "stop"):
        return "down"
    return normalized


def propose_add_interface_ip(
    interface_name: str, ip_address: str
) -> str:
    """Propose adding an IP address to a SONiC switch interface.

    Use this when the user asks to configure, assign, or add an IP address
    to an interface. This builds a change proposal that will be shown to
    the user for approval before any change is actually applied.

    Args:
        interface_name: the interface to configure, e.g. "Ethernet12".
        ip_address: the IPv4 address with prefix length, e.g. "192.168.1.1/24".

    Returns:
        A short acknowledgement string confirming the proposal has been
        recorded. If the input fails validation, returns an error string
        starting with "error:".
    """
    try:
        name = sonic_client._validate_interface_name(interface_name)
        address = sonic_client._validate_ip_address(ip_address)
    except ValueError as exc:
        return f"error: invalid input: {exc}"

    from change_plan import ChangePlan, OPERATION_ADD_IP

    plan = ChangePlan(
        operation=OPERATION_ADD_IP,
        target=name,
        parameters={"ip_address": address},
        commands=[["config", "interface", "ip", "add", name, address]],
        description=f"Add IP {address} to interface {name}",
        predicted_config_db_changes=[
            f"+ INTERFACE|{name}",
            f"+ INTERFACE|{name}|{address}",
        ],
    )
    proposed_plans.append(plan)
    return f"Proposed: add IP {address} to {name}. Awaiting user approval."


def propose_remove_interface_ip(
    interface_name: str, ip_address: str
) -> str:
    """Propose removing an IP address from a SONiC switch interface.

    Use this when the user asks to remove, delete, or unconfigure an IP
    address from an interface. This builds a change proposal that will
    be shown to the user for approval before any change is actually
    applied.

    Args:
        interface_name: the interface to modify, e.g. "Ethernet12".
        ip_address: the IPv4 address with prefix length to remove,
            e.g. "192.168.1.1/24".

    Returns:
        A short acknowledgement string confirming the proposal. Returns
        an error string starting with "error:" if input is invalid.
    """
    try:
        name = sonic_client._validate_interface_name(interface_name)
        address = sonic_client._validate_ip_address(ip_address)
    except ValueError as exc:
        return f"error: invalid input: {exc}"

    from change_plan import ChangePlan, OPERATION_REMOVE_IP

    plan = ChangePlan(
        operation=OPERATION_REMOVE_IP,
        target=name,
        parameters={"ip_address": address},
        commands=[["config", "interface", "ip", "remove", name, address]],
        description=f"Remove IP {address} from interface {name}",
        predicted_config_db_changes=[
            f"- INTERFACE|{name}|{address}",
        ],
    )
    proposed_plans.append(plan)
    return f"Proposed: remove IP {address} from {name}. Awaiting user approval."


def propose_set_interface_admin_status(
    interface_name: str, admin_status: str
) -> str:
    """Propose setting an interface admin status to up or down.

    Use this when the user asks to bring an interface up or down, enable
    or disable an interface, or shut down an interface. This builds a
    change proposal that will be shown to the user for approval before
    any change is actually applied.

    Args:
        interface_name: the interface to modify, e.g. "Ethernet12".
        admin_status: either "up" or "down" (case-insensitive). Common
            synonyms like "enable", "disable", "startup", "shutdown" are
            also accepted.

    Returns:
        A short acknowledgement string. Returns an error string starting
        with "error:" if input is invalid.
    """
    try:
        name = sonic_client._validate_interface_name(interface_name)
    except ValueError as exc:
        return f"error: invalid input: {exc}"

    status = _normalize_admin_status(admin_status)
    if status not in ("up", "down"):
        return f"error: admin_status must be 'up' or 'down', got {admin_status!r}"

    from change_plan import ChangePlan, OPERATION_SET_ADMIN

    subcommand = "startup" if status == "up" else "shutdown"
    plan = ChangePlan(
        operation=OPERATION_SET_ADMIN,
        target=name,
        parameters={"admin_status": status},
        commands=[["config", "interface", subcommand, name]],
        description=f"Set interface {name} admin status to {status}",
        predicted_config_db_changes=[
            f"~ PORT|{name} admin_status -> {status}",
        ],
    )
    proposed_plans.append(plan)
    return (
        f"Proposed: set {name} admin status to {status}. "
        f"Awaiting user approval."
    )
