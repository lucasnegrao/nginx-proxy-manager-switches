"""Switch platform for npm_switches."""
import asyncio
from datetime import datetime, timedelta
import logging

import aiohttp
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.util import slugify
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    # New imports for reachability config
    CONF_REACHABILITY_INTERVAL_MINUTES,
    DEFAULT_REACHABILITY_INTERVAL_MINUTES,
)
from .entity import NpmSwitchesEntity
from . import NpmSwitchesUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Setup sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    api = coordinator.api
    proxy_hosts = await api.get_proxy_hosts()
    redir_hosts = await api.get_redirection_hosts()
    stream_hosts = await api.get_stream_hosts()
    dead_hosts = await api.get_dead_hosts()
    entities = []

    if entry.data.get("include_proxy_hosts"):
        for proxy_host in proxy_hosts.values():
            entities.append(NpmProxyBinarySwitch(coordinator, entry, proxy_host))
    if entry.data.get("include_redirection_hosts"):
        for redir_host in redir_hosts.values():
            entities.append(NpmRedirBinarySwitch(coordinator, entry, redir_host))
    if entry.data.get("include_stream_hosts"):
        for stream_host in stream_hosts.values():
            entities.append(NpmStreamBinarySwitch(coordinator, entry, stream_host))
    if entry.data.get("include_dead_hosts"):
        for dead_host in dead_hosts.values():
            entities.append(NpmDeadBinarySwitch(coordinator, entry, dead_host))

    async_add_entities(entities, True)


class _ReachabilityMixin:
    """Common helpers to manage reachability checks and attributes."""

    def _init_reachability_state(self):
        # Load interval from options -> data -> default (minutes)
        minutes = self.config_entry.options.get(
            CONF_REACHABILITY_INTERVAL_MINUTES,
            self.config_entry.data.get(
                CONF_REACHABILITY_INTERVAL_MINUTES, DEFAULT_REACHABILITY_INTERVAL_MINUTES
            ),
        )
        try:
            minutes = int(minutes)
        except Exception:  # noqa: BLE001
            minutes = DEFAULT_REACHABILITY_INTERVAL_MINUTES

        self._reachability_interval = timedelta(minutes=max(0, minutes))
        self._reachable: bool | None = None
        self._last_reachability_check: datetime | None = None
        self._http_status: int | None = None
        self._unsub_reachability_timer = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def _periodic(_now):
            self.hass.async_create_task(self._update_reachability())

        # Register periodic reachability if interval > 0
        if self._reachability_interval.total_seconds() > 0:
            self._unsub_reachability_timer = async_track_time_interval(
                self.hass, _periodic, self._reachability_interval
            )
            self.async_on_remove(self._unsub_reachability_timer)

        # Also re-check when coordinator updates (NPM state changed)
        self.async_on_remove(
            self.coordinator.async_add_listener(
                lambda: self.hass.async_create_task(self._update_reachability())
            )
        )

        # Listen for option changes and reconfigure the timer dynamically
        async def _on_entry_update(hass, entry: ConfigEntry):
            new_minutes = entry.options.get(
                CONF_REACHABILITY_INTERVAL_MINUTES,
                entry.data.get(
                    CONF_REACHABILITY_INTERVAL_MINUTES,
                    DEFAULT_REACHABILITY_INTERVAL_MINUTES,
                ),
            )
            try:
                new_minutes = int(new_minutes)
            except Exception:  # noqa: BLE001
                new_minutes = DEFAULT_REACHABILITY_INTERVAL_MINUTES

            new_interval = timedelta(minutes=max(0, new_minutes))
            if new_interval != self._reachability_interval:
                # Cancel existing timer
                if self._unsub_reachability_timer:
                    try:
                        self._unsub_reachability_timer()
                    except Exception:  # noqa: BLE001
                        pass
                    self._unsub_reachability_timer = None

                self._reachability_interval = new_interval

                # Re-register if enabled
                if self._reachability_interval.total_seconds() > 0:
                    self._unsub_reachability_timer = async_track_time_interval(
                        self.hass, _periodic, self._reachability_interval
                    )
                    self.async_on_remove(self._unsub_reachability_timer)

                # Trigger an immediate check to update attributes
                self.hass.async_create_task(self._update_reachability())

        remove_update_listener = self.config_entry.add_update_listener(_on_entry_update)
        self.async_on_remove(remove_update_listener)

        # Initial check
        self.hass.async_create_task(self._update_reachability())

    def _attrs_with_reachability(self, base: dict) -> dict:
        base.update(
            {
                "reachable": self._reachable,
                "last_reachability_check": (
                    self._last_reachability_check.isoformat()
                    if self._last_reachability_check
                    else None
                ),
            }
        )
        # Only include http_status for HTTP-based checks
        if hasattr(self, "_http_status"):
            base["http_status"] = self._http_status
        return base


