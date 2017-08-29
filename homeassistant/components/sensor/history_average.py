"""
Calculates the weighted average of a sensor's historical numeric values.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.history_average/
"""
from collections import defaultdict
import logging
import math

import voluptuous as vol

import homeassistant.components.history as history
import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_NAME, CONF_ENTITY_ID, CONF_UNIT_OF_MEASUREMENT,
    EVENT_HOMEASSISTANT_START)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import track_state_change

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'history_average'
DEPENDENCIES = ['history']

CONF_START = 'start'
CONF_END = 'end'
CONF_DURATION = 'duration'
CONF_PERIOD_KEYS = [CONF_START, CONF_END, CONF_DURATION]

DEFAULT_NAME = 'unnamed average'
ICON = 'mdi:chart-line'

ATTR_DURATION = 'duration'


def exactly_two_period_keys(conf):
    """Ensure exactly 2 of CONF_PERIOD_KEYS are provided."""
    provided = 0

    for param in CONF_PERIOD_KEYS:
        if param in conf and conf[param] is not None:
            provided += 1

    if provided != 2:
        raise vol.Invalid('You must provide exactly 2 of the following:'
                          ' start, end, duration')
    return conf


PLATFORM_SCHEMA = vol.All(PLATFORM_SCHEMA.extend({
    vol.Required(CONF_ENTITY_ID): cv.entity_id,
    vol.Optional(CONF_START, default=None): cv.template,
    vol.Optional(CONF_END, default=None): cv.template,
    vol.Optional(CONF_DURATION, default=None): cv.time_period,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=''): cv.string,
}), exactly_two_period_keys)


# noinspection PyUnusedLocal
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the HistoryAverage sensor."""
    entity_id = config.get(CONF_ENTITY_ID)
    start = config.get(CONF_START)
    end = config.get(CONF_END)
    duration = config.get(CONF_DURATION)
    name = config.get(CONF_NAME)
    unit = config.get(CONF_UNIT_OF_MEASUREMENT)

    for template in [start, end]:
        if template is not None:
            template.hass = hass

    add_devices([HistoryAverageSensor(hass, entity_id, start, end,
                                      duration, name, unit)])

    return True


class HistoryAverageSensor(Entity):
    """Representation of a HistoryAverage sensor."""

    def __init__(
            self, hass, entity_id, start, end, duration,
            name, unit):
        """Initialize the HistoryAverage sensor."""
        self._hass = hass
        self._entity_id = entity_id
        self._duration = duration
        self._start = start
        self._end = end
        self._name = name
        self._unit_of_measurement = unit

        self._period = (dt_util.utcnow(), dt_util.utcnow())
        self.value = 0

        def force_refresh(*args):
            """Force the component to refresh."""
            self.schedule_update_ha_state(True)

        # Update value when home assistant starts
        hass.bus.listen_once(EVENT_HOMEASSISTANT_START, force_refresh)

        # Update value when tracked entity changes its state
        track_state_change(hass, entity_id, force_refresh)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return round(self.value, 2)

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit_of_measurement

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def device_state_attributes(self):
        """Return the state attributes of the sensor."""
        hah = HistoryAverageHelper
        return {
            ATTR_DURATION: hah.pretty_duration(self._period),
        }

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        return ICON

    def update(self):
        """Get the latest data and updates the states."""
        # Get previous values of start and end
        p_start, p_end = self._period

        # Parse templates
        self.update_period()
        start, end = self._period

        # Convert times to UTC
        start = dt_util.as_utc(start)
        end = dt_util.as_utc(end)
        p_start = dt_util.as_utc(p_start)
        p_end = dt_util.as_utc(p_end)
        now = dt_util.utcnow()

        # Compute timestamps
        start_timestamp = dt_util.as_timestamp(start)
        end_timestamp = dt_util.as_timestamp(end)
        p_start_timestamp = dt_util.as_timestamp(p_start)
        p_end_timestamp = dt_util.as_timestamp(p_end)
        now_timestamp = dt_util.as_timestamp(now)

        # Only update value every second, and when the period has changed
        if math.floor(start_timestamp) == math.floor(p_start_timestamp) and \
            math.floor(end_timestamp) == math.floor(p_end_timestamp) and \
                math.floor(end_timestamp) <= math.floor(now_timestamp):
            # Don't compute anything as the value cannot have changed by much
            return

        # Get history between start and end
        history_list = history.state_changes_during_period(
            self._hass, start, end, str(self._entity_id))

        last_state = None
        last_time = None
        total_elapsed = 0
        intervals = defaultdict(float)

        # Calculations
        for item in history_list.get(self._entity_id) or []:
            current_state = item.state
            current_time = item.last_changed.timestamp()

            # average only over valid states
            if last_state is not None:
                elapsed = current_time - last_time
                total_elapsed += elapsed
                intervals[float(last_state)] += elapsed

            last_time = current_time
            last_state = current_state

        # Count time elapsed between last history state and end of measure
        if last_state is not None:
            measure_end = min(end_timestamp, now_timestamp)
            elapsed = measure_end - last_time
            total_elapsed += elapsed
            intervals[float(last_state)] += elapsed

        # Calculate the weighted average
        value = 0 if intervals else None
        for state in intervals:
            value += float(state) * (intervals[state] / total_elapsed)

        self.value = value

    def update_period(self):
        """Parse the templates and store a datetime tuple in _period."""
        start = None
        end = None

        # Parse start
        if self._start is not None:
            try:
                start_rendered = self._start.render()
            except (TemplateError, TypeError) as ex:
                HistoryAverageHelper.handle_template_exception(ex, 'start')
                return
            start = dt_util.parse_datetime(start_rendered)
            if start is None:
                try:
                    start = dt_util.utc_from_timestamp(float(start_rendered))
                except ValueError:
                    _LOGGER.error("Parsing error: start must be a datetime"
                                  "or a timestamp")
                    return

        # Parse end
        if self._end is not None:
            try:
                end_rendered = self._end.render()
            except (TemplateError, TypeError) as ex:
                HistoryAverageHelper.handle_template_exception(ex, 'end')
                return
            end = dt_util.parse_datetime(end_rendered)
            if end is None:
                try:
                    print(float(end_rendered))
                    end = dt_util.utc_from_timestamp(float(end_rendered))
                except ValueError:
                    _LOGGER.error("Parsing error: end must be a datetime "
                                  "or a timestamp")
                    return

        # Calculate start or end using the duration
        if start is None:
            start = end - self._duration
        if end is None:
            end = start + self._duration

        self._period = start, end


class HistoryAverageHelper:
    """Static methods to make the HistoryAverageSensor code lighter."""

    @staticmethod
    def period_in_seconds(period):
        """Get the period period duration in seconds."""
        if len(period) != 2 or period[0] == period[1]:
            return 0.0

        return (period[1] - period[0]).total_seconds()

    @staticmethod
    def pretty_duration(period):
        """Format a duration in days, hours, minutes, seconds."""
        seconds = HistoryAverageHelper.period_in_seconds(period)
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        if days > 0:
            return '%dd %dh %dm' % (days, hours, minutes)
        elif hours > 0:
            return '%dh %dm' % (hours, minutes)
        return '%dm' % minutes

    @staticmethod
    def handle_template_exception(ex, field):
        """Log an error nicely if the template cannot be interpreted."""
        if ex.args and ex.args[0].startswith(
                "UndefinedError: 'None' has no attribute"):
            # Common during HA startup - so just a warning
            _LOGGER.warning(ex)
            return
        _LOGGER.error("Error parsing template for field %s", field)
        _LOGGER.error(ex)
