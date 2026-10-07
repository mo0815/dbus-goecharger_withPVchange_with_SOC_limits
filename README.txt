dbus-goecharger Ekrano transition v2

1. Back up your existing dbus-goecharger.py and config.ini.
2. Replace dbus-goecharger.py with the supplied file.
3. Append the sections from config-v2-additions.ini to your existing config.ini.
4. Restart the existing dbus-goecharger service (or reboot the GX).

Behaviour:
- Manual mode: SOC/PV automation does not intervene.
- Auto mode: house-battery SOC hysteresis controls automatic charging permission.
  Default: release at >=55%, block at <=50%.
- Status 7 is used while Auto is blocked by low battery SOC.
- AC-In, DC and AC-Out PV can be enabled independently.
- After OfflineFailures consecutive go-e failures, normal polling pauses and only
  retries every OfflineRetry seconds.
- HTTP requests use HttpTimeout.
- go-e ids is sent at IdsInterval while Auto is released.

Opportunity/S2 is intentionally not emulated by this temporary GX version.

RECHECK 2 fixes:
- SignOfLifeLog=0 now really disables the timer (prevents a 0-ms GLib busy loop).
- FirmwareVersion and Serial are refreshed after reconnect if startup was offline.
- Incomplete go-e status payloads no longer cause index/key exceptions.
- Successful go-e write commands also restore the online state.
