/**
 * PV-Lader – Dashboard-Karte (`custom:pvlader-card`) und Seitenreiter (`pvlader-panel`).
 * Ohne Build-Schritt und ohne externe Bibliotheken; Daten über Entitäten + WebSocket-API
 * `pvlader/config` und `pvlader/sessions`.
 */

const LOCALE = "de-DE";
const MODES = [
  { key: "off", label: "Aus", icon: "mdi:power" },
  { key: "pv", label: "PV", icon: "mdi:white-balance-sunny" },
  { key: "minpv", label: "Min+PV", icon: "mdi:weather-partly-cloudy" },
  { key: "now", label: "Sofort", icon: "mdi:lightning-bolt" },
];
const MIN_SESSION_KWH = 0.01; // Vorgänge ohne Energie (nur angesteckt) ausblenden

// ---------------------------------------------------------------------------
// Hilfen
// ---------------------------------------------------------------------------

const _nf = {};
function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  _nf[digits] ??= new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return _nf[digits].format(value);
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function num(stateObj) {
  if (!stateObj) return null;
  const v = parseFloat(stateObj.state);
  return Number.isFinite(v) ? v : null;
}

/** Leistung in W (kW-Sensoren werden umgerechnet). */
function watts(stateObj) {
  const v = num(stateObj);
  if (v === null) return null;
  return stateObj.attributes?.unit_of_measurement === "kW" ? v * 1000 : v;
}

