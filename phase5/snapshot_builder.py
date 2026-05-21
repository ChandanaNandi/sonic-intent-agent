"""Pure Python transformation: apply a ChangePlan to a config_db dict.

This module is responsible ONLY for the in-memory transformation. It does
not talk to SONiC, Docker, the file system, or Batfish. The transformation
is a pure function: given a config_db dict and a ChangePlan, it returns a
new dict reflecting the proposed change.

The companion file I/O glue (extract live config, write snapshot, write
stub frr.conf) lives in a separate function added later, to keep the
blast radius of bugs small.
"""

import copy
import logging

from change_plan import (
    ChangePlan,
    OPERATION_ADD_IP,
    OPERATION_REMOVE_IP,
    OPERATION_SET_ADMIN,
)

logger = logging.getLogger(__name__)


def apply_plan_to_config_db(config_db: dict, plan: ChangePlan) -> dict:
    """Apply a ChangePlan to a deep copy of config_db and return it.

    Args:
        config_db: parsed SONiC CONFIG_DB as a dict, in the schema produced
            by `sonic-cfggen -d --print-data`. Not modified.
        plan: the proposed change.

    Returns:
        A new dict reflecting the change. The input dict is unchanged.

    Raises:
        ValueError: if plan.operation is unrecognized.
    """
    new_config = copy.deepcopy(config_db)
    if plan.operation == OPERATION_ADD_IP:
        return _apply_add_ip(new_config, plan)
    if plan.operation == OPERATION_REMOVE_IP:
        return _apply_remove_ip(new_config, plan)
    if plan.operation == OPERATION_SET_ADMIN:
        return _apply_set_admin(new_config, plan)
    raise ValueError(f"unknown operation {plan.operation!r}")


def _apply_add_ip(config: dict, plan: ChangePlan) -> dict:
    """Add an IP assignment to the INTERFACE table.

    Adds two keys to config["INTERFACE"]:
        "<interface>": {} (L3 marker; only if not already present)
        "<interface>|<ip>/<prefix>": {} (IP assignment; idempotent)

    Args:
        config: deep-copied config_db (will be mutated).
        plan: a ChangePlan with operation OPERATION_ADD_IP.

    Returns:
        The mutated config.
    """
    interface_table = config.setdefault("INTERFACE", {})
    ip_address = plan.parameters["ip_address"]
    marker_key = plan.target
    ip_key = f"{plan.target}|{ip_address}"

    interface_table.setdefault(marker_key, {})
    interface_table.setdefault(ip_key, {})
    logger.info(
        "candidate: added INTERFACE keys %r and %r", marker_key, ip_key
    )
    return config


def _apply_remove_ip(config: dict, plan: ChangePlan) -> dict:
    """Remove an IP assignment from the INTERFACE table.

    Removes the "<interface>|<ip>/<prefix>" key. The L3 marker
    "<interface>" is left intact (other IPs may still be assigned).
    If the key does not exist, returns the config unchanged.

    Args:
        config: deep-copied config_db (will be mutated).
        plan: a ChangePlan with operation OPERATION_REMOVE_IP.

    Returns:
        The mutated config (or unchanged if the key was not present).
    """
    interface_table = config.get("INTERFACE")
    if interface_table is None:
        logger.info(
            "candidate: INTERFACE table absent, nothing to remove"
        )
        return config

    ip_address = plan.parameters["ip_address"]
    ip_key = f"{plan.target}|{ip_address}"
    if ip_key in interface_table:
        del interface_table[ip_key]
        logger.info("candidate: removed INTERFACE key %r", ip_key)
    else:
        logger.info(
            "candidate: INTERFACE key %r not present, no-op", ip_key
        )
    return config


def _apply_set_admin(config: dict, plan: ChangePlan) -> dict:
    """Set admin_status on a PORT table entry.

    Modifies config["PORT"][<interface>]["admin_status"] to the requested
    value. Creates the PORT entry if it does not exist (this will surface
    in Batfish as a warning about a port that lacks hardware attributes,
    which is the correct outcome for an interface name typo).

    Args:
        config: deep-copied config_db (will be mutated).
        plan: a ChangePlan with operation OPERATION_SET_ADMIN.

    Returns:
        The mutated config.
    """
    port_table = config.setdefault("PORT", {})
    port_entry = port_table.setdefault(plan.target, {})
    admin_status = plan.parameters["admin_status"]
    port_entry["admin_status"] = admin_status
    logger.info(
        "candidate: set PORT[%r].admin_status = %r",
        plan.target,
        admin_status,
    )
    return config


