"""Konstanten und Defaults für pvlader (ohne Home-Assistant-Abhängigkeit)."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "pvlader"

# Lademodi
MODE_OFF: Final = "off"
MODE_NOW: Final = "now"
MODE_PV: Final = "pv"
MODE_MINPV: Final = "minpv"
MODES: Final = (MODE_OFF, MODE_NOW, MODE_PV, MODE_MINPV)

# Elektrik
MIN_CURRENT: Final = 6.0  # A
MAX_CURRENT: Final = 16.0  # A
VOLTAGE: Final = 230.0  # V je Phase (Rechenwert)

# Regelung (Sekunden)
ENABLE_DELAY: Final = 60
DISABLE_DELAY: Final = 180
GUARD: Final = 300
CYCLE: Final = 10
CURRENT_THRESHOLD: Final = 0.5  # A, kleinere Änderungen werden nicht geschrieben
POWER_ON_THRESHOLD: Final = 100.0  # W, ab hier gilt "Auto zieht Strom"

# Fahrzeug / Plan
BATTERY_KWH: Final = 77.0
EFFICIENCY: Final = 0.9
PLAN_BUFFER: Final = timedelta(minutes=15)
SOC_HYSTERESIS: Final = 2.0  # Prozentpunkte

# Preise (EUR/kWh)
GRID_PRICE: Final = 0.298
FEEDIN_PRICE: Final = 0.082

# Sessions
SESSION_HISTORY_SIZE: Final = 3000  # eigene Store-Datei, wird nur beim Session-Ende geschrieben
MAX_INTEGRATION_DT: Final = 60.0  # s, größere Lücken werden gekappt

# Wallbox Dadapower
DEFAULT_PORT: Final = 502
DEFAULT_UNIT_ID: Final = 1
FAILSAFE_TIMEOUT: Final = 300  # s, wird beim Verbinden geschrieben
HEARTBEAT_INTERVAL: Final = 60  # s
# Schreibskala für Register 1001 (A × CURRENT_SCALE). Lt. Probe liefert das
# Zurücklesen per FC04 einen abweichenden Rohwert – daher bewusst konfigurierbar.
CURRENT_SCALE: Final = 100
PHASE_SWITCH_PAUSE: Final = 5.0  # s nach Disable
PHASE_SWITCH_SETTLE: Final = 2.0  # s nach Phasen-Write