class NpmProxyBinarySwitch(_ReachabilityMixin, NpmSwitchesEntity, SwitchEntity):
    """Switches to enable/disable the Proxy Host Type in NPM"""

    def __init__(
        self,
        coordinator: NpmSwitchesUpdateCoordinator,
        entry: ConfigEntry,
        host: dict,
    ) -> None:
        """Initialize proxy switch entity."""
        super().__init__(coordinator, entry)
        self.host = host
        self.name = "Proxy " + self.host["domain_names"][0].replace(".", " ").capitalize()
        self.entity_id = "switch." + slugify(f"{entry.title} {self.name}")
        self._attr_unique_id = f"{entry.entry_id} {self.name}"
        self.host_id = str(host["id"])
        self.host_type = "proxy-hosts"
        self._init_reachability_state()

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        await self.coordinator.api.enable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        await self.coordinator.api.disable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    @property
    def icon(self):
        """Return the icon of this switch."""
        if self.coordinator.api.is_host_enabled(self.host_id, self.host_type):
            return "mdi:check-network"
        return "mdi:close-network"

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self.coordinator.api.is_host_enabled(self.host_id, self.host_type)

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        scheme = self.host.get("forward_scheme", "http")
        domain = self.host["domain_names"][0]
        base = {
            "id": self.host["id"],
            "domain_names": self.host["domain_names"],
            "forward_host": self.host["forward_host"],
            "nginx_online": self.host["meta"]["nginx_online"],
            "url": f"{scheme}://{domain}/",
        }
        return self._attrs_with_reachability(base)

    async def _update_reachability(self):
        """Probe the public URL to determine reachability."""
        scheme = self.host.get("forward_scheme", "http")
        domain = self.host["domain_names"][0]
        url = f"{scheme}://{domain}/"
        session = async_get_clientsession(self.hass)

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
                self._http_status = resp.status
                # Consider < 500 as reachable (even 401/403/404 means server responded)
                self._reachable = resp.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self._reachable = False
            self._http_status = None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unexpected error probing %s: %s", url, err)
            self._reachable = False
            self._http_status = None
        finally:
            self._last_reachability_check = datetime.utcnow()
            self.async_write_ha_state()


class NpmRedirBinarySwitch(_ReachabilityMixin, NpmSwitchesEntity, SwitchEntity):
    """Switches to enable/disable the Redir Host Type in NPM"""

    def __init__(
        self,
        coordinator: NpmSwitchesUpdateCoordinator,
        entry: ConfigEntry,
        host: dict,
    ) -> None:
        """Initialize redir switch entity."""
        super().__init__(coordinator, entry)
        self.host = host
        self.name = "Redirect " + self.host["domain_names"][0].replace(".", " ").capitalize()
        self.entity_id = "switch." + slugify(f"{entry.title} {self.name}")
        self._attr_unique_id = f"{entry.entry_id} {self.name}"
        self.host_type = "redirection-hosts"
        self.host_id = str(host["id"])
        self._init_reachability_state()

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        await self.coordinator.api.enable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        await self.coordinator.api.disable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    @property
    def icon(self):
        """Return the icon of this switch."""
        if self.coordinator.api.is_host_enabled(self.host_id, self.host_type):
            return "mdi:check-network"
        return "mdi:close-network"

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self.coordinator.api.is_host_enabled(self.host_id, self.host_type)

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        scheme = self.host.get("forward_scheme", "http")
        domain = self.host["domain_names"][0]
        base = {
            "id": self.host["id"],
            "domain_names": self.host["domain_names"],
            "forward_host": self.host["forward_domain_name"].split(":")[0],
            "nginx_online": self.host["meta"]["nginx_online"],
            "url": f"{scheme}://{domain}/",
        }
        return self._attrs_with_reachability(base)

    async def _update_reachability(self):
        """Probe the public URL to determine reachability."""
        scheme = self.host.get("forward_scheme", "http")
        domain = self.host["domain_names"][0]
        url = f"{scheme}://{domain}/"
        session = async_get_clientsession(self.hass)

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
                self._http_status = resp.status
                self._reachable = resp.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self._reachable = False
            self._http_status = None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unexpected error probing %s: %s", url, err)
            self._reachable = False
            self._http_status = None
        finally:
            self._last_reachability_check = datetime.utcnow()
            self.async_write_ha_state()