# File I/O glue: extract live config from SONiC, write Batfish snapshots.
# This section depends on docker exec to the SONiC container. The pure
# transformation above does not.

import json
import subprocess
from pathlib import Path


CONTAINER_NAME = "sonic-vs-fixed"
DEVICE_NAME = "sonic-vs-fixed"
SONIC_CFGGEN_TIMEOUT_SECONDS = 30

# Minimal stub for Batfish. The SONiC VS image with no BGP configured does
# not produce a real frr.conf. Batfish requires this file to recognize the
# device as SONiC, so we provide a syntactically valid empty file.
FRR_STUB_CONTENT = (
    "! placeholder - no FRR routing config on this SONiC VS\n"
)


class SnapshotBuilderError(Exception):
    """Raised when snapshot extraction or write fails."""


def _fetch_live_config_db() -> dict:
    """Fetch the live CONFIG_DB from the SONiC container as a dict.

    Runs `sonic-cfggen -d --print-data` inside the container and parses
    the resulting JSON. This reflects the current Redis CONFIG_DB state,
    which may differ from the on-disk /etc/sonic/config_db.json if
    `config save` has not been run since the last change.

    Returns:
        The CONFIG_DB as a Python dict.

    Raises:
        SnapshotBuilderError: if the docker exec, command, or JSON parse
            fails.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "sonic-cfggen", "-d",
             "--print-data"],
            capture_output=True,
            text=True,
            check=True,
            timeout=SONIC_CFGGEN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotBuilderError(
            f"sonic-cfggen timed out after "
            f"{SONIC_CFGGEN_TIMEOUT_SECONDS}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SnapshotBuilderError(
            f"sonic-cfggen failed with exit code {exc.returncode}: "
            f"stderr={exc.stderr.strip()}"
        ) from exc
    except FileNotFoundError as exc:
        raise SnapshotBuilderError(
            "docker executable not found on PATH"
        ) from exc

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SnapshotBuilderError(
            f"sonic-cfggen output is not valid JSON: {exc}"
        ) from exc


def _write_snapshot(
    snapshot_root: Path,
    config_db: dict,
    device_name: str = DEVICE_NAME,
) -> None:
    """Write a Batfish-format SONiC snapshot to disk.

    Layout produced:
        <snapshot_root>/
          sonic_configs/
            <device_name>/
              config_db.json
              frr.conf

    Args:
        snapshot_root: the root directory to write into. Created if it
            does not exist.
        config_db: the CONFIG_DB dict to serialize.
        device_name: the subfolder name under sonic_configs/.

    Raises:
        SnapshotBuilderError: if any write fails.
    """
    device_dir = snapshot_root / "sonic_configs" / device_name
    try:
        device_dir.mkdir(parents=True, exist_ok=True)
        (device_dir / "config_db.json").write_text(
            json.dumps(config_db, indent=2)
        )
        (device_dir / "frr.conf").write_text(FRR_STUB_CONTENT)
    except OSError as exc:
        raise SnapshotBuilderError(
            f"failed to write snapshot under {snapshot_root}: {exc}"
        ) from exc
    logger.info(
        "wrote snapshot to %s (device=%s)", snapshot_root, device_name
    )


def build_current_snapshot(snapshot_root: Path) -> None:
    """Extract live SONiC config and write a Batfish snapshot.

    Args:
        snapshot_root: directory to write the snapshot into. Created if
            it does not exist.

    Raises:
        SnapshotBuilderError: if extraction or write fails.
    """
    config = _fetch_live_config_db()
    _write_snapshot(snapshot_root, config)


def build_candidate_snapshot(
    snapshot_root: Path, plan: ChangePlan
) -> None:
    """Extract live SONiC config, apply a proposed plan, write snapshot.

    The live config is NOT modified. Only the in-memory copy used to
    build the candidate snapshot reflects the change.

    Args:
        snapshot_root: directory to write the candidate snapshot into.
        plan: the proposed change to apply.

    Raises:
        SnapshotBuilderError: if extraction or write fails.
        ValueError: if the plan operation is unrecognized.
    """
    config = _fetch_live_config_db()
    modified_config = apply_plan_to_config_db(config, plan)
    _write_snapshot(snapshot_root, modified_config)
