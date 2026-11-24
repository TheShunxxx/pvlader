"""Dashboard-Karte und Seitenreiter „PV-Lader“ bereitstellen."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "www"
URL_BASE = "/pvlader_static"
PANEL_PATH = "pvlader"
PANEL_ELEMENT = "pvlader-panel"


def _version() -> str:
    """Hash über alle Frontend-Dateien – ändert die URL nach jedem Update (Browser-Cache)."""
    digest = hashlib.sha1()
    for path in sorted(FRONTEND_DIR.glob("*.js")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Statische Dateien, Karten-Skript (alle Dashboards) und Seitenleisten-Panel registrieren."""
    if hass.http is None or "frontend" not in hass.config.components:
        return  # z. B. in Tests oder ohne Oberfläche

    from homeassistant.components import panel_custom  # noqa: PLC0415
    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    version = await hass.async_add_executor_job(_version)
    await hass.http.async_register_static_paths([StaticPathConfig(URL_BASE, str(FRONTEND_DIR), False)])
    module_url = f"{URL_BASE}/pvlader.js?v={version}"
    # Lädt die Karte auf allen Dashboards – keine manuelle Ressource nötig
    add_extra_js_url(hass, module_url)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title="PV-Lader",
        sidebar_icon="mdi:ev-station",
        module_url=module_url,
        require_admin=False,
    )
    _LOGGER.debug("Frontend registriert (%s)", module_url)
