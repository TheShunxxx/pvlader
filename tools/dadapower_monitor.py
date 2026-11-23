"""Nur-lesender Mitschreiber für die Dadapower-Wallbox (FC04, schreibt NICHTS).

Schreibt alle N Sekunden eine CSV-Zeile, um zu beobachten, wie evcc die Box steuert.
Aufruf:  python tools/dadapower_monitor.py <host> <csv-datei> [intervall_s] [dauer_min]
"""
from __future__ import annotations

import csv
import socket
import struct
import sys
import time
from datetime import datetime

if len(sys.argv) < 3:
    sys.exit("Aufruf: python tools/dadapower_monitor.py <host> <csv-datei> [intervall_s] [dauer_min]")
HOST, OUT = sys.argv[1], sys.argv[2]
INTERVAL = float(sys.argv[3]) if len(sys.argv) > 3 else 5
DURATION = float(sys.argv[4]) if len(sys.argv) > 4 else 240
_tid = 0


def _recv(s: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("geschlossen")
        b += c
    return b


def rd(s: socket.socket, addr: int, cnt: int) -> list[int]:
    global _tid
    _tid = (_tid + 1) & 0xFFFF
    s.sendall(struct.pack(">HHHBBHH", _tid, 0, 6, 1, 4, addr, cnt))
    length = struct.unpack(">HHHB", _recv(s, 7))[2]
    body = _recv(s, length - 1)
    if body[0] & 0x80:
        raise RuntimeError(f"Exception {body[1]} @ {addr}")
    return list(struct.unpack(f">{body[1] // 2}H", body[2 : 2 + body[1]]))


def u(regs: list[int]) -> int:
    v = 0
    for r in regs:
        v = (v << 16) | r
    return v


HEAD = ["zeit", "r102", "r1000_enable", "r1001_raw", "r1002_phasen", "u1", "u2", "u3",
        "i1", "i2", "i3", "leistung_w", "r1015_zustand", "r1016_stecker", "session_wh", "total_wh"]


def main() -> None:
    end = time.time() + DURATION * 60
    sock = None
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if f.tell() == 0:
            w.writerow(HEAD)
        last = None
        while time.time() < end:
            try:
                if sock is None:
                    sock = socket.create_connection((HOST, 502), timeout=5)
                a = rd(sock, 1000, 11)  # 1000..1010
                b = rd(sock, 1015, 6)   # 1015..1020
                t = rd(sock, 1025, 4)
                hb = rd(sock, 102, 1)[0]
                row = [hb, a[0], a[1], a[2], *(x / 100 for x in a[3:6]), *(x / 100 for x in a[6:9]),
                       u(a[9:11]), b[0], b[1], u(b[2:6]), u(t)]
                # nur Änderungen + jede Minute eine Zeile, damit die Datei klein bleibt
                key = tuple(row[:5]) + (round(row[11] / 200),) + tuple(row[12:14])
                if key != last or int(time.time()) % 60 < INTERVAL:
                    w.writerow([datetime.now().isoformat(timespec="seconds"), *row])
                    f.flush()
                    last = key
            except Exception as e:  # noqa: BLE001
                w.writerow([datetime.now().isoformat(timespec="seconds"), f"FEHLER {e}"])
                f.flush()
                if sock:
                    sock.close()
                sock = None
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
