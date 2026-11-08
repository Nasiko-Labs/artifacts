"""Started by sandbox.py as `python -I sandbox_launcher.py <pytest args>` inside a run directory.

It is the only code that runs between the agent and the user's pasted tests: it lowers the resource
limits (they cannot be raised again by the child), installs a best-effort guard that refuses outbound
connections (NOT a security boundary: pasted code could undo it), puts the run directory (and its `src/`,
the common src layout) on sys.path, and runs pytest. `-I` keeps the agent's own modules and every
PYTHON* variable out of the child. It imports nothing from the agent."""

import os
import resource
import socket
import sys

MB = 1024 * 1024
LIMITS = (("RLIMIT_CPU", 20), ("RLIMIT_AS", 400 * MB), ("RLIMIT_FSIZE", 10 * MB), ("RLIMIT_NOFILE", 128))
# RLIMIT_NPROC counts every process of the user; only on Linux (the pod, uid 10001) is that this agent alone.
LINUX_LIMITS = (("RLIMIT_NPROC", 64),)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _lower(name, value):
    which = getattr(resource, name, None)
    if which is None:
        return
    try:
        _, hard = resource.getrlimit(which)
        cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(which, (cap, cap))
    except (ValueError, OSError):
        pass  # the platform refuses this limit (e.g. RLIMIT_AS on macOS); the wall-clock kill still applies


def _guard_network():
    original = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else None
        if self.family in (socket.AF_INET, socket.AF_INET6) and host not in LOOPBACK:
            raise OSError("network access is disabled in this test sandbox")
        return original(self, address)

    socket.socket.connect = connect


def main():
    for name, value in LIMITS + (LINUX_LIMITS if sys.platform.startswith("linux") else ()):
        _lower(name, value)
    _guard_network()
    here = os.getcwd()
    sys.path[:0] = [here] + ([os.path.join(here, "src")] if os.path.isdir(os.path.join(here, "src")) else [])
    import pytest

    sys.exit(pytest.main(sys.argv[1:]))


if __name__ == "__main__":
    main()
