"""Low-level client for the SONiC virtual switch.

Wraps docker exec calls to the running sonic-vs-fixed container and parses
the output into structured Python data. Each function corresponds to one
logical SONiC read operation. Functions are intended to be testable
standalone (no LLM involvement).
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

CONTAINER_NAME = "sonic-vs-fixed"
CONFIG_DB_NUMBER = 4
COMMAND_TIMEOUT_SECONDS = 10


class SonicClientError(Exception):
    """Raised when a SONiC client operation fails."""


def _run_docker_exec(args: list[str]) -> str:
    """Run a command inside the SONiC container and return stdout.

    Args:
        args: the command and arguments to run inside the container, as a
            list of strings. Example: ["redis-cli", "-n", "4", "KEYS", "*"].

    Returns:
        The captured stdout as a string, with trailing newlines stripped.

    Raises:
        SonicClientError: if docker exec fails, times out, or the container
            is not running.
    """
    command = ["docker", "exec", CONTAINER_NAME, *args]
    logger.debug("running command: %s", command)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SonicClientError(
            f"command timed out after {COMMAND_TIMEOUT_SECONDS}s: {command}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SonicClientError(
            f"command failed with exit code {exc.returncode}: "
            f"{command}; stderr: {exc.stderr.strip()}"
        ) from exc
    except FileNotFoundError as exc:
        raise SonicClientError(
            "docker executable not found on PATH; is Docker Desktop running?"
        ) from exc
    return result.stdout.rstrip("\n")


def list_interface_keys() -> list[str]:
    """List all INTERFACE configuration keys in SONiC CONFIG_DB.

    Returns:
        A list of CONFIG_DB key strings starting with "INTERFACE|". Each key
        is either an L3-enabled interface marker (e.g. "INTERFACE|Ethernet0")
        or an IP address assignment (e.g. "INTERFACE|Ethernet0|10.0.0.1/24").

    Raises:
        SonicClientError: if the SONiC container is unreachable or the redis
            command fails.
    """
    output = _run_docker_exec(
        ["redis-cli", "-n", str(CONFIG_DB_NUMBER), "KEYS", "INTERFACE|*"]
    )
    if not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def get_interface_ip(interface_name: str) -> str | None:
    """Return the IP address configured on a given interface, or None.

    Args:
        interface_name: the SONiC interface name, e.g. "Ethernet0".

    Returns:
        The IP address with prefix length (e.g. "10.0.0.1/24") if one is
        configured for the given interface, or None if no IP is set.

    Raises:
        ValueError: if interface_name is empty or contains a "|" character
            (which would corrupt the CONFIG_DB key pattern).
        SonicClientError: if the SONiC container is unreachable or the redis
            command fails.
    """
    name = interface_name.strip()
    if not name:
        raise ValueError("interface_name must not be empty")
    if "|" in name:
        raise ValueError(
            f"interface_name must not contain '|': {interface_name!r}"
        )

    pattern = f"INTERFACE|{name}|*"
    output = _run_docker_exec(
        ["redis-cli", "-n", str(CONFIG_DB_NUMBER), "KEYS", pattern]
    )
    if not output:
        return None

    keys = [line.strip() for line in output.splitlines() if line.strip()]
    for key in keys:
        parts = key.split("|", maxsplit=2)
        if len(parts) == 3 and parts[1] == name:
            return parts[2]
    return None


def list_configured_interfaces() -> list[str]:
    """List interfaces that have any L3 configuration in CONFIG_DB.

    Returns:
        A sorted list of distinct interface names that have at least one
        INTERFACE|* entry in CONFIG_DB. An interface appears in the result
        if it is L3-enabled, has an IP assigned, or both.

    Raises:
        SonicClientError: if the SONiC container is unreachable or the redis
            command fails.
    """
    keys = list_interface_keys()
    names: set[str] = set()
    for key in keys:
        parts = key.split("|", maxsplit=2)
        if len(parts) >= 2 and parts[1]:
            names.add(parts[1])
    return sorted(names)


def get_bgp_summary() -> dict:
    """Return a summary of BGP state on the switch.

    Calls vtysh inside the SONiC container to run "show ip bgp summary".
    Parses the output into a small dict. This phase only distinguishes
    between "no BGP instance configured" and "BGP instance exists";
    detailed peer parsing is deferred until BGP is actually configured.

    Returns:
        A dict with three keys:
            configured: bool, True if BGP is running with at least an AS number
            summary: str, human-readable one-line description
            raw: str, the raw vtysh output (preserved for transparency)

    Raises:
        SonicClientError: if the SONiC container is unreachable, vtysh
            is missing, or the command fails.
    """
    output = _run_docker_exec(["vtysh", "-c", "show ip bgp summary"])
    raw = output.strip()

    if "BGP instance not found" in raw:
        return {
            "configured": False,
            "summary": "no BGP instance configured on this switch",
            "raw": raw,
        }

    return {
        "configured": True,
        "summary": "BGP instance exists; detailed parsing not implemented",
        "raw": raw,
    }


def _validate_interface_name(interface_name: str) -> str:
    """Validate and normalize an interface name.

    Args:
        interface_name: raw input from caller.

    Returns:
        The validated interface name with whitespace stripped.

    Raises:
        ValueError: if the name is empty or contains characters that would
            break the SONiC CLI or CONFIG_DB schema.
    """
    name = interface_name.strip()
    if not name:
        raise ValueError("interface_name must not be empty")
    if "|" in name:
        raise ValueError(
            f"interface_name must not contain '|': {interface_name!r}"
        )
    if " " in name:
        raise ValueError(
            f"interface_name must not contain spaces: {interface_name!r}"
        )
    return name


def _validate_ip_address(ip_address: str) -> str:
    """Validate and normalize an IPv4 address with prefix length.

    Args:
        ip_address: raw input like "10.0.0.1/24".

    Returns:
        The validated address string.

    Raises:
        ValueError: if the address is missing a prefix length, contains
            shell-special characters, or has other obvious issues.
    """
    address = ip_address.strip()
    if not address:
        raise ValueError("ip_address must not be empty")
    if "/" not in address:
        raise ValueError(
            f"ip_address must include a prefix length: {ip_address!r}"
        )
    for forbidden in (" ", "|", ";", "&", "$", "`"):
        if forbidden in address:
            raise ValueError(
                f"ip_address contains forbidden character {forbidden!r}: "
                f"{ip_address!r}"
            )
    return address


def apply_add_interface_ip(interface_name: str, ip_address: str) -> None:
    """Add an IP address to a SONiC interface.

    Runs `config interface ip add <interface> <ip>` inside the container.
    This mutates SONiC state. Callers should only invoke this after a
    user approval gate.

    Args:
        interface_name: e.g. "Ethernet12"
        ip_address: e.g. "192.168.1.1/24"

    Raises:
        ValueError: if either argument fails validation.
        SonicClientError: if the SONiC command fails.
    """
    name = _validate_interface_name(interface_name)
    address = _validate_ip_address(ip_address)
    logger.info("apply: add IP %s to %s", address, name)
    _run_docker_exec(["config", "interface", "ip", "add", name, address])


def apply_remove_interface_ip(interface_name: str, ip_address: str) -> None:
    """Remove an IP address from a SONiC interface.

    Runs `config interface ip remove <interface> <ip>` inside the container.
    This mutates SONiC state. Callers should only invoke this after a
    user approval gate.

    Args:
        interface_name: e.g. "Ethernet12"
        ip_address: e.g. "192.168.1.1/24"

    Raises:
        ValueError: if either argument fails validation.
        SonicClientError: if the SONiC command fails. This includes the
            case where the IP is not actually configured on the interface.
    """
    name = _validate_interface_name(interface_name)
    address = _validate_ip_address(ip_address)
    logger.info("apply: remove IP %s from %s", address, name)
    _run_docker_exec(["config", "interface", "ip", "remove", name, address])


def apply_set_interface_admin_status(
    interface_name: str, admin_status: str
) -> None:
    """Set the admin status of a SONiC interface to "up" or "down".

    Runs `config interface startup <interface>` for "up" or
    `config interface shutdown <interface>` for "down". This mutates
    SONiC state. Callers should only invoke this after a user approval gate.

    Args:
        interface_name: e.g. "Ethernet12"
        admin_status: either "up" or "down" (case-insensitive).

    Raises:
        ValueError: if admin_status is not "up" or "down", or if the
            interface name fails validation.
        SonicClientError: if the SONiC command fails.
    """
    name = _validate_interface_name(interface_name)
    status = admin_status.strip().lower()
    if status == "up":
        subcommand = "startup"
    elif status == "down":
        subcommand = "shutdown"
    else:
        raise ValueError(
            f"admin_status must be 'up' or 'down', got {admin_status!r}"
        )
    logger.info("apply: set %s admin status to %s", name, status)
    _run_docker_exec(["config", "interface", subcommand, name])
