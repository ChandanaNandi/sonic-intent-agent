"""Phase 6 Chunk 1: read-after-write timing test.

Measures how long after sonic_client.apply_add_interface_ip returns
before the corresponding INTERFACE keys are visible in CONFIG_DB.

If consistently under 100ms, no retry logic is needed in post_apply_check.
If it varies, document the variance and add a small wait-with-retry.

Runs 5 iterations using Ethernet24 (unused by fixture and other tests).
"""

import logging
import subprocess
import time
from typing import Optional

import sonic_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TEST_INTERFACE = "Ethernet24"
TEST_IP = "10.24.0.1/24"
TEST_IP_KEY = f"INTERFACE|{TEST_INTERFACE}|{TEST_IP}"
POLL_INTERVAL_SECONDS = 0.010
MAX_WAIT_SECONDS = 2.0
ITERATIONS = 5


def _redis_key_exists(key: str) -> bool:
    """Return True if the given key is present in SONiC CONFIG_DB."""
    result = subprocess.run(
        [
            "docker", "exec", "sonic-vs-fixed",
            "redis-cli", "-n", "4", "EXISTS", key,
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return result.stdout.strip() == "1"


def _wait_for_key(key: str, max_seconds: float) -> Optional[float]:
    """Poll until the given key appears in CONFIG_DB, or timeout.

    Args:
        key: the CONFIG_DB key to wait for.
        max_seconds: how long to wait before giving up.

    Returns:
        Elapsed seconds if the key appeared, None if timed out.
    """
    start = time.monotonic()
    while time.monotonic() - start < max_seconds:
        if _redis_key_exists(key):
            return time.monotonic() - start
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _cleanup() -> None:
    """Remove the test IP from Ethernet24 if present. Best-effort."""
    try:
        current = sonic_client.get_interface_ip(TEST_INTERFACE)
    except sonic_client.SonicClientError:
        return
    if current == TEST_IP:
        try:
            sonic_client.apply_remove_interface_ip(TEST_INTERFACE, TEST_IP)
        except sonic_client.SonicClientError:
            pass
    time.sleep(0.5)


def run_one_iteration(iteration: int) -> Optional[float]:
    """Run a single timing iteration. Returns elapsed seconds or None."""
    _cleanup()
    if _redis_key_exists(TEST_IP_KEY):
        logger.warning(
            "iteration %d: key still present after cleanup, skipping",
            iteration,
        )
        return None

    apply_start = time.monotonic()
    sonic_client.apply_add_interface_ip(TEST_INTERFACE, TEST_IP)
    apply_elapsed = time.monotonic() - apply_start

    wait_elapsed = _wait_for_key(TEST_IP_KEY, MAX_WAIT_SECONDS)
    if wait_elapsed is None:
        logger.warning(
            "iteration %d: key did not appear within %.1fs",
            iteration,
            MAX_WAIT_SECONDS,
        )
        return None

    total = apply_elapsed + wait_elapsed
    print(
        f"  iteration {iteration}: "
        f"apply={apply_elapsed * 1000:.1f}ms, "
        f"wait_for_key={wait_elapsed * 1000:.1f}ms, "
        f"total={total * 1000:.1f}ms"
    )
    return total


def main() -> int:
    """Run the timing test. Returns 0 on success, 1 on any iteration failure."""
    print(f"--- read-after-write timing test ({ITERATIONS} iterations) ---")
    print(
        f"  interface={TEST_INTERFACE}, ip={TEST_IP}, "
        f"poll_interval={POLL_INTERVAL_SECONDS * 1000:.0f}ms"
    )
    print()

    elapsed_times = []
    for i in range(1, ITERATIONS + 1):
        elapsed = run_one_iteration(i)
        if elapsed is not None:
            elapsed_times.append(elapsed)

    print()
    print("--- summary ---")
    if not elapsed_times:
        print("FAIL: no successful iterations")
        return 1
    print(f"  successful iterations: {len(elapsed_times)}/{ITERATIONS}")
    print(f"  min:  {min(elapsed_times) * 1000:.1f}ms")
    print(f"  max:  {max(elapsed_times) * 1000:.1f}ms")
    print(f"  mean: {sum(elapsed_times) / len(elapsed_times) * 1000:.1f}ms")
    if max(elapsed_times) < 0.100:
        print("  verdict: consistently under 100ms; no retry logic needed")
    else:
        print(
            f"  verdict: max {max(elapsed_times) * 1000:.1f}ms exceeds 100ms; "
            f"consider retry logic"
        )

    _cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
