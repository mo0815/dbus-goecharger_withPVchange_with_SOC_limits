# dbus-goecharger_withPVchange_with_SOC_limits
Its a REPO for the GO-E Charger for Victron with Changes like SOC Limits

Thx to gonzo7734

ORIGINAL: https://github.com/gonzo7734/dbus-goecharger

Idea is inspired on @fabian-lauer and @trixing project linked below, many thanks for sharing the knowledge:

https://github.com/fabian-lauer/dbus-shelly-3em-smartmeter

https://github.com/trixing/venus.dbus-twc3

Based on good work from @0x7878 and @vikt0rm

https://github.com/vikt0rm/dbus-goecharger

https://github.com/0x7878/dbus-goecharger

dbus-goecharger Ekrano v2
Configuration-driven transition version for Ekrano/Cerbo GX.

User-specific values live in config.ini.
House-battery SOC hysteresis is active only in Victron Auto mode.
Manual mode is not blocked by SOC control.
AC-In, DC and AC-Out PV sources can be enabled independently.
Offline detection reduces HTTP polling while the go-e is unavailable.
HTTP reads and writes use configurable timeouts.
Missing battery SOC fails safe in Auto mode (charging remains blocked).
Identity D-Bus paths are present even if the charger is offline during service startup.
Opportunity/S2 is intentionally not emulated by this transition version.

Preused:
https://github.com/mo0815/dbus-goecharger_PVonOutput
