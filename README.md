# OledGuard

A lightweight Python background utility that protects OLED monitors from burn-in
by dimming inactive windows. Features system tray integration, idle detection via
WinAPI, and Windows autostart support.

## Run

```powershell
pip install -r requirements.txt
pythonw .\oled_guard.pyw
```

The idle timeout is currently set to `10` seconds in `IDLE_THRESHOLD_SECONDS`
for testing. Change it to `180` for the requested 3-minute production delay.
