# dbus-goecharger Ekrano v2

Configuration-driven transition version for Ekrano/Cerbo GX.

- User-specific values live in `config.ini`.
- House-battery SOC hysteresis is active only in Victron Auto mode.
- Manual mode is not blocked by SOC control.
- AC-In, DC and AC-Out PV sources can be enabled independently.
- Offline detection reduces HTTP polling while the go-e is unavailable.
- HTTP reads and writes use configurable timeouts.
- Missing battery SOC fails safe in Auto mode (charging remains blocked).
- Identity D-Bus paths are present even if the charger is offline during service startup.

Opportunity/S2 is intentionally not emulated by this transition version.
