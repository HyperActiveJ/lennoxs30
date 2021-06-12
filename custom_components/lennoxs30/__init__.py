from homeassistant.components.lennoxs30.api.s30exception import EC_HTTP_ERR, EC_LOGIN, EC_UNAUTHORIZED, S30Exception
from homeassistant.exceptions import HomeAssistantError
import logging
import asyncio
from .api import s30api_async
#
DOMAIN = "lennoxs30"
DOMAIN_STATE = "lennoxs30.state"

DS_CONNECTING = "Connecting"
DS_DISCONNECTED = "Disconnected"
DS_LOGIN_FAILED = "Login Failed"
DS_CONNECTED = "Connected"
DS_RETRY_WAIT = "Waiting to Retry"
DS_FAILED = "Failed"

#
_LOGGER = logging.getLogger(__name__)
#
from homeassistant.const import (CONF_EMAIL, CONF_PASSWORD)
#

MAX_ERRORS = 5
RETRY_INTERVAL_SECONDS = 60
POLLING_INTERVAL = 10

async def async_setup(hass, config) -> bool:
    _LOGGER.info("async_setup starting")

    email = config.get(DOMAIN).get(CONF_EMAIL)
    password = config.get(DOMAIN).get(CONF_PASSWORD)
    s30api = s30api_async.s30api_async(email, password)
    hass.states.async_set(DOMAIN_STATE, DS_CONNECTING, s30api.metrics.getMetricList())
    try:
        await s30_initalize(s30api, hass, config)
    except S30Exception as e:
        _LOGGER.error("async_setup: " + str(e))
        if e.error_code == EC_LOGIN:
            hass.states.async_set(DOMAIN_STATE, DS_LOGIN_FAILED, s30api.metrics.getMetricList())
            raise HomeAssistantError("Lennox30 unable to login - please check credentials and restart Home Assistant")
        _LOGGER.info("retying initialization")
        hass.states.async_set(DOMAIN_STATE, DS_RETRY_WAIT, s30api.metrics.getMetricList())
        asyncio.create_task(initialize_retry_task(s30api, hass, config))
        return False
    
    hass.states.async_set(DOMAIN_STATE, DS_CONNECTED, s30api.metrics.getMetricList())
    _LOGGER.info("async_setup complete")
    return True
 
async def s30_initalize(s30api: s30api_async, hass, config):
    await connect_subscribe(s30api, hass)
    await configuration_initialization(s30api, hass)
    # Launch the message pump loop
    asyncio.create_task(messagePump_task(s30api, hass))
    hass.helpers.discovery.load_platform('climate', DOMAIN, s30api, config)

async def initialize_retry_task(s30api: s30api_async, hass, config):
    while (True):
        hass.states.async_set(DOMAIN_STATE, DS_RETRY_WAIT, s30api.metrics.getMetricList())
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        hass.states.async_set(DOMAIN_STATE, DS_CONNECTING, s30api.metrics.getMetricList())
        try:
            await s30_initalize(s30api, hass, config)
            hass.states.async_set(DOMAIN_STATE, DS_CONNECTED, s30api.metrics.getMetricList())
            return
        except S30Exception as e:
            _LOGGER.error("async_setup: " + str(e))
            if e.error_code == EC_LOGIN:
                hass.states.async_set(DOMAIN_STATE, DS_LOGIN_FAILED, s30api.metrics.getMetricList())
                raise HomeAssistantError("Lennox30 unable to login - please check credentials and restart Home Assistant")
            _LOGGER.info("retying initialization")
            hass.states.async_set(DOMAIN_STATE, DS_RETRY_WAIT, s30api.metrics.getMetricList())

