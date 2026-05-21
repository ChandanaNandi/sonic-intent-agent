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
