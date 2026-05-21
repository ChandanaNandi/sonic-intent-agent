"""Low-level client wrapping the pybatfish API.

Provides a small set of functions that the rest of the agent code uses to
interact with the Batfish service. Hides pybatfish exception classes
behind a single BatfishClientError, mirroring the pattern used in
sonic_client.py for SONiC.

This module deliberately does NOT build snapshot files from SONiC state.
That responsibility belongs to snapshot_builder.py. This module only
talks to the Batfish service.
"""

import logging
from typing import Any

import pandas

from pybatfish.client.session import Session

logger = logging.getLogger(__name__)

BATFISH_HOST = "localhost"
DEFAULT_NETWORK_NAME = "sonic-agent"
SERVICE_TIMEOUT_SECONDS = 60


class BatfishClientError(Exception):
    """Raised when a Batfish client operation fails."""


def open_session(host: str = BATFISH_HOST) -> Session:
    """Open a pybatfish Session against the running Batfish service.

    Args:
        host: hostname or IP where Batfish is reachable. Defaults to
            BATFISH_HOST, which is the local Docker-exposed port.

    Returns:
        A live Session object. Caller may use it for subsequent calls.

    Raises:
        BatfishClientError: if a Session cannot be constructed or the
            service is unreachable.
    """
    try:
        session = Session(host=host)
    except Exception as exc:
        raise BatfishClientError(
            f"could not connect to Batfish at {host}: {exc}"
        ) from exc
    return session


def get_service_version(session: Session) -> str:
    """Return the version string of the running Batfish service.

    Args:
        session: an open pybatfish Session.

    Returns:
        The Batfish version string, e.g. "2026.04.01.3234". If the
        service does not return a version, returns "unknown".

    Raises:
        BatfishClientError: if the service is unreachable.
    """
    try:
        versions = session.get_component_versions()
    except Exception as exc:
        raise BatfishClientError(
            f"could not query Batfish version: {exc}"
        ) from exc
    return versions.get("Batfish", "unknown")


def init_snapshot(
    session: Session,
    snapshot_dir: str,
    snapshot_name: str,
    network_name: str = DEFAULT_NETWORK_NAME,
) -> None:
    """Upload a snapshot directory to Batfish and analyze it.

    Args:
        session: an open pybatfish Session.
        snapshot_dir: absolute path to a directory containing the
            snapshot files. The directory should be in the format Batfish
            expects (e.g., a "sonic_configs" subfolder for SONiC devices).
        snapshot_name: a unique name to identify this snapshot within the
            network. Existing snapshots with this name will be overwritten.
        network_name: the logical Batfish "network" to attach the snapshot
            to. A network is just a folder for organizing snapshots.

    Raises:
        BatfishClientError: if the network cannot be set or the snapshot
            cannot be uploaded.
    """
    try:
        session.set_network(network_name)
    except Exception as exc:
        raise BatfishClientError(
            f"could not set Batfish network {network_name!r}: {exc}"
        ) from exc
    try:
        session.init_snapshot(
            snapshot_dir, name=snapshot_name, overwrite=True
        )
    except Exception as exc:
        raise BatfishClientError(
            f"could not initialize snapshot from {snapshot_dir}: {exc}"
        ) from exc
    logger.info(
        "initialized snapshot %r in network %r from %s",
        snapshot_name,
        network_name,
        snapshot_dir,
    )


def get_parse_status(session: Session) -> pandas.DataFrame:
    """Return the parse status for each file in the current snapshot.

    Args:
        session: an open pybatfish Session with a snapshot already
            initialized.

    Returns:
        A pandas DataFrame with one row per file, including its parse
        status (PASSED, FAILED, or PARTIALLY_PARSED) and which device
        nodes were produced.

    Raises:
        BatfishClientError: if the query fails.
    """
    try:
        return session.q.fileParseStatus().answer().frame()
    except Exception as exc:
        raise BatfishClientError(
            f"could not get parse status: {exc}"
        ) from exc


def get_init_issues(session: Session) -> pandas.DataFrame:
    """Return the list of warnings and errors from parsing the snapshot.

    Args:
        session: an open pybatfish Session with a snapshot already
            initialized.

    Returns:
        A pandas DataFrame of init issues. Empty DataFrame means no
        issues. Each row has fields including Nodes (affected nodes),
        Source_Lines, Type (e.g., "Convert warning (redflag)"), Details
        (human-readable description), Line_Text, and Parser_Context.

    Raises:
        BatfishClientError: if the query fails.
    """
    try:
        return session.q.initIssues().answer().frame()
    except Exception as exc:
        raise BatfishClientError(
            f"could not get init issues: {exc}"
        ) from exc


def summarize_issues(issues_frame: pandas.DataFrame) -> dict[str, Any]:
    """Reduce an init issues DataFrame to a small dict for diff display.

    Args:
        issues_frame: the DataFrame returned by get_init_issues().

    Returns:
        A dict with keys:
            issue_count: int, number of rows in the frame
            critical: list[str], one short string per critical issue
            warnings: list[str], one short string per non-critical issue
    """
    if issues_frame.empty:
        return {"issue_count": 0, "critical": [], "warnings": []}

    critical: list[str] = []
    warnings_list: list[str] = []
    for _, row in issues_frame.iterrows():
        issue_type = str(row.get("Type", "")).lower()
        details = str(row.get("Details", "")).strip()
        nodes = row.get("Nodes", [])
        node_str = ",".join(nodes) if isinstance(nodes, list) else str(nodes)
        line = f"{node_str}: {details}"
        if "error" in issue_type:
            critical.append(line)
        else:
            warnings_list.append(line)
    return {
        "issue_count": len(issues_frame),
        "critical": critical,
        "warnings": warnings_list,
    }
