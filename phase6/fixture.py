"""Set up a known test fixture on the SONiC switch for Phase 3 tests.

Idempotently configures three interfaces with IP addresses:
    Ethernet0 -> 10.0.0.1/24
    Ethernet4 -> 10.0.4.1/24
    Ethernet8 -> 10.0.8.1/24

This is the ONLY script in Phase 3 that modifies switch state. All agent
code and tools are strictly read-only. The fixture is intended to be run
once before integration tests so the test questions have predictable
answers.

Usage:
    python3 fixture.py
"""

import logging
import sys

import sonic_client

logger = logging.getLogger(__name__)

FIXTURE_CONFIG: dict[str, str] = {
    "Ethernet0": "10.0.0.1/24",
    "Ethernet4": "10.0.4.1/24",
    "Ethernet8": "10.0.8.1/24",
}


def _apply_interface_ip(interface_name: str, ip_address: str) -> str:
    """Ensure the given interface has the given IP address configured.

    If the interface already has this IP, returns "already configured".
    If the interface has a different IP, leaves it alone and returns a
    warning (we do not overwrite to avoid disrupting other tests).
    Otherwise applies the configuration.

    Args:
        interface_name: e.g. "Ethernet4"
        ip_address: e.g. "10.0.4.1/24"

    Returns:
        A status string describing what was done.

    Raises:
        sonic_client.SonicClientError: if the SONiC commands fail.
    """
    existing = sonic_client.get_interface_ip(interface_name)
    if existing == ip_address:
        return f"{interface_name}: already configured as {ip_address}"
    if existing is not None:
        return (
            f"{interface_name}: has different IP {existing}; "
            f"leaving as-is (will not overwrite)"
        )

    sonic_client._run_docker_exec(
        ["config", "interface", "ip", "add", interface_name, ip_address]
    )
    return f"{interface_name}: configured as {ip_address}"


def main() -> int:
    """Apply the fixture configuration. Returns process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    print("applying Phase 3 fixture configuration:")
    try:
        for interface_name, ip_address in FIXTURE_CONFIG.items():
            status = _apply_interface_ip(interface_name, ip_address)
            print(f"  {status}")
    except sonic_client.SonicClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print("verifying final state:")
    try:
        configured = sonic_client.list_configured_interfaces()
        for name in configured:
            ip = sonic_client.get_interface_ip(name)
            print(f"  {name}: {ip}")
    except sonic_client.SonicClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
