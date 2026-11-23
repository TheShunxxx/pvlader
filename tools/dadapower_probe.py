"""Nur-lesende Modbus-TCP-Probe für die Dadapower-Wallbox.

Nutzt ausschließlich FC04 (Read Input Registers). Es wird NICHTS geschrieben.
Aufruf:  python tools/dadapower_probe.py <host> [port] [unit]
"""
from __future__ import annotations

import socket
import struct
import sys

if len(sys.argv) < 2:
    sys.exit("Aufruf: python tools/dadapower_probe.py <host> [port] [unit]")
HOST = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 502
UNIT = int(sys.argv[3]) if len(sys.argv) > 3 else 1

_tid = 0


def read_input(sock: socket.socket, addr: int, count: int) -> list[int]:
    global _tid
    _tid = (_tid + 1) & 0xFFFF
    req = struct.pack(">HHHBBHH", _tid, 0, 6, UNIT, 0x04, addr, count)
    sock.sendall(req)
    hdr = _recv(sock, 7)
    _, _, length, _ = struct.unpack(">HHHB", hdr)
    body = _recv(sock, length - 1)
    if body[0] & 0x80:
        raise RuntimeError(f"Modbus-Exception {body[1]} bei Register {addr}")
    n = body[1]
    return list(struct.unpack(f">{n // 2}H", body[2 : 2 + n]))


def _recv(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Verbindung geschlossen")
        buf += chunk
    return buf


def as_str(regs: list[int]) -> str:
    return b"".join(struct.pack(">H", r) for r in regs).replace(b"\x00", b"").decode(errors="replace").strip()


def as_uint(regs: list[int]) -> int:
    v = 0
    for r in regs:
        v = (v << 16) | r
    return v


def main() -> None:
    print(f"Verbinde {HOST}:{PORT} Unit {UNIT} (nur lesen) ...")
    with socket.create_connection((HOST, PORT), timeout=5) as s:
        s.settimeout(5)
        checks = [
            ("102 Failsafe-Timeout [s]", 102, 1, lambda r: r[0]),
            ("105 Modell", 105, 1, lambda r: r[0] & 0xFF),
            ("106 Seriennummer", 106, 6, as_str),
            ("112 Firmware", 112, 6, as_str),
            ("1000 Enable", 1000, 1, lambda r: r[0]),
            ("1001 Strom-Soll [A]", 1001, 1, lambda r: r[0] / 100),
            ("1002 Phasen", 1002, 1, lambda r: r[0]),
            ("1003 Spannungen [V]", 1003, 3, lambda r: [x / 100 for x in r]),
            ("1006 Ströme [A]", 1006, 3, lambda r: [x / 100 for x in r]),
            ("1009 Leistung [W]", 1009, 2, as_uint),
            ("1015 Zustand", 1015, 1, lambda r: r[0]),
            ("1016 Stecker (10=A,11=B,12=C)", 1016, 1, lambda r: r[0]),
            ("1017 Energie Ladevorgang [kWh]", 1017, 4, lambda r: as_uint(r) / 1000),
            ("1025 Energie gesamt [kWh]", 1025, 4, lambda r: as_uint(r) / 1000),
        ]
        for label, addr, cnt, conv in checks:
            try:
                regs = read_input(s, addr, cnt)
                print(f"  {label:34} = {conv(regs)}   raw={regs}")
            except Exception as e:  # noqa: BLE001
                print(f"  {label:34} FEHLER: {e}")


if __name__ == "__main__":
    main()
