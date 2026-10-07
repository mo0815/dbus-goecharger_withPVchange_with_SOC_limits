#!/usr/bin/env python
 
# import normal packages
import platform 
import logging
import logging.handlers
import sys
import os
import sys
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import sys
import time
import requests # for http GET
import configparser # for config/ini file

# for AutomaticMode
import dbus

 
# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService


class DbusGoeChargerService:
  def __init__(self, servicename, paths, productname='go-eCharger', connection='go-eCharger HTTP JSON service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    hardwareVersion = int(config['DEFAULT']['HardwareVersion'])
    pauseBetweenRequests = int(config['ONPREMISE']['PauseBetweenRequests']) # in ms
    position = int(config['DEFAULT'].get('Position', '1'))

    # Validate user-editable configuration once at startup.
    startSoc = config.getfloat('AUTO_CHARGE', 'StartSoc', fallback=55.0)
    stopSoc = config.getfloat('AUTO_CHARGE', 'StopSoc', fallback=50.0)
    if not (0 <= stopSoc < startSoc <= 100):
      raise ValueError("SOC limits invalid: require 0 <= StopSoc < StartSoc <= 100")
    if position not in (0, 1, 2):
      raise ValueError("Position must be 0, 1 or 2")

    if pauseBetweenRequests <= 20:
      raise ValueError("Pause between requests must be greater than 20")

    if hardwareVersion < 3:
      raise ValueError("Minimum hardware version required is 3.")

    self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
    self._paths = paths
    
    logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))
    
    paths_wo_unit = [
      '/Status'#,  # value 'car' 1: charging station ready, no vehicle 2: vehicle loads 3: Waiting for vehicle 4: Charge finished, vehicle still connected
      #'/Mode' 
    ]
    
    #get data from go-eCharger
    data = self._getGoeChargerData()

    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unkown version, and running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)
    
    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductId', 0xFFFF) # 
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', productname)    
    # Always expose mandatory identity paths, even when the charger is offline at startup.
    self._dbusservice.add_path('/FirmwareVersion', data.get('fwv', '') if data else '')
    self._dbusservice.add_path('/Serial', data.get('sse', '') if data else '')
    self._dbusservice.add_path('/HardwareVersion', hardwareVersion)
    self._dbusservice.add_path('/Connected', 1 if data else 0)
    self._dbusservice.add_path('/UpdateIndex', 0)
    self._dbusservice.add_path("/Position", position)
    #self._dbusservice.add_path("/Mode", 1)

    # add paths without units
    for path in paths_wo_unit:
      self._dbusservice.add_path(path, None)
    
    # add path values to dbus
    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)



    # last update
    self._lastUpdate = 0
    
    # charging time in float
    self._chargingTime = 0.0

    # v2 runtime state
    self._failureCount = 0
    self._offline = False
    self._nextOfflineRetry = 0.0
    self._autoSocReleased = False
    self._lastIdsUpdate = 0.0

    # add _update function 'timer'
    gobject.timeout_add(pauseBetweenRequests, self._update)
    
    # Add sign-of-life timer only when enabled. A 0 ms GLib timer would busy-loop.
    sign_of_life_minutes = self._getSignOfLifeInterval()
    if sign_of_life_minutes > 0:
      gobject.timeout_add(sign_of_life_minutes * 60 * 1000, self._signOfLife)
 
  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config
 
 
  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']
    
    if not value:
        value = 0
    return max(0, int(value))
  
  
  def _getGoeChargerStatusUrl(self):
    config = self._getConfig()
    accessType = config['DEFAULT']['AccessType']
    
    if accessType == 'OnPremise': 
        URL = "http://%s/api/status?filter=fwv,sse,nrg,wh,alw,amp,ama,car" % (config['ONPREMISE']['Host'])
    else:
        raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))
    
    return URL
  
  def _getGoeChargerAPIPayloadUrl(self, parameter, value):
    config = self._getConfig()
    accessType = config['DEFAULT']['AccessType']
    
    if accessType == 'OnPremise': 
        URL = "http://%s/api/set?%s=%s" % (config['ONPREMISE']['Host'], parameter, value)
        logging.info("Folgende URL wird getriggert: %s" % (URL))
    else:
        raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))
    
    return URL
  
  def _setGoeChargerValue(self, parameter, value):
    logging.info("Parameter hat folgenden Wert: %s" % (parameter))
    logging.info("value hat folgenden Wert: %s" % (value))
    URL = self._getGoeChargerAPIPayloadUrl(parameter, str(value))
    logging.info("URL auf %s gesetzt" % (URL))
    try:
      request_data = requests.get(url=URL, timeout=self._cfg_float('CONNECTION', 'HttpTimeout', 2.0))
    except Exception as e:
      logging.warning("go-e write failed (%s=%s): %s", parameter, value, e)
      self._markFailure()
      return False
    logging.info("Request_data hat Inhalt: %s" % (request_data))

    # check for response
    if not request_data:
      logging.warning("No response from go-eCharger - %s" % (URL))
      self._markFailure()
      return False

    
    try:
      json_data = request_data.json()
    except Exception as e:
      logging.warning("Invalid JSON response from go-e: %s", e)
      self._markFailure()
      return False

    logging.info("json_data[parameter] hat folgenden Wert: %s" % json_data.get(parameter))

    # check for Json
    if not json_data:
        logging.error("Converting response to JSON failed")
        self._markFailure()
        return False
    
    returned = json_data.get(parameter)
    if str(returned) == "true" or str(returned) == "True" or str(returned) == str(value):
      # A successful command also proves that the charger is reachable.
      self._markOnline()
      return True
    else:
      logging.error("go-eCharger parameter %s not set to %s" % (parameter, str(value)))
      return False

 
  def _getGoeChargerData(self):
    URL = self._getGoeChargerStatusUrl()
    try:
       request_data = requests.get(url = URL, timeout=self._cfg_float('CONNECTION', 'HttpTimeout', 2.0))
    except Exception:
       return None
    
    # check for response
    if not request_data:
        raise ConnectionError("No response from go-eCharger - %s" % (URL))
    
    try:
      json_data = request_data.json()
    except Exception as e:
      logging.warning("Invalid JSON status response from go-e: %s", e)
      return None
    if not json_data:
      return None
    
    
    return json_data
 

  def _setGoeChargerAutomaticModeValues(self):
    config = self._getConfig()
    if config['DEFAULT']['AccessType'] != 'OnPremise':
      raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))

    bus = dbus.SystemBus()

    # Battery SOC hysteresis applies ONLY in Auto mode.
    if not self._updateSocRelease(bus):
      self._dbusservice['/Status'] = 7  # Low SOC
      # Force off while Auto is blocked by house battery SOC.
      self._setGoeChargerValue('frc', 1)
      return False

    pGrid = (self._dbus_value(bus, '/Ac/Grid/L1/Power') +
             self._dbus_value(bus, '/Ac/Grid/L2/Power') +
             self._dbus_value(bus, '/Ac/Grid/L3/Power'))

    pPv = 0.0
    if self._cfg_bool('PV', 'AC_IN_PV', False):
      pPv += (self._dbus_value(bus, '/Ac/PvOnGrid/L1/Power') +
              self._dbus_value(bus, '/Ac/PvOnGrid/L2/Power') +
              self._dbus_value(bus, '/Ac/PvOnGrid/L3/Power'))
    if self._cfg_bool('PV', 'AC_OUT_PV', True):
      pPv += (self._dbus_value(bus, '/Ac/PvOnOutput/L1/Power') +
              self._dbus_value(bus, '/Ac/PvOnOutput/L2/Power') +
              self._dbus_value(bus, '/Ac/PvOnOutput/L3/Power'))
    if self._cfg_bool('PV', 'DC_PV', False):
      pPv += self._dbus_value(bus, '/Dc/Pv/Power')

    pAkku = self._dbus_value(bus, '/Dc/Battery/Power') * -1
    logging.debug("Auto values pGrid=%s pAkku=%s pPv=%s", pGrid, pAkku, pPv)

    # go-e requires ids cyclically. Keep this independent from the fast UI poll.
    idsInterval = max(1.0, self._cfg_float('AUTO_CHARGE', 'IdsInterval', 5.0))
    if time.time() - self._lastIdsUpdate >= idsInterval:
      URL = 'http://%s/api/set?ids={"pGrid":%s,"pAkku":%s,"pPv":%s}' % (config['ONPREMISE']['Host'], pGrid, pAkku, pPv)
      try:
        response = requests.get(url=URL, timeout=self._cfg_float('CONNECTION', 'HttpTimeout', 2.0))
        if not response:
          logging.warning("go-e ids update returned HTTP error")
          return False
        self._lastIdsUpdate = time.time()
      except Exception as e:
        logging.warning("go-e ids update failed: %s", e)
        return False
    return True




  def _cfg_bool(self, section, key, default=False):
    config = self._getConfig()
    try:
      return config.getboolean(section, key)
    except Exception:
      return default

  def _cfg_int(self, section, key, default):
    config = self._getConfig()
    try:
      return config.getint(section, key)
    except Exception:
      return default

  def _cfg_float(self, section, key, default):
    config = self._getConfig()
    try:
      return config.getfloat(section, key)
    except Exception:
      return default

  def _dbus_value(self, bus, path, default=0.0):
    try:
      value = (bus.get_object('com.victronenergy.system', path)).GetValue()
      if value is None:
        return default
      return float(value)
    except Exception as e:
      logging.debug("D-Bus path unavailable %s: %s", path, e)
      return default

  def _getBatterySoc(self, bus):
    try:
      value = (bus.get_object('com.victronenergy.system', '/Dc/Battery/Soc')).GetValue()
      return None if value is None else float(value)
    except Exception as e:
      logging.warning("Battery SOC unavailable: %s", e)
      return None

  def _updateSocRelease(self, bus):
    if not self._cfg_bool('AUTO_CHARGE', 'BatterySocControl', True):
      self._autoSocReleased = True
      return True
    soc = self._getBatterySoc(bus)
    if soc is None:
      # Fail safe: without a valid house-battery SOC, Auto must not charge.
      self._autoSocReleased = False
      return False
    startSoc = self._cfg_float('AUTO_CHARGE', 'StartSoc', 55.0)
    stopSoc = self._cfg_float('AUTO_CHARGE', 'StopSoc', 50.0)
    if stopSoc >= startSoc:
      logging.error("StopSoc must be lower than StartSoc (%.1f >= %.1f)", stopSoc, startSoc)
      return False
    if soc >= startSoc:
      self._autoSocReleased = True
    elif soc <= stopSoc:
      self._autoSocReleased = False
    return self._autoSocReleased

  def _setConnected(self, connected):
    self._dbusservice['/Connected'] = 1 if connected else 0

  def _markOnline(self):
    if self._offline:
      logging.info("go-eCharger reachable again")
    self._failureCount = 0
    self._offline = False
    self._nextOfflineRetry = 0.0
    self._setConnected(True)

  def _markFailure(self):
    self._failureCount += 1
    limit = max(1, self._cfg_int('CONNECTION', 'OfflineFailures', 3))
    if self._failureCount >= limit:
      if not self._offline:
        logging.warning("go-eCharger offline after %d consecutive failures", self._failureCount)
      self._offline = True
      self._setConnected(False)
      self._nextOfflineRetry = time.time() + max(1, self._cfg_int('CONNECTION', 'OfflineRetry', 15))

  def _offlineRetryDue(self):
    return (not self._offline) or time.time() >= self._nextOfflineRetry

  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
    logging.info("--- End: sign of life ---")
    return True
 
  def _update(self):
    try:
      # While offline do not execute the expensive normal loop. Only probe at
      # the configured retry interval.
      if not self._offlineRetryDue():
        return True

      data = self._getGoeChargerData()
      if data is None:
        self._markFailure()
        return True

      self._markOnline()
      # Identity may have been unavailable when the service started offline.
      if data.get('fwv') is not None:
        self._dbusservice['/FirmwareVersion'] = data.get('fwv')
      if data.get('sse') is not None:
        self._dbusservice['/Serial'] = data.get('sse')

      # Reject incomplete status payloads without indexing missing nrg values.
      required = ('nrg', 'wh', 'alw', 'amp', 'ama', 'car')
      if any(k not in data for k in required) or not isinstance(data.get('nrg'), (list, tuple)) or len(data.get('nrg')) < 12:
        logging.warning("Incomplete go-e status payload; keeping service online but skipping this sample")
        return True

      modestatus = self._dbusservice['/Mode']

      # All SOC/PV automatic control is strictly Auto mode only.
      autoReleased = True
      if modestatus == 1:
        try:
          autoReleased = self._setGoeChargerAutomaticModeValues()
        except Exception as e:
          logging.warning("Automatic mode update failed: %s", e)
          self._markFailure()
          return True

      self._dbusservice['/Ac/L1/Power'] = int(data['nrg'][7])
      self._dbusservice['/Ac/L2/Power'] = int(data['nrg'][8])
      self._dbusservice['/Ac/L3/Power'] = int(data['nrg'][9])
      self._dbusservice['/Ac/Power'] = int(data['nrg'][11])
      self._dbusservice['/Current'] = max(data['nrg'][4], data['nrg'][5], data['nrg'][6])
      self._dbusservice['/Ac/Energy/Forward'] = round(data['wh'] / 1000, 2)
      self._dbusservice['/SetCurrent'] = int(data['amp'])
      self._dbusservice['/MaxCurrent'] = int(data['ama'])
      # Keep the Victron Start/Stop state synchronized with the actual charger.
      # go-e alw: 0 = charging not allowed, 1 = charging allowed.
      if 'alw' in data:
        self._dbusservice['/StartStop'] = 1 if int(data['alw']) else 0

      timeDelta = time.time() - self._lastUpdate
      if int(data['car']) == 2 and self._lastUpdate > 0:
        self._chargingTime += timeDelta
      elif int(data['car']) == 1:
        self._chargingTime = 0
      self._dbusservice['/ChargingTime'] = int(self._chargingTime)

      # Do not overwrite Low SOC while Auto is blocked.
      if not (modestatus == 1 and not autoReleased):
        status = 0
        if int(data['car']) == 1:
          status = 0
        elif int(data['car']) == 2:
          status = 2
        elif int(data['car']) == 3:
          status = 6
        elif int(data['car']) == 4:
          status = 3
        self._dbusservice['/Status'] = status

      index = self._dbusservice['/UpdateIndex'] + 1
      if index > 255:
        index = 0
      self._dbusservice['/UpdateIndex'] = index
      self._lastUpdate = time.time()

    except Exception as e:
      logging.error('Error at _update: %s', e, exc_info=True)
      self._markFailure()
    return True

  def _handlechangedvalue(self, path, value):
    logging.warning("someone else updated %s to %s" % (path, value))
    MaxCurrent = self._dbusservice['/MaxCurrent']
    
    if path == '/SetCurrent':
      if value > MaxCurrent:
        logging.warning("SetCurrent is higher than MaxCurrent. Limit reached. Set SetCurrent to MaxCurrent!")
        return self._setGoeChargerValue('amp', MaxCurrent)
      return self._setGoeChargerValue('amp', value)
    elif path == '/StartStop':
      # Wenn Automatisch (Modestatus = 1), dann im GoECharger auf 0 (Ueberschuss-Laden aktivieren) oder 1 (Laden deaktivieren) stellen
      # wenn geplant (Modestatus = 2), dann im GoECharger auf x stellen - nicht implementiert
      # wenn manuell (Modestatus = 0), dann im GoECharger auf 1 (Laden deaktivieren) und 2 (Laden aktivieren) stellen
      modestatus = self._dbusservice['/Mode']
      if modestatus == 0:
        return self._setGoeChargerValue('frc', value + 1)

      elif modestatus == 1:
        # pruefen, wo StartStop steht
        if value == 1:
          return self._setGoeChargerValue('frc', 0)
        if value == 0:
          return self._setGoeChargerValue('frc', 1)
      else:
        return False

    elif path == '/MaxCurrent':
      logging.warning("It's not allowed to set MaxCurrent via Victron! set MaxCurrent in your Go eCharger!")
      return False
      #return self._setGoeChargerValue('ama', value)
    elif path == '/Mode':
      logging.info("/Mode value %s" % (value))
      StartStop = self._dbusservice['/StartStop']
      logging.info("StartStop ist: %s" % (StartStop))
      lmo = 0
      frc = 1
      # Victron Mode 0 = manual | Go eCharger Loading Mode:"basic", parameter lmo = 3 
      # Victron Mode 1 = automatic | go eCharger Loading Mode: "eco", parameter lmo = 4
      # Victron Mode 2 = scheduled | go eCharger Loading Mode: "daily trip", parameter lmo = 5 - nicht implementiert
      if value == 0:
        lmo = 3
        if (StartStop == 1):
          frc = 2
        logging.info("lmo auf %s gesetzt" % (lmo))
      elif value == 1:
        lmo = 4
        if (StartStop == 1):
          frc = 0
        logging.info("lmo auf %s gesetzt" % (lmo))
      elif value == 2:
        lmo = 5
        logging.info("lmo auf %s gesetzt" % (lmo))
      else:
        logging.info("lmo nicht gesetzt, ELSE Part")
        return False
      logging.info("jetzt wird setGoeChargerValue mit lmo = %s aufgerufen." % (lmo))
      modeswitch = False
      if (self._setGoeChargerValue('lmo', lmo) == True and self._setGoeChargerValue('frc', frc) == True):
        modeswitch = True
      return modeswitch
    else:
      logging.error("mapping for evcharger path %s does not exist" % (path))
      return False