async def configuration_initialization(s30api: s30api_async, hass) ->None:
     # Wait for zones to appear on each system
    sytemsWithZones = 0
    numOfSystems = len(s30api.getSystems())
    while (sytemsWithZones < numOfSystems):
        # TODO - should add a timeout and spin the APPLICATION_ID
        _LOGGER.debug("__init__:async_setup waiting for zone config to arrive numSystems [" + str(numOfSystems) + "] sytemsWithZones [" + str(sytemsWithZones) + "]")
        sytemsWithZones = 0
        await asyncio.sleep(1)
        await s30api.messagePump()
        for lsystem in s30api.getSystems():
                numZones = len(lsystem.getZoneList())
                _LOGGER.debug("__init__:async_setup wait for zones system [" + lsystem.sysId + "] numZone [" + str(numZones) + "]")
                if numZones > 0:
                    sytemsWithZones += 1

async def connect_subscribe(s30api: s30api_async, hass):
    await s30api.serverConnect()

    for lsystem in s30api.getSystems():
        await s30api.subscribe(lsystem)

async def reinitialize_task(s30api: s30api_async, hass) -> None:
    while True:
        try:
            hass.states.async_set(DOMAIN_STATE, DS_CONNECTING, s30api.metrics.getMetricList())
            _LOGGER.info("reinitialize_task - trying reconnect")
            await connect_subscribe(s30api)
            hass.states.async_set(DOMAIN_STATE, DS_CONNECTED, s30api.metrics.getMetricList())
            break
        except S30Exception as e:
            _LOGGER.error("reinitialize_task: " + str(e))
            if e.error_code == EC_LOGIN:
                raise HomeAssistantError("Lennox30 unable to login - please check credentials and restart Home Assistant")
        hass.states.async_set(DOMAIN_STATE, DS_RETRY_WAIT, s30api.metrics.getMetricList())
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)

    _LOGGER.info("reinitialize_task - reconnect successful")
    asyncio.create_task(messagePump_task(s30api))

async def messagePump_task(s30api : s30api_async.s30api_async, hass) -> None:
    # TODO figure out a way to shutdown
    await asyncio.sleep(10)
    reinitialize = False
    err_cnt: int = 0
    update_counter: int = 0
    while reinitialize == False:
        bErr = False
        try:
            update_counter += 1
            _LOGGER.debug("messagePump_task running")
            await s30api.messagePump()
            if (update_counter == 6):
                hass.states.async_set(DOMAIN_STATE, DS_CONNECTED, s30api.metrics.getMetricList(), force_update = True)
                update_counter = 0

            await asyncio.sleep(POLLING_INTERVAL)
        except S30Exception as e:
            err_cnt += 1
            # This should mean we have been logged out and need to start the login process
            if e.error_code == EC_UNAUTHORIZED:
                _LOGGER.info("messagePump_task - unauthorized - trying to relogin")
                reinitialize = True
            # If its an HTTP error, we will not log an error, just and info message, unless
            # this exeeeds the max consecutive error count
            elif e.error_code == EC_HTTP_ERR and err_cnt < MAX_ERRORS:
              _LOGGER.info("messagePump_task - S30Exception " + str(e))
            else:
              _LOGGER.error("messagePump_task - S30Exception " + str(e))
            bErr = True
        except Exception as e:          
            _LOGGER.error("messagePump_task - Exception " + str(e))
            err_cnt += 1
            bErr = True
        # Keep retrying retrive up until we get this number of errors in a row, at which point will try to reconnect
        if err_cnt > MAX_ERRORS:
            reinitialize = True
        if bErr is False:
            err_cnt = 0

    if reinitialize == True:
        hass.states.async_set(DOMAIN_STATE, DS_DISCONNECTED, s30api.metrics.getMetricList())
        asyncio.create_task(reinitialize_task(s30api, hass))
        _LOGGER.info("messagePump_task is exiting - to enter retries")
    else:
        _LOGGER.warning("messagePump_task is exiting - with no reschelued retries - no data will be received anymore")
        hass.states.async_set(DOMAIN_STATE, DS_FAILED, s30api.metrics.getMetricList())