function duration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "–";
  const m = Math.round(seconds / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

function durationLong(minutes) {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return "–";
  const m = Math.round(minutes);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

function dateTime(d) {
  return d.toLocaleString(LOCALE, { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function timeOnly(d) {
  return d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

/** Date -> Wert für <input type="datetime-local"> (Ortszeit). */
function toLocalInput(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function notify(el, message) {
  el.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
}

function navigate(path) {
  history.pushState(null, "", path);
  window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
}

// Konfiguration (Entity-IDs, Preise) einmal je Seite laden und teilen
let _configPromise = null;
function loadConfig(hass) {
  if (!_configPromise) {
    _configPromise = hass.callWS({ type: "pvlader/config" }).then((res) => {
      if (!res.entries.length) _configPromise = null; // Integration evtl. noch nicht geladen
      return res;
    }, (err) => {
      _configPromise = null;
      throw err;
    });
  }
  return _configPromise;
}

function pickEntry(res, entryId) {
  return res.entries.find((e) => !entryId || e.entry_id === entryId) ?? null;
}

/** Session aus der WebSocket-API in Anzeigewerte umrechnen. */
function normSession(s, running = false) {
  const kwh = (s.energy_wh ?? 0) / 1000;
  const dur = s.charge_s > 0 ? s.charge_s : null;
  const has = (a, b) => a !== null && a !== undefined && b !== null && b !== undefined;
  return {
    start: new Date(s.start),
    end: s.end ? new Date(s.end) : null,
    running,
    kwh,
    solarKwh: (s.solar_wh ?? 0) / 1000,
    gridKwh: (s.grid_wh ?? 0) / 1000,
    cost: s.cost_eur ?? 0,
    solarPct: kwh > 0 ? (s.solar_wh / s.energy_wh) * 100 : null,
    price: kwh > 0 ? (s.cost_eur / kwh) * 100 : null,
    dur,
    socStart: s.soc_start ?? null,
    socEnd: s.soc_end ?? null,
    socDelta: has(s.soc_start, s.soc_end) ? s.soc_end - s.soc_start : null,
    rangeDelta: has(s.range_start_km, s.range_end_km) ? s.range_end_km - s.range_start_km : null,
    odo: s.odometer_km ?? null,
    avgKw: dur ? kwh / (dur / 3600) : null,
  };
}

function summarize(list, gridPrice, feedinPrice) {
  const sum = (f) => list.reduce((a, s) => a + (f(s) ?? 0), 0);
  const kwh = sum((s) => s.kwh);
  const solar = sum((s) => s.solarKwh);
  const cost = sum((s) => s.cost);
  const withDur = list.filter((s) => s.dur);
  const durS = sum((s) => s.dur);
  const durKwh = withDur.reduce((a, s) => a + s.kwh, 0);
  const anySoc = list.some((s) => s.socDelta !== null);
  const anyRange = list.some((s) => s.rangeDelta !== null);
  return {
    count: list.length,
    kwh,
    solar,
    grid: sum((s) => s.gridKwh),
    cost,
    solarPct: kwh > 0 ? (solar / kwh) * 100 : null,
    price: kwh > 0 ? (cost / kwh) * 100 : null,
    savings: solar * (gridPrice - feedinPrice),
    dur: withDur.length ? durS : null,
    avgKw: durS > 0 ? durKwh / (durS / 3600) : null,
    socDelta: anySoc ? sum((s) => s.socDelta) : null,
    rangeDelta: anyRange ? sum((s) => s.rangeDelta) : null,
  };
}

const COLORS = `
  :host {
    --pvl-solar: #0fde41;
    --pvl-grid: #28293e;
    --pvl-export: #fad000;
    --pvl-home: #2f80ed;
    --pvl-radius: 12px;
  }
  :host([dark]) {
    --pvl-grid: #8e97b8;
    --pvl-home: #5c9dff;
  }
`;

// ---------------------------------------------------------------------------
// Dashboard-Karte
// ---------------------------------------------------------------------------

const CARD_KEYS = [
  "mode", "limit_soc", "plan_target_soc", "plan_time", "plan_active", "control_enabled", "connected", "charging",
  "plan_charging", "charge_power", "charge_current", "phases_active", "control_reason", "session_energy",
  "session_solar_percent", "session_cost", "remaining_time", "planned_start",
];

const CARD_STYLE = `
  ${COLORS}
  ha-card { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { font-size: 1.2rem; font-weight: 600; letter-spacing: .02em; text-transform: uppercase; }
  .pills { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .pill { border-radius: 999px; padding: 2px 10px; font-size: .78rem; border: 1px solid var(--divider-color); color: var(--secondary-text-color); white-space: nowrap; }
  .pill.on { border-color: var(--primary-color); color: var(--primary-color); }
  .pill.charging { border-color: var(--pvl-solar); color: var(--primary-text-color); background: color-mix(in srgb, var(--pvl-solar) 18%, transparent); }
  .pill.warn { border-color: var(--warning-color, #ffa600); color: var(--warning-color, #ffa600); }
  .modes { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .mode { border: 1px solid var(--divider-color); border-radius: var(--pvl-radius); padding: 10px 4px 8px; background: none;
          color: var(--primary-text-color); display: flex; flex-direction: column; align-items: center; gap: 4px;
          cursor: pointer; font: inherit; font-size: .85rem; }
  .mode:hover { border-color: var(--primary-color); }
  .mode.active { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .mode:disabled { opacity: .5; cursor: default; }
  .vehicle { display: flex; align-items: center; gap: 8px; font-weight: 500; }
  .vehicle .grow { flex: 1; }
  .muted { color: var(--secondary-text-color); font-size: .85rem; }
  .socbar { position: relative; height: 12px; border-radius: 6px; background: var(--secondary-background-color, #eee); overflow: visible; }
  .socfill { position: absolute; inset: 0 auto 0 0; border-radius: 6px; background: var(--primary-color); transition: width .4s; }
  .socfill.charging { background: var(--pvl-solar); }
  .marker { position: absolute; top: -4px; width: 4px; height: 20px; border-radius: 2px; background: var(--pvl-solar); transform: translateX(-2px); }
  .marker.plan { background: var(--primary-color); }
  .power { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .power .big { font-size: 2rem; font-weight: 600; }
  .reason { font-size: .85rem; color: var(--secondary-text-color); }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .stats.four { grid-template-columns: repeat(4, 1fr); }
  .stat .label { font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
  .stat .value { font-size: 1.05rem; font-weight: 600; }
  .row { display: flex; align-items: center; gap: 8px 12px; flex-wrap: wrap; }
  .row label { min-width: 84px; font-size: .9rem; }
  .row input[type=range] { flex: 1 1 100px; min-width: 0; accent-color: var(--primary-color); }
  .row .val { min-width: 44px; text-align: right; font-weight: 600; }
  .section { border-top: 1px solid var(--divider-color); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
  .section h3 { margin: 0; font-size: .8rem; letter-spacing: .06em; text-transform: uppercase; color: var(--secondary-text-color); font-weight: 600; }
  input[type=datetime-local] { flex: 1 1 170px; min-width: 0; max-width: 100%; font: inherit; padding: 6px 8px; border-radius: 8px; border: 1px solid var(--divider-color);
          background: var(--card-background-color); color: var(--primary-text-color); color-scheme: light dark; }
  .estimate { display: flex; justify-content: space-between; font-size: .85rem; }
  .estimate b { color: var(--primary-text-color); }
  .btn { border: none; border-radius: 10px; padding: 10px 14px; font: inherit; font-weight: 600; cursor: pointer;
         background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .btn.secondary { background: none; color: var(--primary-color); border: 1px solid var(--primary-color); }
  .btn:disabled { opacity: .5; cursor: default; }
  .plan-active { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .link { align-self: flex-end; background: none; border: none; color: var(--primary-color); cursor: pointer; font: inherit; font-size: .9rem; padding: 0; }
  .error { color: var(--error-color, #db4437); }
`;

class PvLaderCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._info = null;
    this._sig = null;
    this._busy = false;
    this._draft = null; // Plan-Formular (vom Nutzer bearbeitet)
  }

  static getStubConfig() {
    return { title: "Garage", vehicle_name: "Model Y" };
  }

  setConfig(config) {
    this._config = { show_plan: true, show_session: true, show_link: true, ...(config || {}) };
    this._sig = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureInfo();
    this._render();
  }

  getCardSize() {
    return 10;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _ensureInfo() {
    if (this._info || this._loading || !this._hass) return;
    this._loading = true;
    loadConfig(this._hass)
      .then((res) => {
        this._info = pickEntry(res, this._config?.entry_id);
        this._error = this._info ? null : "PV-Lader-Integration nicht gefunden";
        if (!this._info) setTimeout(() => this._ensureInfo(), 10000);
      })
      .catch((err) => {
        this._error = `PV-Lader nicht erreichbar: ${err.message ?? err}`;
        setTimeout(() => this._ensureInfo(), 10000);
      })
      .finally(() => {
        this._loading = false;
        this._sig = null;
        this._render();
      });
  }

  _st(key) {
    const id = this._info?.entities[key];
    return id ? this._hass.states[id] : undefined;
  }

  _signature() {
    const parts = CARD_KEYS.map((k) => {
      const s = this._st(k);
      return s ? `${s.state}|${s.last_updated}` : "";
    });
    const soc = this._info?.sources.soc && this._hass.states[this._info.sources.soc];
    const range = this._info?.sources.range && this._hass.states[this._info.sources.range];
    parts.push(soc?.state, range?.state, JSON.stringify(this._draft), this._error, this._hass?.themes?.darkMode);
    return parts.join("#");
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (this._busy) {
      this._pending = true;
      return;
    }
    const sig = this._signature();
    if (sig === this._sig) return;
    this._sig = sig;
    this.toggleAttribute("dark", !!this._hass.themes?.darkMode);

    if (!this._info) {
      this.shadowRoot.innerHTML = `<style>${CARD_STYLE}</style><ha-card><div class="${this._error ? "error" : "muted"}">${esc(this._error ?? "Lade PV-Lader …")}</div></ha-card>`;
      return;
    }

    const c = this._config;
    const info = this._info;
    const st = (k) => this._st(k);
    const modeState = st("mode");
    const mode = modeState?.state;
    const connected = st("connected")?.state === "on";
    const charging = st("charging")?.state === "on";
    const planCharging = st("plan_charging")?.state === "on";
    const control = st("control_enabled")?.state === "on";
    const available = modeState && modeState.state !== "unavailable";
    const powerW = watts(st("charge_power")) ?? 0;
    const phases = num(st("phases_active"));
    const current = num(st("charge_current"));
    const socObj = info.sources.soc ? this._hass.states[info.sources.soc] : null;
    const soc = num(socObj);
    const range = info.sources.range ? num(this._hass.states[info.sources.range]) : null;
    const limit = num(st("limit_soc"));
    const planActive = st("plan_active")?.state === "on";
    const planSoc = num(st("plan_target_soc"));
    const reason = st("control_reason")?.state;
    const remaining = num(st("remaining_time"));

    let status = { text: "Getrennt", cls: "" };
    if (!available) status = { text: "Nicht verfügbar", cls: "warn" };
    else if (charging) status = { text: planCharging ? "Lädt (Plan)" : "Lädt", cls: "charging" };
    else if (connected) status = { text: "Verbunden", cls: "on" };

    const modes = MODES.map(
      (m) => `<button class="mode ${m.key === mode ? "active" : ""}" data-mode="${m.key}" ${available ? "" : "disabled"}>
        <ha-icon icon="${m.icon}"></ha-icon><span>${m.label}</span></button>`,
    ).join("");

    const markers = [
      limit !== null && limit < 100 ? `<div class="marker" style="left:${limit}%" title="Ladegrenze ${fmt(limit, 0)} %"></div>` : "",
      planActive && planSoc !== null ? `<div class="marker plan" style="left:${planSoc}%" title="Planziel ${fmt(planSoc, 0)} %"></div>` : "",
    ].join("");

    const detail = [
      phases ? `${fmt(phases, 0)}-phasig` : null,
      charging && current !== null ? `${fmt(current, 1)} A` : null,
    ].filter(Boolean).join(" · ");

    const sessionKwh = num(st("session_energy"));
    const sessionEnded = !!st("session_energy")?.attributes?.end;

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title">${esc(c.title ?? info.title)}</div>
          <div class="pills">
            ${control ? "" : `<span class="pill warn" title="„Steuerung aktiv“ ist AUS – PV-Lader liest nur mit und schreibt nichts auf die Wallbox.">Nur mitlesen</span>`}
            <span class="pill ${status.cls}">${status.text}</span>
          </div>
        </div>
        <div class="modes">${modes}</div>
        <div class="vehicle">
          <ha-icon icon="mdi:car-electric"></ha-icon>
          <span class="grow">${esc(c.vehicle_name ?? "Fahrzeug")}</span>
          <span class="muted"><ha-icon icon="mdi:battery-high" style="--mdc-icon-size:16px"></ha-icon> ${fmt(soc, 0)} %</span>
          ${range !== null ? `<span class="muted">${fmt(range, 0)} km</span>` : ""}
        </div>
        <div class="socbar"><div class="socfill ${charging ? "charging" : ""}" style="width:${Math.max(0, Math.min(100, soc ?? 0))}%"></div>${markers}</div>
        <div class="power">
          <span class="big">${fmt(powerW / 1000, 1)} kW</span>
          <span class="muted">${esc(detail)}</span>
        </div>
        ${reason ? `<div class="reason">${esc(reason)}</div>` : ""}
        <div class="stats">
          <div class="stat"><div class="label">${sessionEnded ? "Zuletzt geladen" : "Geladen"}</div><div class="value">${fmt(sessionKwh, sessionKwh !== null && sessionKwh < 10 ? 2 : 1)} kWh</div></div>
          <div class="stat"><div class="label">Solar</div><div class="value">${fmt(num(st("session_solar_percent")), 0)} %</div></div>
          <div class="stat"><div class="label">Restzeit</div><div class="value">${charging && remaining !== null ? durationLong(remaining) : "–"}</div></div>
        </div>
        <div class="row">
          <label>Ladegrenze</label>
          <input type="range" min="0" max="100" step="5" value="${limit ?? 100}" data-number="limit_soc" ${available ? "" : "disabled"}>
          <span class="val" data-val="limit_soc">${fmt(limit, 0)} %</span>
        </div>
        ${c.show_plan ? this._planSection(soc, planActive, planSoc, available) : ""}
        ${c.show_session ? this._sessionSection() : ""}
        ${c.show_link ? `<button class="link" data-nav>Auswertung →</button>` : ""}
      </ha-card>`;
    this._bind();
  }

  _planSection(soc, planActive, planSoc, available) {
    const planTimeState = this._st("plan_time")?.state;
    const planTime = planTimeState && !["unknown", "unavailable"].includes(planTimeState) ? new Date(planTimeState) : null;
    if (planActive && planTime) {
      const startState = this._st("planned_start")?.state;
      const start = startState && !["unknown", "unavailable"].includes(startState) ? new Date(startState) : null;
      return `
        <div class="section">
          <h3>Ladeplanung</h3>
          <div class="plan-active">
            <div><b>${fmt(planSoc, 0)} %</b> bis <b>${esc(dateTime(planTime))}</b>
              <div class="muted">${start ? `Start ca. ${esc(timeOnly(start))}` : "Startzeit wird berechnet"}</div></div>
            <button class="btn secondary" data-plan="clear" ${available ? "" : "disabled"}>Plan löschen</button>
          </div>
        </div>`;
    }
    const d = this._draftValues(planTime, planSoc);
    return `
      <div class="section">
        <h3>Ladeplanung</h3>
        <div class="row"><label>Fertig bis</label><input type="datetime-local" data-draft="time" value="${d.time}"></div>
        <div class="row">
          <label>Ziel-SoC</label>
          <input type="range" min="10" max="100" step="5" value="${d.soc}" data-draft="soc">
          <span class="val" data-val="draft_soc">${fmt(d.soc, 0)} %</span>
        </div>
        <div class="estimate muted" data-estimate>${this._estimate(soc, d.soc)}</div>
        <button class="btn" data-plan="set" ${available ? "" : "disabled"}>Plan setzen</button>
      </div>`;
  }

  _draftValues(planTime, planSoc) {
    if (this._draft) return this._draft;
    let time = planTime && planTime > new Date() ? planTime : null;
    if (!time) {
      time = new Date();
      time.setDate(time.getDate() + 1);
      time.setHours(7, 0, 0, 0);
    }
    return { time: toLocalInput(time), soc: planSoc ?? 80 };
  }

  _estimate(soc, target) {
    const info = this._info;
    if (soc === null) return "Ladestand unbekannt";
    const kwh = (Math.max(0, target - soc) / 100) * info.battery_kwh / info.efficiency;
    const kw = info.max_power_w / 1000;
    const minutes = (kwh / kw) * 60;
    return `<span>Dauer <b>${durationLong(minutes)}</b> @ ${fmt(kw, 0)} kW</span><span>${fmt(kwh, 1)} kWh · max. ${fmt(kwh * info.grid_price, 2)} €</span>`;
  }

  _sessionSection() {
    const e = this._st("session_energy");
    if (!e || e.state === "unknown" || e.state === "unavailable") return "";
    const kwh = num(e);
    const cost = num(this._st("session_cost"));
    const solar = num(this._st("session_solar_percent"));
    const running = !e.attributes.end;
    return `
      <div class="section">
        <h3>${running ? "Aktueller Ladevorgang" : "Letzter Ladevorgang"}</h3>
        <div class="stats four">
          <div class="stat"><div class="label">Energie</div><div class="value">${fmt(kwh, 2)} kWh</div></div>
          <div class="stat"><div class="label">Kosten</div><div class="value">${fmt(cost, 2)} €</div></div>
          <div class="stat"><div class="label">Preis/kWh</div><div class="value">${kwh > 0 && cost !== null ? fmt((cost / kwh) * 100, 1) + " ct" : "–"}</div></div>
          <div class="stat"><div class="label">PV-Anteil</div><div class="value">${fmt(solar, 0)} %</div></div>
        </div>
      </div>`;
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-mode]").forEach((b) =>
      b.addEventListener("click", () => this._call("select", "select_option", "mode", { option: b.dataset.mode })),
    );
    root.querySelectorAll("input[type=range]").forEach((input) => {
      const hold = () => { this._busy = true; };
      const release = () => setTimeout(() => this._release(), 300);
      input.addEventListener("pointerdown", hold);
      input.addEventListener("pointerup", release);
      input.addEventListener("pointercancel", release);
    });
    const limit = root.querySelector("[data-number=limit_soc]");
    limit?.addEventListener("input", () => { root.querySelector("[data-val=limit_soc]").textContent = `${fmt(+limit.value, 0)} %`; });
    limit?.addEventListener("change", () => {
      this._release();
      this._call("number", "set_value", "limit_soc", { value: +limit.value });
    });

    const timeInput = root.querySelector("[data-draft=time]");
    const socInput = root.querySelector("[data-draft=soc]");
    const syncDraft = () => {
      this._draft = { time: timeInput.value, soc: +socInput.value };
      root.querySelector("[data-val=draft_soc]").textContent = `${fmt(+socInput.value, 0)} %`;
      const soc = this._info.sources.soc ? num(this._hass.states[this._info.sources.soc]) : null;
      root.querySelector("[data-estimate]").innerHTML = this._estimate(soc, +socInput.value);
    };
    timeInput?.addEventListener("focus", () => { this._busy = true; });
    timeInput?.addEventListener("blur", () => this._release());
    timeInput?.addEventListener("input", syncDraft);
    socInput?.addEventListener("input", syncDraft);
    socInput?.addEventListener("change", () => { syncDraft(); this._release(); });

    root.querySelector("[data-plan=set]")?.addEventListener("click", () => this._setPlan(timeInput.value, +socInput.value));
    root.querySelector("[data-plan=clear]")?.addEventListener("click", () => this._call("switch", "turn_off", "plan_active", {}));
    root.querySelector("[data-nav]")?.addEventListener("click", () => navigate("/pvlader"));
  }

  _release() {
    if (!this._busy) return;
    this._busy = false;
    if (this._pending) {
      this._pending = false;
      this._render();
    }
  }

  async _call(domain, service, key, data) {
    const entityId = this._info?.entities[key];
    if (!entityId) return;
    try {
      await this._hass.callService(domain, service, { entity_id: entityId, ...data });
    } catch (err) {
      notify(this, `PV-Lader: ${err.message ?? err}`);
    }
  }

  async _setPlan(time, soc) {
    if (!time) {
      notify(this, "Bitte eine Zielzeit wählen.");
      return;
    }
    if (new Date(time) <= new Date()) {
      notify(this, "Die Zielzeit liegt in der Vergangenheit.");
      return;
    }
    try {
      await this._hass.callService("pvlader", "set_plan", {
        target_soc: soc,
        time: `${time}:00`,
        config_entry_id: this._info.entry_id,
      });
      this._draft = null;
    } catch (err) {
      notify(this, `Plan nicht gesetzt: ${err.message ?? err}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Diagramm (SVG, gestapelte Balken)
// ---------------------------------------------------------------------------

function niceStep(raw) {
  if (raw <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * exp;
}

/**
 * buckets: [{ key, label, parts: [{ value, color, name }], title }]
 */
function barChart(buckets, width, unit, digits) {
  const height = 240;
  const pad = { l: 8, r: 44, t: 24, b: 26 };
  const w = Math.max(200, width);
  const plotW = w - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const totals = buckets.map((b) => b.parts.reduce((a, p) => a + p.value, 0));
  const step = niceStep(Math.max(0, ...totals) / 4);
  const max = Math.max(step, Math.ceil(Math.max(0, ...totals) / step - 1e-9) * step);
  const slot = plotW / Math.max(1, buckets.length);
  const barW = Math.max(2, Math.min(36, slot * 0.62));
  const y = (v) => pad.t + plotH - (v / max) * plotH;
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const tickDigits = step >= 1 ? (Number.isInteger(step) ? 0 : 1) : step >= 0.1 ? (Number.isInteger(step * 10) ? 1 : 2) : 2;
  const every = slot >= 18 ? 1 : slot >= 9 ? 2 : 5;

  let svg = `<svg width="${w}" height="${height}" viewBox="0 0 ${w} ${height}" role="img">`;
  svg += `<text x="${w - 2}" y="10" text-anchor="end" class="axis small">${unit}</text>`;
  for (const t of ticks) {
    svg += `<line x1="${pad.l}" x2="${pad.l + plotW}" y1="${y(t)}" y2="${y(t)}" class="gridline"/>`;
    svg += `<text x="${w - 2}" y="${y(t) + 4}" text-anchor="end" class="axis">${fmt(t, tickDigits)}</text>`;
  }
  buckets.forEach((b, i) => {
    const cx = pad.l + slot * i + slot / 2;
    let acc = 0;
    const segs = b.parts.filter((p) => p.value > 0);
    segs.forEach((p, j) => {
      const y0 = y(acc);
      const y1 = y(acc + p.value);
      acc += p.value;
      const top = j === segs.length - 1;
      const h = Math.max(top ? 1.5 : 0, y0 - y1);
      const r = top ? Math.min(4, barW / 2, h) : 0;
      // oben abgerundet: Pfad statt rect
      const x0 = cx - barW / 2;
      const yTop = y0 - h;
      svg += `<path d="M${x0},${y0} V${yTop + r} Q${x0},${yTop} ${x0 + r},${yTop} H${x0 + barW - r} Q${x0 + barW},${yTop} ${x0 + barW},${yTop + r} V${y0} Z" fill="${p.color}"/>`;
    });
    const tip = b.title ?? `${b.label}: ${fmt(totals[i], digits)} ${unit}`;
    svg += `<rect x="${pad.l + slot * i}" y="${pad.t}" width="${slot}" height="${plotH}" fill="transparent" class="hit" data-key="${b.key}"><title>${esc(tip)}</title></rect>`;
    if (i % every === 0 || buckets.length <= 12) {
      svg += `<text x="${cx}" y="${height - 8}" text-anchor="middle" class="axis">${esc(b.label)}</text>`;
    }
  });
  svg += "</svg>";
  return svg;
}

// ---------------------------------------------------------------------------
// Seitenreiter
// ---------------------------------------------------------------------------

const PANEL_STYLE = `
  ${COLORS}
  :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family, inherit); }
  .toolbar { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px);
             padding: 0 12px; background: var(--app-header-background-color, var(--primary-color));
             color: var(--app-header-text-color, var(--text-primary-color, #fff)); border-bottom: var(--app-header-border-bottom, none); }
  .toolbar .name { font-size: 1.25rem; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tabs { display: flex; gap: 4px; }
  .tabs button { background: none; border: none; color: inherit; font: inherit; padding: 8px 12px; border-radius: 8px; cursor: pointer; opacity: .75;
                 display: flex; align-items: center; gap: 6px; }
  .tabs button.active { opacity: 1; background: rgba(255,255,255,.16); font-weight: 600; }
  .content { max-width: 1240px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; box-sizing: border-box; }
  section[hidden] { display: none !important; }
  .grid2 { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 16px; align-items: start; }
  .col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
  ha-card { padding: 16px 18px; display: block; }
  h2 { margin: 0 0 12px; font-size: 1.1rem; font-weight: 500; }
  h2 .sub { color: var(--secondary-text-color); font-size: .85rem; font-weight: 400; margin-left: 6px; }
  .muted { color: var(--secondary-text-color); }
  .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 999px; padding: 3px; gap: 2px; background: var(--card-background-color); }
  .seg button { border: none; background: none; color: var(--primary-text-color); font: inherit; padding: 6px 16px; border-radius: 999px; cursor: pointer; }
  .seg button.active { background: var(--primary-text-color); color: var(--card-background-color); font-weight: 600; }
  .controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
  .nav { display: inline-flex; align-items: center; gap: 4px; }
  .nav button { border: none; background: none; color: var(--primary-text-color); font-size: 1.2rem; cursor: pointer; padding: 4px 10px; border-radius: 8px; }
  .nav button:disabled { opacity: .3; cursor: default; }
  .nav .label { min-width: 140px; text-align: center; font-weight: 500; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 12px; }
  .tile { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px); padding: 14px 16px;
          box-shadow: var(--ha-card-box-shadow, none); border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color)); }
  .tile .label { font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); }
  .tile .value { font-size: 1.4rem; font-weight: 600; margin-top: 4px; }
  .tile .hint { font-size: .8rem; color: var(--secondary-text-color); margin-top: 2px; }
  .chart { width: 100%; overflow: hidden; }
  .chart svg { display: block; }
  .chart .gridline { stroke: var(--divider-color); stroke-width: 1; }
  .chart .axis { fill: var(--secondary-text-color); font-size: 11px; }
  .chart .axis.small { font-size: 10px; }
  .chart .hit { cursor: pointer; }
  .chart .hit:hover { fill: color-mix(in srgb, var(--primary-text-color) 6%, transparent); }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 8px; font-size: .9rem; }
  .dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
  .tablewrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: .9rem; white-space: nowrap; }
  th { text-align: right; font-weight: 600; padding: 8px 10px; vertical-align: bottom; }
  th .unit { display: block; font-weight: 400; font-size: .75rem; color: var(--secondary-text-color); }
  td { text-align: right; padding: 9px 10px; border-top: 1px solid var(--divider-color); font-variant-numeric: tabular-nums; }
  th:first-child, td:first-child { text-align: left; }
  tr.total td { font-weight: 700; }
  .badge { font-size: .72rem; border-radius: 999px; padding: 1px 8px; margin-left: 6px; background: color-mix(in srgb, var(--pvl-solar) 25%, transparent); }
  .tablehead { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .btn { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font: inherit; border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .flowbar { display: flex; height: 34px; border-radius: 8px; overflow: hidden; margin: 6px 0 2px; background: var(--secondary-background-color); }
  .flowbar div { display: flex; align-items: center; justify-content: center; font-size: .8rem; color: #fff; white-space: nowrap; overflow: hidden; transition: flex-basis .5s; }
  .flowbar .solar, .flowbar .export { color: #111; }
  .flowcap { display: flex; justify-content: space-between; font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; color: var(--secondary-text-color); margin-top: 10px; }
  .flowlists { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr)); gap: 12px 24px; margin-top: 16px; }
  @media (max-width: 520px) {
    .content { padding: 10px; gap: 12px; }
    ha-card { padding: 14px 12px; }
    .tabs button span { display: none; }
    .flowrow ha-icon, .recent .p.solar { display: none; }
    .nav .label { min-width: 110px; }
    .tiles { gap: 8px; }
    .tile { padding: 10px 12px; }
    .tile .value { font-size: 1.15rem; }
  }
  .flowlists h3 { display: flex; justify-content: space-between; margin: 0 0 6px; font-size: 1rem; }
  .flowrow { display: flex; align-items: center; gap: 10px; padding: 5px 0; min-width: 0; }
  .flowrow > * { flex-shrink: 0; }
  .flowrow .n { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .flowrow .p { color: var(--secondary-text-color); font-size: .85rem; white-space: nowrap; }
  .flowrow .v { font-weight: 600; min-width: 56px; text-align: right; white-space: nowrap; }
  .flowrow.zero { opacity: .5; }
  .empty { padding: 24px 0; text-align: center; color: var(--secondary-text-color); }
  .recent .flowrow { border-top: 1px solid var(--divider-color); }
  .recent .flowrow:first-of-type { border-top: none; }
  select { font: inherit; padding: 4px 8px; border-radius: 8px; }
`;

const MONTHS_SHORT = Array.from({ length: 12 }, (_, i) => new Date(2000, i, 1).toLocaleString(LOCALE, { month: "short" }).replace(".", ""));
const MONTHS_LONG = Array.from({ length: 12 }, (_, i) => new Date(2000, i, 1).toLocaleString(LOCALE, { month: "long" }));

class PvLaderPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const now = new Date();
    this._tab = this._remember("tab") ?? "laden";
    this._period = this._remember("period") ?? "month";
    this._metric = "energy";
    this._year = now.getFullYear();
    this._month = now.getMonth();
    this._sessions = null;
    this._current = null;
    this._chartWidth = 800;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
    this._onHass();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(_p) {}
  set route(_r) {}

  connectedCallback() {
    this._timer = setInterval(() => this._fetchSessions(), 60000);
    if (this._chartEl && this._ro) this._ro.observe(this._chartEl);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._ro?.disconnect();
  }

  _remember(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(`pvlader.${key}`);
      localStorage.setItem(`pvlader.${key}`, value);
    } catch (_e) {
      /* privates Fenster o. Ä. */
    }
    return null;
  }

  _init() {
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${PANEL_STYLE}</style>
      <div class="toolbar">
        <span id="menu"></span>
        <div class="name">PV-Lader</div>
        <span id="entrysel"></span>
        <div class="tabs">
          <button data-tab="laden"><ha-icon icon="mdi:lightning-bolt"></ha-icon><span>Laden</span></button>
          <button data-tab="ladungen"><ha-icon icon="mdi:format-list-checks"></ha-icon><span>Ladungen</span></button>
        </div>
      </div>
      <div class="content">
        <section id="tab-laden">
          <div class="grid2">
            <div class="col">
              <ha-card><h2>Energiefluss</h2><div id="flow"></div></ha-card>
              <ha-card class="recent"><h2>Letzte Ladevorgänge</h2><div id="recent"></div></ha-card>
            </div>
            <div class="col" id="cardslot"></div>
          </div>
        </section>
        <section id="tab-ladungen">
          <div class="controls" id="controls"></div>
          <div class="tiles" id="tiles"></div>
          <ha-card>
            <h2 id="charthead"></h2>
            <div class="chart" id="chart"></div>
            <div class="legend" id="legend"></div>
          </ha-card>
          <ha-card>
            <div class="tablehead"><h2>Übersicht</h2><button class="btn" id="csv"><ha-icon icon="mdi:download" style="--mdc-icon-size:18px"></ha-icon> CSV</button></div>
            <div class="tablewrap" id="table"></div>
            <div class="muted" style="font-size:.8rem;margin-top:8px">Vorgänge ohne geladene Energie sind ausgeblendet. Kosten wie evcc: Netzstrom × Strompreis + Solarstrom × Einspeisevergütung (entgangene Einspeisung). PV-Ersparnis = Solarstrom × (Strompreis − Einspeisevergütung).</div>
          </ha-card>
        </section>
      </div>`;

    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    root.getElementById("menu").append(this._menu);

    root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => this._setTab(b.dataset.tab)));
    root.getElementById("csv").addEventListener("click", () => this._exportCsv());

    this._chartEl = root.getElementById("chart");
    this._chartEl.addEventListener("click", (ev) => this._onBarClick(ev));
    this._ro = new ResizeObserver(() => {
      const w = Math.floor(this._chartEl.clientWidth);
      if (w > 0 && Math.abs(w - this._chartWidth) > 4) {
        this._chartWidth = w;
        this._renderChart();
      }
    });
    this._ro.observe(this._chartEl);

    this._setTab(this._tab);
    this._loadInfo();
  }

  _loadInfo() {
    loadConfig(this._hass)
      .then((res) => {
        this._entries = res.entries;
        const wanted = this._remember("entry");
        this._info = res.entries.find((e) => e.entry_id === wanted) ?? res.entries[0] ?? null;
        if (!this._info) {
          this.shadowRoot.getElementById("flow").innerHTML = `<div class="empty">PV-Lader-Integration nicht geladen.</div>`;
          setTimeout(() => this._loadInfo(), 10000);
          return;
        }
        this._renderEntrySelect();
        this._mountCard();
        this._fetchSessions();
        this._renderFlow(true);
      })
      .catch((err) => {
        this.shadowRoot.getElementById("flow").innerHTML = `<div class="empty">Fehler: ${esc(err.message ?? err)}</div>`;
      });
  }

  _renderEntrySelect() {
    const slot = this.shadowRoot.getElementById("entrysel");
    if (!this._entries || this._entries.length < 2) {
      slot.innerHTML = "";
      return;
    }
    slot.innerHTML = `<select>${this._entries.map((e) => `<option value="${e.entry_id}" ${e.entry_id === this._info.entry_id ? "selected" : ""}>${esc(e.title)}</option>`).join("")}</select>`;
    slot.querySelector("select").addEventListener("change", (ev) => {
      this._info = this._entries.find((e) => e.entry_id === ev.target.value);
      this._remember("entry", this._info.entry_id);
      this._mountCard();
      this._sessions = null;
      this._fetchSessions();
      this._renderFlow(true);
    });
  }

  _mountCard() {
    const slot = this.shadowRoot.getElementById("cardslot");
    slot.innerHTML = "";
    this._card = document.createElement("pvlader-card");
    this._card.setConfig({ entry_id: this._info.entry_id, title: this._info.title, show_link: false });
    this._card.hass = this._hass;
    slot.append(this._card);
  }

  _setTab(tab) {
    this._tab = tab;
    this._remember("tab", tab);
    const root = this.shadowRoot;
    root.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    root.getElementById("tab-laden").hidden = tab !== "laden";
    root.getElementById("tab-ladungen").hidden = tab !== "ladungen";
    if (tab === "ladungen") {
      requestAnimationFrame(() => {
        const w = Math.floor(this._chartEl.clientWidth);
        if (w > 0) this._chartWidth = w;
        this._renderStats();
      });
    }
  }

  _onHass() {
    if (this._menu) this._menu.hass = this._hass;
    if (this._card) this._card.hass = this._hass;
    if (!this._info) return;
    this._renderFlow();
    // Neue Session abgeschlossen -> Historie neu laden
    const countState = this._hass.states[this._info.entities.stats_count]?.state;
    if (this._lastCount !== undefined && countState !== this._lastCount) this._fetchSessions();
    this._lastCount = countState;
  }

  async _fetchSessions() {
    if (!this._info || !this._hass || this._fetching) return;
    this._fetching = true;
    try {
      const res = await this._hass.callWS({ type: "pvlader/sessions", entry_id: this._info.entry_id });
      this._sessions = res.sessions.map((s) => normSession(s)).filter((s) => s.kwh >= MIN_SESSION_KWH);
      this._current = res.current ? normSession(res.current, true) : null;
      this._prices = { grid: res.grid_price, feedin: res.feedin_price };
      this._renderRecent();
      this._renderStats();
    } catch (err) {
      this.shadowRoot.getElementById("table").innerHTML = `<div class="empty">Ladehistorie nicht abrufbar: ${esc(err.message ?? err)}</div>`;
    } finally {
      this._fetching = false;
    }
  }

  /** Alle Vorgänge inkl. laufendem (neueste zuerst). */
  _all() {
    const list = [...(this._sessions ?? [])];
    if (this._current && this._current.kwh >= MIN_SESSION_KWH) list.unshift(this._current);
    return list;
  }

  // --- Laden: Energiefluss ---------------------------------------------------

  _renderFlow(force = false) {
    const info = this._info;
    const states = this._hass.states;
    const grid = info.sources.grid_power ? watts(states[info.sources.grid_power]) : null;
    const pvRaw = info.sources.pv_power ? watts(states[info.sources.pv_power]) : null;
    const charge = watts(states[info.entities.charge_power]) ?? 0;
    const sig = `${grid}|${pvRaw}|${charge}|${this._hass.themes?.darkMode}`;
    if (!force && sig === this._flowSig) return;
    this._flowSig = sig;
    this.toggleAttribute("dark", !!this._hass.themes?.darkMode);

    const el = this.shadowRoot.getElementById("flow");
    if (grid === null) {
      el.innerHTML = `<div class="empty">Netzleistung nicht verfügbar.</div>`;
      return;
    }
    const pv = Math.max(0, pvRaw ?? 0);
    const imp = Math.max(0, grid);
    const exp = Math.max(0, -grid);
    const home = Math.max(0, pv + grid - charge);
    const total = Math.max(1, pv + imp, home + charge + exp);
    const kw = (w) => `${fmt(w / 1000, 1)} kW`;
    const seg = (w, cls, color) => {
      const pct = (w / total) * 100;
      return pct > 0.5 ? `<div class="${cls}" style="flex-basis:${pct}%;background:${color}">${pct > 12 ? kw(w) : ""}</div>` : "";
    };
    const row = (icon, name, w, price, color) => `
      <div class="flowrow ${w < 50 ? "zero" : ""}">
        <span class="dot" style="background:${color}"></span>
        <ha-icon icon="${icon}" style="--mdc-icon-size:20px"></ha-icon>
        <span class="n">${name}</span>
        ${price !== null ? `<span class="p">${fmt(price * 100, 1)} ct</span>` : ""}
        <span class="v">${kw(w)}</span>
      </div>`;
    el.innerHTML = `
      <div class="flowcap"><span>Quellen</span><span>${kw(pv + imp)}</span></div>
      <div class="flowbar">${seg(pv, "solar", "var(--pvl-solar)")}${seg(imp, "grid", "var(--pvl-grid)")}</div>
      <div class="flowcap"><span>Verbrauch</span><span>${kw(home + charge + exp)}</span></div>
      <div class="flowbar">${seg(home, "home", "var(--pvl-home)")}${seg(charge, "charge", "var(--primary-color)")}${seg(exp, "export", "var(--pvl-export)")}</div>
      <div class="flowlists">
        <div>
          <h3><span>In</span><span>${kw(pv + imp)}</span></h3>
          ${row("mdi:solar-power", "Erzeugung", pv, null, "var(--pvl-solar)")}
          ${row("mdi:transmission-tower-import", "Netzbezug", imp, info.grid_price, "var(--pvl-grid)")}
        </div>
        <div>
          <h3><span>Out</span><span>${kw(home + charge + exp)}</span></h3>
          ${row("mdi:home", "Hausverbrauch", home, null, "var(--pvl-home)")}
          ${row("mdi:ev-station", "Ladepunkt", charge, null, "var(--primary-color)")}
          ${row("mdi:transmission-tower-export", "Netzexport", exp, -info.feedin_price, "var(--pvl-export)")}
        </div>
      </div>
      ${pvRaw === null ? `<div class="muted" style="font-size:.8rem;margin-top:8px">Keine PV-Leistung konfiguriert – Hausverbrauch ohne PV gerechnet.</div>` : ""}`;
  }

  _renderRecent() {
    const el = this.shadowRoot.getElementById("recent");
    const list = this._all().slice(0, 6);
    if (!list.length) {
      el.innerHTML = `<div class="empty">Noch keine Ladevorgänge aufgezeichnet.</div>`;
      return;
    }
    el.innerHTML =
      list.map((s) => `
        <div class="flowrow">
          <span class="n">${esc(dateTime(s.start))}${s.running ? `<span class="badge">läuft</span>` : ""}</span>
          <span class="p solar">${fmt(s.solarPct, 0)} % ☀</span>
          <span class="p">${fmt(s.cost, 2)} €</span>
          <span class="v">${fmt(s.kwh, 1)} kWh</span>
        </div>`).join("") +
      `<div style="text-align:right;margin-top:8px"><button class="btn" id="toall">Alle Ladungen →</button></div>`;
    el.querySelector("#toall").addEventListener("click", () => this._setTab("ladungen"));
  }

  // --- Ladungen: Auswertung --------------------------------------------------

  _inPeriod(s) {
    if (this._period === "all") return true;
    if (s.start.getFullYear() !== this._year) return false;
    return this._period === "year" || s.start.getMonth() === this._month;
  }

  _renderStats() {
    if (this._tab !== "ladungen" || !this._info) return;
    this._renderControls();
    if (!this._sessions) {
      this.shadowRoot.getElementById("table").innerHTML = `<div class="empty">Lade …</div>`;
      return;
    }
    const list = this._all().filter((s) => this._inPeriod(s));
    const sum = summarize(list, this._prices.grid, this._prices.feedin);
    this._list = list;
    this._sum = sum;
    this._renderTiles(sum);
    this._renderChart();
    this._renderTable(list, sum);
  }

  _periodLabel() {
    if (this._period === "all") return "Gesamt";
    if (this._period === "year") return String(this._year);
    return `${MONTHS_LONG[this._month]} ${this._year}`;
  }

  _renderControls() {
    const el = this.shadowRoot.getElementById("controls");
    const now = new Date();
    const atEnd = this._period === "year" ? this._year >= now.getFullYear()
      : this._year > now.getFullYear() || (this._year === now.getFullYear() && this._month >= now.getMonth());
    const seg = (name, items, active) => `<div class="seg" data-seg="${name}">${items.map(([k, l]) => `<button data-v="${k}" class="${k === active ? "active" : ""}">${l}</button>`).join("")}</div>`;
    el.innerHTML = `
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        ${seg("period", [["month", "Monat"], ["year", "Jahr"], ["all", "Gesamt"]], this._period)}
        ${seg("metric", [["energy", "Energie"], ["cost", "Kosten"]], this._metric)}
      </div>
      <div class="nav">
        <button data-step="-1" ${this._period === "all" ? "disabled" : ""} title="zurück">‹</button>
        <span class="label">${esc(this._periodLabel())}</span>
        <button data-step="1" ${this._period === "all" || atEnd ? "disabled" : ""} title="vor">›</button>
      </div>`;
    el.querySelectorAll("[data-seg=period] button").forEach((b) => b.addEventListener("click", () => {
      this._period = b.dataset.v;
      this._remember("period", this._period);
      this._renderStats();
    }));
    el.querySelectorAll("[data-seg=metric] button").forEach((b) => b.addEventListener("click", () => {
      this._metric = b.dataset.v;
      this._renderStats();
    }));
    el.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => this._step(+b.dataset.step)));
  }

  _step(dir) {
    if (this._period === "year") this._year += dir;
    else if (this._period === "month") {
      const d = new Date(this._year, this._month + dir, 1);
      this._year = d.getFullYear();
      this._month = d.getMonth();
    }
    this._renderStats();
  }

  _renderTiles(sum) {
    const tile = (label, value, hint = "") => `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
    this.shadowRoot.getElementById("tiles").innerHTML = [
      tile("Geladen", `${fmt(sum.kwh, sum.kwh >= 1000 ? 0 : 1)} kWh`, `${sum.count} Ladevorgänge`),
      tile("Solaranteil", `${fmt(sum.solarPct, 0)} %`, `${fmt(sum.solar, 1)} kWh Sonne`),
      tile("Kosten", `${fmt(sum.cost, 2)} €`, `Ø ${fmt(sum.price, 1)} ct/kWh`),
      tile("PV-Ersparnis", `${fmt(sum.savings, 2)} €`, "gegenüber Netzbezug"),
      tile("Ladedauer", sum.dur ? `${duration(sum.dur)} h` : "–", sum.avgKw ? `Ø ${fmt(sum.avgKw, 1)} kW` : ""),
      tile("Reichweite", sum.rangeDelta !== null ? `+${fmt(sum.rangeDelta, 0)} km` : "–", sum.socDelta !== null ? `+${fmt(sum.socDelta, 0)} % Ladestand` : ""),
    ].join("");
  }

  _buckets() {
    const list = this._list;
    let keys;
    let keyOf;
    let labelOf;
    let titleOf;
    if (this._period === "month") {
      const days = new Date(this._year, this._month + 1, 0).getDate();
      keys = Array.from({ length: days }, (_, i) => i + 1);
      keyOf = (s) => s.start.getDate();
      labelOf = (k) => String(k);
      titleOf = (k) => `${k}. ${MONTHS_LONG[this._month]}`;
    } else if (this._period === "year") {
      keys = Array.from({ length: 12 }, (_, i) => i);
      keyOf = (s) => s.start.getMonth();
      labelOf = (k) => MONTHS_SHORT[k];
      titleOf = (k) => `${MONTHS_LONG[k]} ${this._year}`;
    } else {
      const years = list.map((s) => s.start.getFullYear());
      const now = new Date().getFullYear();
      const first = years.length ? Math.min(...years) : now;
      keys = Array.from({ length: now - first + 1 }, (_, i) => first + i);
      keyOf = (s) => s.start.getFullYear();
      labelOf = (k) => String(k);
      titleOf = (k) => String(k);
    }
    const groups = new Map(keys.map((k) => [k, []]));
    for (const s of list) groups.get(keyOf(s))?.push(s);
    const energy = this._metric === "energy";
    return keys.map((k) => {
      const g = groups.get(k);
      const sum = summarize(g, this._prices.grid, this._prices.feedin);
      const parts = energy
        ? [
            { value: sum.grid, color: "var(--pvl-grid)", name: "Netz" },
            { value: sum.solar, color: "var(--pvl-solar)", name: "Solar" },
          ]
        : [
            { value: sum.grid * this._prices.grid, color: "var(--pvl-grid)", name: "Netz" },
            { value: sum.solar * this._prices.feedin, color: "var(--pvl-solar)", name: "Solar" },
          ];
      const title = energy
        ? `${titleOf(k)}: ${fmt(sum.kwh, 1)} kWh (Netz ${fmt(sum.grid, 1)}, Solar ${fmt(sum.solar, 1)}) · ${g.length} Vorgänge`
        : `${titleOf(k)}: ${fmt(sum.cost, 2)} € (Netz ${fmt(sum.grid * this._prices.grid, 2)}, Solar ${fmt(sum.solar * this._prices.feedin, 2)}) · Ø ${fmt(sum.price, 1)} ct/kWh`;
      return { key: k, label: labelOf(k), parts, title };
    });
  }

  _renderChart() {
    if (this._tab !== "ladungen" || !this._list) return;
    const sum = this._sum;
    const energy = this._metric === "energy";
    const head = this.shadowRoot.getElementById("charthead");
    head.innerHTML = energy
      ? `Geladene Energie<span class="sub">${fmt(sum.kwh, 1)} kWh gesamt · ${fmt(sum.solarPct, 0)} % Sonne</span>`
      : `Kosten<span class="sub">${fmt(sum.cost, 2)} € gesamt · Ø ${fmt(sum.price, 1)} ct/kWh</span>`;
    this._chartEl.innerHTML = barChart(this._buckets(), this._chartWidth, energy ? "kWh" : "€", energy ? 1 : 2);
    this.shadowRoot.getElementById("legend").innerHTML = energy
      ? `<span><span class="dot" style="background:var(--pvl-grid)"></span>Netz ${fmt(sum.grid, 1)} kWh</span>
         <span><span class="dot" style="background:var(--pvl-solar)"></span>Solar ${fmt(sum.solar, 1)} kWh</span>`
      : `<span><span class="dot" style="background:var(--pvl-grid)"></span>Netz ${fmt(sum.grid * this._prices.grid, 2)} €</span>
         <span><span class="dot" style="background:var(--pvl-solar)"></span>Solar ${fmt(sum.solar * this._prices.feedin, 2)} €</span>
         <span class="muted">PV-Ersparnis ${fmt(sum.savings, 2)} €</span>`;
  }

  _onBarClick(ev) {
    const key = ev.target?.dataset?.key;
    if (key === undefined) return;
    if (this._period === "year") {
      this._period = "month";
      this._month = +key;
    } else if (this._period === "all") {
      this._period = "year";
      this._year = +key;
    } else return;
    this._remember("period", this._period);
    this._renderStats();
  }

  _renderTable(list, sum) {
    const el = this.shadowRoot.getElementById("table");
    if (!list.length) {
      el.innerHTML = `<div class="empty">Keine Ladevorgänge im Zeitraum ${esc(this._periodLabel())}.</div>`;
      return;
    }
    const signed = (v, d = 0) => (v === null ? "–" : `${v > 0 ? "+" : ""}${fmt(v, d)}`);
    const head = [
      ["Anfang", ""], ["Geladen", "kWh"], ["Ladestand", "%"], ["Solar", "%"], ["Kosten", "€"], ["Ø Preis", "ct/kWh"],
      ["Ladedauer", "h:mm"], ["Reichweite", "km"], ["Kilometerstand", "km"], ["Ø Leistung", "kW"],
    ];
    const rows = list.map((s) => `
      <tr>
        <td>${esc(dateTime(s.start))}${s.running ? `<span class="badge">läuft</span>` : ""}</td>
        <td>${fmt(s.kwh, 1)}</td>
        <td title="${s.socStart !== null ? `${fmt(s.socStart, 0)} → ${fmt(s.socEnd, 0)} %` : ""}">${signed(s.socDelta)}</td>
        <td>${fmt(s.solarPct, 1)}</td>
        <td>${fmt(s.cost, 2)}</td>
        <td>${fmt(s.price, 1)}</td>
        <td>${duration(s.dur)}</td>
        <td>${signed(s.rangeDelta)}</td>
        <td>${s.odo !== null ? fmt(s.odo, 0) : "–"}</td>
        <td>${fmt(s.avgKw, 1)}</td>
      </tr>`).join("");
    el.innerHTML = `
      <table>
        <thead><tr>${head.map(([t, u]) => `<th>${t}<span class="unit">${u || "&nbsp;"}</span></th>`).join("")}</tr></thead>
        <tbody>${rows}
          <tr class="total">
            <td>Gesamt</td><td>${fmt(sum.kwh, 1)}</td><td>${signed(sum.socDelta)}</td><td>${fmt(sum.solarPct, 1)}</td>
            <td>${fmt(sum.cost, 2)}</td><td>${fmt(sum.price, 1)}</td><td>${duration(sum.dur)}</td>
            <td>${signed(sum.rangeDelta)}</td><td></td><td>${fmt(sum.avgKw, 1)}</td>
          </tr>
        </tbody>
      </table>`;
  }

  _exportCsv() {
    const list = this._list ?? [];
    const n = (v, d) => (v === null || v === undefined ? "" : fmt(v, d).replace(/\./g, ""));
    const iso = (d) => (d ? toLocalInput(d).replace("T", " ") : "");
    const lines = [
      ["Anfang", "Ende", "Geladen kWh", "Solar kWh", "Netz kWh", "Solar %", "Kosten EUR", "Preis ct/kWh", "Ladedauer h:mm",
        "SoC Start %", "SoC Ende %", "Reichweite +km", "Kilometerstand km", "Leistung kW"].join(";"),
      ...list.map((s) => [
        iso(s.start), iso(s.end), n(s.kwh, 3), n(s.solarKwh, 3), n(s.gridKwh, 3), n(s.solarPct, 1), n(s.cost, 2), n(s.price, 1),
        s.dur ? duration(s.dur) : "", n(s.socStart, 0), n(s.socEnd, 0), n(s.rangeDelta, 0), n(s.odo, 0), n(s.avgKw, 2),
      ].join(";")),
    ];
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ladevorgaenge-${this._periodLabel().replace(/\s+/g, "-").toLowerCase()}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
}

// ---------------------------------------------------------------------------
// Registrierung
// ---------------------------------------------------------------------------

if (!customElements.get("pvlader-card")) customElements.define("pvlader-card", PvLaderCard);
if (!customElements.get("pvlader-panel")) customElements.define("pvlader-panel", PvLaderPanel);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "pvlader-card")) {
  window.customCards.push({
    type: "pvlader-card",
    name: "PV-Lader",
    description: "Wallbox-Steuerung wie in evcc: Lademodus, Ladegrenze, Ladeplan, letzter Ladevorgang.",
    preview: true,
    documentationURL: "https://github.com/",
  });
}
