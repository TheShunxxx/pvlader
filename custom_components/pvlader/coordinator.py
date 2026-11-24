"""DataUpdateCoordinator: liest Wallbox + HA-Zustände, regelt, erfasst Sessions."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CYCLE,
    DOMAIN,
    EFFICIENCY,
    HEARTBEAT_INTERVAL,
    MAX_CURRENT,
    MIN_CURRENT,
    MODE_OFF,
    MODE_PV,
    MODES,
    POWER_ON_THRESHOLD,
)
from .controller import Command, ControllerInputs, ControllerSettings, ControllerState, StatusInfo, power_at, step
from .dadapower import DadapowerClient, WallboxError, WallboxInfo, WallboxState
from .ha_const import (
    CONF_BATTERY_KWH,
    CONF_DISABLE_DELAY,
    CONF_ENABLE_DELAY,
    CONF_FEEDIN_PRICE,
    CONF_GRID_POWER,
    CONF_GRID_PRICE,
    CONF_GUARD,
    CONF_MAX_CURRENT,
    CONF_MIN_CURRENT,
    CONF_ODOMETER,
    CONF_PLUGGED,
    CONF_PV_POWER,
    CONF_RANGE,
    CONF_SOC,
    ENTITY_FALLBACKS,
    OPTION_DEFAULTS,
    SAVE_DELAY,
    STORAGE_VERSION,
)
from .session import COST_VERSION, ChargingSession, SessionTracker

type PvLaderConfigEntry = ConfigEntry[PvLaderCoordinator]

_LOGGER = logging.getLogger(__name__)


@dataclass
class UserSettings:
    """Über Entitäten änderbare, persistierte Einstellungen."""

    mode: str = MODE_PV
    limit_soc: float = 100.0
    plan_active: bool = False
    plan_target_soc: float = 80.0
    plan_time: datetime | None = None
    control_enabled: bool = False  # Sicherheitsschalter, Default AUS
    min_current: float = MIN_CURRENT
    max_current: float = MAX_CURRENT

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["plan_time"] = self.plan_time.isoformat() if self.plan_time else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UserSettings:
        data = dict(data or {})
        if data.get("plan_time"):
            data["plan_time"] = dt_util.parse_datetime(data["plan_time"])
        if data.get("mode") not in MODES:
            data.pop("mode", None)
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class PvLaderData:
    """Ergebnis eines Regelzyklus für die Entitäten."""

    wallbox: WallboxState
    grid_power_w: float | None
    pv_power_w: float | None
    soc: float | None
    vehicle_plugged: bool | None
    connected: bool
    command: Command
    info: StatusInfo
    control_enabled: bool
    remaining_min: float | None
    target: ControllerState  # Sollzustand laut Regler (im Schattenbetrieb hypothetisch)
    updated: datetime = field(default_factory=dt_util.utcnow)


class PvLaderCoordinator(DataUpdateCoordinator[PvLaderData]):
    """Ein Regelzyklus alle CYCLE Sekunden."""

    config_entry: PvLaderConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PvLaderConfigEntry,
        client: DadapowerClient,
        info: WallboxInfo,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=CYCLE),
        )
        self.client = client
        self.wallbox_info = info
        self.conf: dict[str, Any] = {**OPTION_DEFAULTS, **ENTITY_FALLBACKS, **entry.data, **entry.options}
        self.settings = UserSettings()
        self.tracker = SessionTracker(
            grid_price=float(self.conf[CONF_GRID_PRICE]), feedin_price=float(self.conf[CONF_FEEDIN_PRICE])
        )
        self._ctrl_state = ControllerState()
        # Ladeleistung der letzten ~60 s, um sie zeitlich zum Netzzähler passend zu wählen
        self._power_samples: deque[tuple[datetime, float]] = deque(maxlen=7)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        # Ladehistorie in eigener Datei: groß, ändert sich nur beim Session-Ende
        self._history_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.history"
        )
        self._save_pending = False
        self._loaded = False
        self._cycle_lock = asyncio.Lock()

    # --- Setup / Persistenz -------------------------------------------------

    async def async_load(self) -> None:
        """Gespeicherte Einstellungen + Sessions laden, Schreibmodus setzen, Heartbeat starten."""
        stored = await self._store.async_load() or {}
        history = await self._history_store.async_load()
        self.settings = UserSettings.from_dict(stored.get("settings"))
        sessions = dict(stored.get("sessions") or {})
        if history is not None:
            sessions["history"] = history.get("sessions", [])
            sessions["history_cost_version"] = history.get("cost_version", 1)
        history_outdated = history is None or int(history.get("cost_version", 1)) < COST_VERSION
        main_outdated = int(sessions.get("cost_version", 1)) < COST_VERSION
        self.tracker = SessionTracker.from_dict(
            sessions,
            grid_price=float(self.conf[CONF_GRID_PRICE]),
            feedin_price=float(self.conf[CONF_FEEDIN_PRICE]),
        )
        # Migration: Historie lag bisher im Haupt-Store bzw. Kosten nach alter Rechnung
        if self.tracker.history and history_outdated:
            await self.async_save_history()
        if stored and main_outdated:
            await self._store.async_save(self._data_to_save())
        self._loaded = True
        self.client.read_only = not self.settings.control_enabled
        if self.settings.control_enabled:
            await self.client.write_failsafe()
        self.config_entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._async_heartbeat, timedelta(seconds=HEARTBEAT_INTERVAL), name=f"{DOMAIN} heartbeat"
            )
        )

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        self._save_pending = False
        return {"settings": self.settings.to_dict(), "sessions": self.tracker.to_dict(include_history=False)}

    async def async_save_history(self) -> None:
        await self._history_store.async_save(
            {"sessions": self.tracker.history_to_list(), "cost_version": COST_VERSION}
        )

    @callback
    def _schedule_save(self) -> None:
        # async_delay_save verschiebt bei jedem Aufruf – daher nur einmal je Fenster planen
        if not self._save_pending:
            self._save_pending = True
            self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data_to_save())

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        async with self._cycle_lock:
            if self._loaded:  # nie ungeladene Defaults über den Store schreiben
                await self.async_save_now()
            await self.client.close()

    # --- Einstellungen ------------------------------------------------------

    def current_bounds(self) -> tuple[float, float]:
        """Installationsgrenzen aus den Optionen (min ≤ max, ≥ 6 A)."""
        lo = max(MIN_CURRENT, float(self.conf[CONF_MIN_CURRENT]))
        hi = max(lo, float(self.conf[CONF_MAX_CURRENT]))
        return lo, hi

    def effective_currents(self) -> tuple[float, float]:
        """Vom Nutzer gewählte Ströme, in die Installationsgrenzen geklemmt."""
        lo, hi = self.current_bounds()
        mn = min(hi, max(lo, self.settings.min_current))
        mx = min(hi, max(mn, self.settings.max_current))
        return mn, mx

    async def async_update_settings(self, **changes: Any) -> None:
        """Einstellung(en) ändern, speichern und sofort neu regeln."""
        if "control_enabled" in changes:
            await self.async_set_control_enabled(bool(changes.pop("control_enabled")))
        if "mode" in changes and changes["mode"] not in MODES:
            raise HomeAssistantError(f"Unbekannter Modus {changes['mode']!r}")
        for key, value in changes.items():
            setattr(self.settings, key, value)
        self._schedule_save()
        self.async_update_listeners()
        await self.async_request_refresh()

    async def async_set_plan(self, target_soc: float, when: datetime) -> None:
        await self.async_update_settings(plan_target_soc=float(target_soc), plan_time=when, plan_active=True)

    async def async_set_control_enabled(self, enabled: bool) -> None:
        """Sicherheitsschalter: AN = Failsafe schreiben + frisch regeln, AUS = nur noch lesen."""
        async with self._cycle_lock:
            if enabled == self.settings.control_enabled:
                return
            if enabled:
                self.client.read_only = False
                try:
                    await self.client.write_failsafe()
                except WallboxError as err:
                    self.client.read_only = True
                    raise HomeAssistantError(f"Wallbox nicht erreichbar: {err}") from err
                self._ctrl_state = ControllerState()
                _LOGGER.info("Steuerung aktiviert – pvlader schreibt jetzt auf die Wallbox")
            else:
                self.client.read_only = True
                _LOGGER.info("Steuerung deaktiviert – pvlader liest nur noch mit")
            self.settings.control_enabled = enabled
            await self.async_save_now()
        self.async_update_listeners()
        await self.async_request_refresh()

    # --- Zyklus -------------------------------------------------------------

    def _state(self, key: str) -> Any:
        entity_id = self.conf.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state

    def _float(self, key: str, *, power: bool = False) -> float | None:
        state = self._state(key)
        if state is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        if power and state.attributes.get("unit_of_measurement") == "kW":
            value *= 1000.0
        return value

    def _bool(self, key: str) -> bool | None:
        state = self._state(key)
        return None if state is None else state.state == STATE_ON

    async def _async_update_data(self) -> PvLaderData:
        async with self._cycle_lock:
            return await self._async_cycle()

    async def _async_cycle(self) -> PvLaderData:
        now = dt_util.now()
        try:
            wb = await self.client.read_state()
        except WallboxError as err:
            raise UpdateFailed(f"Wallbox nicht erreichbar: {err}") from err

        s = self.settings
        grid = self._float(CONF_GRID_POWER, power=True)
        grid_state = self._state(CONF_GRID_POWER)
        grid_ts = None if grid_state is None else (getattr(grid_state, "last_reported", None) or grid_state.last_updated)
        self._power_samples.append((now, wb.power_w))
        charge_at_grid = power_at(self._power_samples, grid_ts, wb.power_w)
        pv = self._float(CONF_PV_POWER, power=True)
        soc = self._float(CONF_SOC)
        plugged = self._bool(CONF_PLUGGED)
        # Steckerstatus der Box hat Vorrang; nur bei unbekanntem Code das Auto fragen
        connected = wb.connected if wb.plug is not None else bool(plugged)
        min_c, max_c = self.effective_currents()

        inp = ControllerInputs(
            now=now,
            mode=s.mode,
            grid_power_w=grid,
            charge_power_w=wb.power_w,
            charge_power_at_grid_w=charge_at_grid,
            plug=wb.plug,
            connected=connected,
            charging=wb.charging,
            active_phases=wb.phases_active,
            configured_phases=wb.phases_raw,
            current_setpoint=None if wb.current_setpoint_raw is None else float(wb.current_setpoint_raw),
            enabled=wb.enabled,
            soc=soc,
            limit_soc=s.limit_soc,
            min_current=min_c,
            max_current=max_c,
            plan_active=s.plan_active,
            plan_target_soc=s.plan_target_soc,
            plan_time=s.plan_time,
            battery_kwh=float(self.conf[CONF_BATTERY_KWH]),
            settings=ControllerSettings(
                enable_delay=float(self.conf[CONF_ENABLE_DELAY]),
                disable_delay=float(self.conf[CONF_DISABLE_DELAY]),
                guard=float(self.conf[CONF_GUARD]),
            ),
        )
        prev_state = self._ctrl_state
        cmd, self._ctrl_state, info = step(inp, prev_state)

        # Nur bei aktiver Steuerung schreiben; sonst Schattenbetrieb (Zustand läuft mit)
        if s.control_enabled and not cmd.empty:
            try:
                await self._async_apply(cmd)
            except WallboxError as err:
                # Zustand aus Messwerten neu aufbauen, Schaltsperre behalten
                self._ctrl_state = ControllerState(last_switch=self._ctrl_state.last_switch)
                raise UpdateFailed(f"Schreiben fehlgeschlagen: {err}") from err

        # Session: endet beim Abstecken bzw. bei Modus Aus unter eigener Steuerung
        active = connected and not (s.control_enabled and s.mode == MODE_OFF)
        finished = self.tracker.update(
            now,
            active,
            wb.power_w,
            grid,
            wb.session_wh,
            soc=soc,
            odometer=self._float(CONF_ODOMETER),
            range_km=self._float(CONF_RANGE),
        )
        if finished is not None:
            await self.async_save_history()
            await self.async_save_now()
        else:
            self._schedule_save()

        return PvLaderData(
            wallbox=wb,
            grid_power_w=grid,
            pv_power_w=pv,
            soc=soc,
            vehicle_plugged=plugged,
            connected=connected,
            command=cmd,
            info=info,
            control_enabled=s.control_enabled,
            remaining_min=self._remaining_min(wb.power_w, soc, info.plan_charging),
            target=self._ctrl_state,
        )

    async def _async_apply(self, cmd: Command) -> None:
        """Reihenfolge laut Controller: Phasen -> Strom -> Freigabe."""
        _LOGGER.debug("Kommando: %s", cmd)
        if cmd.phases is not None:
            await self.client.set_phases(cmd.phases)
        if cmd.current is not None:
            await self.client.set_current(cmd.current)
        if cmd.enable is not None:
            await self.client.set_enabled(cmd.enable)

    def _remaining_min(self, power_w: float, soc: float | None, plan_charging: bool) -> float | None:
        if soc is None or power_w <= POWER_ON_THRESHOLD:
            return None
        s = self.settings
        target = s.plan_target_soc if plan_charging else s.limit_soc
        if target <= soc:
            return 0.0
        kwh = (target - soc) / 100.0 * float(self.conf[CONF_BATTERY_KWH]) / EFFICIENCY
        return round(kwh * 1000.0 / power_w * 60.0)

    async def _async_heartbeat(self, _now: datetime) -> None:
        # Schreibt ggf. Register 102 – daher nur bei aktiver Steuerung
        if not self.settings.control_enabled:
            return
        try:
            await self.client.heartbeat()
        except WallboxError as err:
            _LOGGER.warning("Heartbeat fehlgeschlagen: %s", err)

    # --- Hilfen für Entitäten -----------------------------------------------

    @property
    def display_session(self) -> ChargingSession | None:
        """Laufende Session, sonst die zuletzt beendete."""
        if self.tracker.current is not None:
            return self.tracker.current
        return self.tracker.history[0] if self.tracker.history else None

    @property
    def feedin_price(self) -> float:
        return float(self.conf[CONF_FEEDIN_PRICE])
