"""Macht custom_components/pvlader als Paket `pvlader` importierbar.

Es wird ein synthetisches Paketmodul mit __path__ registriert, sodass die
relativen Importe innerhalb des Pakets funktionieren, die (später HA-abhängige)
__init__.py aber NIE ausgeführt wird. Tests importieren z. B.
`from pvlader.controller import step`.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "pvlader"

if "pvlader" not in sys.modules:
    _pkg = types.ModuleType("pvlader")
    _pkg.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
    _pkg.__package__ = "pvlader"
    sys.modules["pvlader"] = _pkg


def _windows_allow_socketpair() -> None:
    """Windows: der Proactor-Eventloop braucht socket.socketpair() (AF_INET), das
    pytest-homeassistant-custom-component per pytest_socket.disable_socket sperrt.
    Sperre daher abschalten; socket_allow_hosts(127.0.0.1) bleibt aktiv, d. h.
    Verbindungen zu fremden Hosts schlagen weiterhin fehl."""
    if sys.platform != "win32":
        return
    try:
        import pytest_socket
    except ImportError:
        return
    pytest_socket.disable_socket = lambda *args, **kwargs: None


_windows_allow_socketpair()
