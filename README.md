# PV-Lader (`pvlader`)

Home-Assistant-Custom-Integration für PV-Überschussladen mit der Dadapower-Wallbox (Modbus TCP) – Ersatz für die genutzten evcc-Funktionen: Lademodi Aus/Sofort/Nur PV/Min+PV, SoC-Limit, Ladeplan mit Zielzeit, automatische Phasenumschaltung, Ladevorgangs-Statistik (Energie, Solaranteil, Kosten).

Unabhängige Implementierung; nutzt keinen evcc-Code.

## Installation

1. **Per HACS:** *HACS → ⋮ → Benutzerdefinierte Repositories* → URL dieses Repos, Typ „Integration“ → „PV-Lader“ herunterladen.
   Alternativ manuell: `custom_components/pvlader/` nach `/config/custom_components/pvlader/` kopieren (nur diesen Unterordner).
2. Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → „PV-Lader“*.
   - Schritt 1: Host (IP-Adresse der Wallbox), Port `502`, Unit-ID `1`. Der Verbindungstest **liest nur** (Modell, Seriennummer, Firmware) und schreibt nichts auf die Wallbox.
   - Schritt 2: Messquellen – Netzleistung (`sensor.bitshake_smartmeterreader_e320_power_in`, vorzeichenbehaftet: + Bezug / − Einspeisung), SoC (`sensor.tesla_battery_level`), optional PV-Leistung (nur Anzeige) und „Fahrzeug eingesteckt“ (nur Rückfallebene bei unbekanntem Steckerstatus).
4. Über *Konfigurieren* (Optionen): Akkukapazität (77 kWh), Strompreis (0,298 €/kWh), Einspeisevergütung (0,082 €/kWh), Strom-Installationsgrenzen (6–16 A), Aktivierungs-/Deaktivierungsverzögerung (60/180 s), Schaltsperre (300 s). Änderungen laden die Integration neu.

pymodbus bringt Home Assistant bereits mit; es wird nichts zusätzlich installiert.

## Dashboard-Karte und Seitenreiter

Nach dem Neustart bringt die Integration beides selbst mit – keine HACS-Frontend-Ressource nötig:

- **Seitenreiter „PV-Lader“** (Seitenleiste, `/pvlader`):
  - *Laden*: Energiefluss (PV, Netz, Haus, Ladepunkt) mit Preisen, die Ladekarte und die letzten Ladevorgänge.
  - *Ladungen*: Auswertung nach Monat/Jahr/Gesamt (Energie Netz/Solar oder Kosten als Balkendiagramm, Klick auf einen Balken springt eine Ebene tiefer), Kennzahlen (Solaranteil, Kosten, Ø Preis, PV-Ersparnis, Ladedauer, Reichweite) und Tabelle aller Ladevorgänge mit CSV-Export.
- **Karte** `custom:pvlader-card` (im Karten-Auswahldialog als „PV-Lader“): Lademodus Aus/PV/Min+PV/Sofort, Ladestand mit Ladegrenze, Leistung und Regelstatus, Ladegrenze-Regler, Ladeplanung (Zielzeit + Ziel-SoC, Dauer-/Kostenschätzung), letzter bzw. laufender Ladevorgang.

```yaml
type: custom:pvlader-card
title: Garage            # optional, Standard: Name des Eintrags
vehicle_name: Model Y    # optional
show_plan: true          # optional
show_session: true       # optional
show_link: true          # Link „Auswertung →“ zum Seitenreiter
# entry_id: …            # nur bei mehreren Wallboxen
```

**Kosten** werden wie in evcc gerechnet: Netzstrom × Strompreis + Solarstrom × Einspeisevergütung (die entgangene Einspeisung). Ein Ladevorgang mit 95 % Sonne kostet so rund 9 ct/kWh. „PV-Ersparnis“ in der Auswertung = Solarstrom × (Strompreis − Einspeisevergütung). Ältere Daten rechnet pvlader beim ersten Start einmalig um.

Der Hinweis „Nur mitlesen“ auf der Karte bedeutet: „Steuerung aktiv“ ist AUS, pvlader schreibt nichts auf die Wallbox.