def main():
  #configure logging
  config = configparser.ConfigParser()
  config.read(f"{(os.path.dirname(os.path.realpath(__file__)))}/config.ini")
  logging_level = config["DEFAULT"]["Logging"].upper()

  log_rotate_handler = logging.handlers.RotatingFileHandler(
    maxBytes=512000,
    backupCount=2,
    encoding=None,
    delay=0,
    filename="%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))
  )


  logging.basicConfig(      format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=logging_level,
                            
                            handlers=[
                                #logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))), # removed due to hint from Philipp Trenz @ Community.victron.com and added log_rotate_handler
                                logging.StreamHandler(),
                                log_rotate_handler
                            ])
 
  try:
      logging.info("Start")
  
      from dbus.mainloop.glib import DBusGMainLoop
      # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
      DBusGMainLoop(set_as_default=True)
     
      #formatting 
      _kwh = lambda p, v: (str(round(v, 2)) + 'kWh')
      _a = lambda p, v: (str(round(v, 1)) + 'A')
      _w = lambda p, v: (str(round(v, 1)) + 'W')
      _v = lambda p, v: (str(round(v, 1)) + 'V')
      _degC = lambda p, v: (str(v) + '°C')
      _s = lambda p, v: (str(v) + 's')
     
      #start our main-service
      pvac_output = DbusGoeChargerService(
        servicename='com.victronenergy.evcharger',
        paths={
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L2/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L3/Power': {'initial': 0, 'textformat': _w},
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/ChargingTime': {'initial': 0, 'textformat': _s},
          
          '/Ac/Voltage': {'initial': 0, 'textformat': _v},
          '/Current': {'initial': 0, 'textformat': _a},
          '/SetCurrent': {'initial': 0, 'textformat': _a},
          '/MaxCurrent': {'initial': 0, 'textformat': _a},
          '/MCU/Temperature': {'initial': 0, 'textformat': _degC},
          '/StartStop': {'initial': 0, 'textformat': lambda p, v: (str(v))},
          '/Mode': {'initial': 0, 'textformat': lambda p, v: (str(v))}
        }
        )
     
      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()            
  except Exception as e:
    logging.critical('Error at %s', 'main', exc_info=e)
if __name__ == "__main__":
  main()