class NpmStreamBinarySwitch(_ReachabilityMixin, NpmSwitchesEntity, SwitchEntity):
    """Switches to enable/disable the Stream Host Type in NPM"""

    def __init__(
        self,
        coordinator: NpmSwitchesUpdateCoordinator,
        entry: ConfigEntry,
        host: dict,
    ) -> None:
        """Initialize stream switch entity."""
        super().__init__(coordinator, entry)
        self.host = host
        self.name = "Stream " + str(self.host["incoming_port"])
        self.entity_id = "switch." + slugify(f"{entry.title} {self.name}")
        self._attr_unique_id = f"{entry.entry_id} {self.name}"
        self.host_type = "streams"
        self.host_id = str(host["id"])
        self._init_reachability_state()

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        await self.coordinator.api.enable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        await self.coordinator.api.disable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    @property
    def icon(self):
        """Return the icon of this switch."""
        if self.coordinator.api.is_host_enabled(self.host_id, self.host_type):
            return "mdi:check-network"
        return "mdi:close-network"

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self.coordinator.api.is_host_enabled(self.host_id, self.host_type)

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        base = {
            "id": self.host["id"],
            "forwarding_host": self.host["forwarding_host"],
            "forwarding_port": self.host["forwarding_port"],
        }
        return self._attrs_with_reachability(base)

    async def _update_reachability(self):
        """TCP connect to the upstream target to determine reachability."""
        host = self.host["forwarding_host"]
        port = int(self.host["forwarding_port"])
        try:
            await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
            self._reachable = True
        except (OSError, asyncio.TimeoutError):
            self._reachable = False
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unexpected error probing TCP %s:%s: %s", host, port, err)
            self._reachable = False
        finally:
            self._last_reachability_check = datetime.utcnow()
            self.async_write_ha_state()


class NpmDeadBinarySwitch(_ReachabilityMixin, NpmSwitchesEntity, SwitchEntity):
    """Switches to enable/disable the Dead Host Type in NPM"""

    def __init__(
        self,
        coordinator: NpmSwitchesUpdateCoordinator,
        entry: ConfigEntry,
        host: dict,
    ) -> None:
        """Initialize dead-host switch entity."""
        super().__init__(coordinator, entry)
        self.host = host
        self.name = "404 " + self.host["domain_names"][0].replace(".", " ").capitalize()
        self.entity_id = "switch." + slugify(f"{entry.title} {self.name}")
        self._attr_unique_id = f"{entry.entry_id} {self.name}"
        self.host_type = "dead-hosts"
        self.host_id = str(host["id"])
        self._init_reachability_state()

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        await self.coordinator.api.enable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        await self.coordinator.api.disable_host(self.host_id, self.host_type)
        self.host = await self.coordinator.api.get_host(self.host_id, self.host_type)
        await self._update_reachability()
        self.async_write_ha_state()

    @property
    def icon(self):
        """Return the icon of this switch."""
        if self.coordinator.api.is_host_enabled(self.host_id, self.host_type):
            return "mdi:check-network"
        return "mdi:close-network"

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self.coordinator.api.is_host_enabled(self.host_id, self.host_type)

    @property
    def extra_state_attributes(self):
        """Return device state attributes."""
        scheme = self.host.get("forward_scheme", "http")
        domain = self.host["domain_names"][0]
        base = {
            "id": self.host["id"],
            "domain_names": self.host["domain_names"],
            "url": f"{scheme}://{domain}/",
        }
        return self._attrs_with_reachability(base)

    async def _update_reachability(self):
        """Probe the public URL (should return 404, but server must be reachable)."""
        scheme = self.host.get("forward_scheme", "http")
        domain = self.host["domain_names"][0]
        url = f"{scheme}://{domain}/"
        session = async_get_clientsession(self.hass)

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
                self._http_status = resp.status
                # "Dead host" should respond (often 404). Treat any response as reachable.
                self._reachable = True
        except (aiohttp.ClientError, asyncio.TimeoutError):
            self._reachable = False
            self._http_status = None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unexpected error probing %s: %s", url, err)
            self._reachable = False
            self._http_status = None
        finally:
            self._last_reachability_check = datetime.utcnow()
            self.async_write_ha_state()