Die Ladehistorie liegt in `/config/.storage/pvlader.<entry_id>.history` (bis 3000 Vorgänge) und enthält je Vorgang zusätzlich Ladestand Start/Ende, Kilometerstand, Reichweite Start/Ende und die reine Ladedauer. Kilometerstand und Reichweite kommen aus den optionalen Messquellen (Standard `sensor.tesla_odometer`, `sensor.tesla_rated_battery_range_km`, änderbar unter *Konfigurieren*). Vorgänge ohne geladene Energie blendet die Auswertung aus.

Vorschau ohne Home Assistant (Entwicklung): `python -m http.server 8765` im Repo, dann `http://localhost:8765/tools/frontend-preview.html` (`?dark`, `?view=card`).

## Wichtigste Entitäten

| Entität | Zweck |
|---|---|
| `switch.pv_lader_control_active` („Steuerung aktiv“) | **Sicherheitsschalter**, Standard AUS |
| `select.pv_lader_charge_mode` („Lademodus“) | Aus / Sofort / Nur PV / Min+PV |
| `number.pv_lader_charge_limit` („Ladegrenze“) | SoC-Limit in % |
| `switch.pv_lader_charge_plan_active`, `number.pv_lader_plan_target_soc`, `datetime.pv_lader_plan_target_time` | Ladeplan; alternativ Service `pvlader.set_plan` |
| `sensor.pv_lader_control_status` („Regelstatus“) | Begründung der Regelentscheidung; Attribute *geplantes Kommando* und Sollzustand |
| `sensor.pv_lader_total_energy` | Zählerstand der Wallbox (total_increasing) – für das Energie-Dashboard |

Die entity_ids entstehen aus Gerätename und Entitätsname; bei Abweichungen in den Entitätseinstellungen nachsehen.

## Parallelbetrieb mit evcc (Testphase)

Solange **„Steuerung aktiv“ AUS** ist, schreibt die Integration **nichts** auf die Wallbox – kein Failsafe-Register beim Verbinden, kein Heartbeat, keine Lade-Kommandos. Sie liest nur mit (mehrere Modbus-TCP-Verbindungen sind möglich) und zeigt im Regelstatus, was sie tun *würde*. evcc steuert in dieser Zeit weiter.

So lässt sich die Regelung neben evcc vergleichen: Regelstatus, Soll-Strom/-Phasen und PV-Überschuss gegen das tatsächliche Verhalten von evcc halten.

Hinweis: Im Parallelbetrieb endet ein Ladevorgang in der Statistik nur durch Abstecken (der eigene Modus „Aus“ beendet ihn nur bei aktiver Steuerung, weil evcc ja weiterlädt).

## Umstieg auf pvlader

1. evcc stoppen (Add-on/App „evcc“ anhalten und Autostart deaktivieren) – **nie zwei Regler gleichzeitig schreiben lassen**.
2. Lademodus und Ladegrenze in pvlader passend setzen.
3. „Steuerung aktiv“ einschalten. Dabei schreibt pvlader zuerst den Failsafe-Timeout (Register 102 = 300 s), setzt den internen Reglerzustand zurück und übernimmt dann die Regelung (alle 10 s, Heartbeat alle 60 s).
4. Beim ersten Laden prüfen, ob der eingestellte Strom an den Phasenströmen ankommt (Skalierung Register 1001, siehe `wiki/komponenten/wallbox-dadapower.md`).

## Rückweg zu evcc

1. „Steuerung aktiv“ ausschalten – pvlader hört sofort auf zu schreiben (es wird dabei nichts mehr geschrieben, auch kein „Aus“-Kommando).
2. evcc wieder starten.
3. Optional die Integration entfernen. Einstellungen und Statistik liegen in `/config/.storage/pvlader.<entry_id>`.

## Entwicklung / Tests

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

`tests/` enthält die HA-freien Kerntests (Regler, Planer, Sessions, Modbus-Client), `tests/ha/` die Integrationstests mit `pytest-homeassistant-custom-component` (Config-Flow, Setup/Unload, Sicherheitsschalter, Regelzyklus, Persistenz, Service). Unter Windows hebt `tests/conftest.py` die Socket-Sperre des HA-Testplugins auf, da der Proactor-Eventloop `socketpair()` benötigt.
