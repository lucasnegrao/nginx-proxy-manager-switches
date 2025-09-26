"""Adds config flow for Blueprint."""
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import slugify
import voluptuous as vol

from .api import NpmSwitchesApiClient
from .const import (
    CONF_NPM_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    CONF_INDLUDE_PROXY,
    CONF_INCLUDE_REDIR,
    CONF_INCLUDE_STREAMS,
    CONF_INCLUDE_DEAD,
    CONF_INCLUDE_SENSORS,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_NPM_URL,
    DEFAULT_INDLUDE_PROXY,
    DEFAULT_INCLUDE_REDIR,
    DEFAULT_INCLUDE_STREAMS,
    DEFAULT_INCLUDE_DEAD,
    DEFAULT_INCLUDE_SENSORS,
    # New imports for reachability interval
    CONF_REACHABILITY_INTERVAL_MINUTES,
    DEFAULT_REACHABILITY_INTERVAL_MINUTES,
)


class NPMSwitchesFloHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for NPM Switches."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize."""
        self._errors = {}
        self.clean_npm_url = None

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        self._errors = {}

        # Uncomment the next 2 lines if only a single instance of the integration is allowed:
        # if self._async_current_entries():
        #     return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            scheme_end = user_input[CONF_NPM_URL].find("://") + 3
            self.clean_npm_url = user_input[CONF_NPM_URL][scheme_end:]
            user_input["clean_npm_url"] = slugify(f"{self.clean_npm_url}")

            existing_entry = self._async_entry_for_username(self.clean_npm_url)
            if existing_entry:
                return self.async_abort(reason="already_configured")

            valid = await self._test_credentials(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input[CONF_NPM_URL],
            )
            if valid:
                # Store initial values (including reachability interval) in data
                return self.async_create_entry(
                    title=self.clean_npm_url, data=user_input
                )
            else:
                self._errors["base"] = "auth"

            return await self._show_config_form()

        return await self._show_config_form()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NPMSwitchesOptionsFlowHandler(config_entry)

    async def _show_config_form(self):  # pylint: disable=unused-argument
        """Show the configuration form to edit location data."""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                    vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                    vol.Required(CONF_NPM_URL, default=DEFAULT_NPM_URL): str,
                    vol.Optional(CONF_INCLUDE_SENSORS, default=DEFAULT_INCLUDE_SENSORS): bool,
                    vol.Optional(CONF_INDLUDE_PROXY, default=DEFAULT_INDLUDE_PROXY): bool,
                    vol.Optional(CONF_INCLUDE_REDIR, default=DEFAULT_INCLUDE_REDIR): bool,
                    vol.Optional(CONF_INCLUDE_STREAMS, default=DEFAULT_INCLUDE_STREAMS): bool,
                    vol.Optional(CONF_INCLUDE_DEAD, default=DEFAULT_INCLUDE_DEAD): bool,
                    # New: reachability interval (minutes)
                    vol.Optional(
                        CONF_REACHABILITY_INTERVAL_MINUTES,
                        default=DEFAULT_REACHABILITY_INTERVAL_MINUTES,
                    ): vol.All(int, vol.Range(min=0, max=1440)),
                }
            ),
            errors=self._errors,
        )

    async def _test_credentials(self, username, password, npm_url):
        """Return true if credentials is valid."""
        try:
            session = async_create_clientsession(self.hass)
            client = NpmSwitchesApiClient(username, password, npm_url, session)
            await client.async_get_new_token()
            return True
        except Exception:  # pylint: disable=broad-except
            pass
        return False

    @callback
    def _async_entry_for_username(self, username):
        """Find an existing entry for a username."""
        for entry in self._async_current_entries():
            if entry.title == username:
                return entry
        return None


class NPMSwitchesOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler to change settings after setup."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):  # pylint: disable=unused-argument
        """Manage the options."""
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self.options.update(user_input)
            return self.async_create_entry(title="", data=self.options)

        # Derive default from options -> data -> constant
        default_interval = self.config_entry.options.get(
            CONF_REACHABILITY_INTERVAL_MINUTES,
            self.config_entry.data.get(
                CONF_REACHABILITY_INTERVAL_MINUTES, DEFAULT_REACHABILITY_INTERVAL_MINUTES
            ),
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_REACHABILITY_INTERVAL_MINUTES,
                        default=default_interval,
                    ): vol.All(int, vol.Range(min=0, max=1440)),
                }
            ),
        )
