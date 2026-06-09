# OledGuard

A lightweight Python background utility that protects OLED monitors from burn-in
by dimming the screen after user inactivity. It runs in the Windows system tray,
uses WinAPI idle detection, supports Windows autostart, and stores user settings.

## Features

- Background tray app without a console window.
- Full-screen transparent overlay across the virtual desktop.
- Idle detection via `GetLastInputInfo`.
- Configurable timeout: 10 seconds, 1, 3, 5, or 10 minutes.
- Configurable dim intensity: 20%, 40%, 50%, 60%, or 80%.
- Pause toggle from the tray menu.
- Windows startup toggle from the tray menu.
- Settings saved in `%APPDATA%\OledGuard\config.json`.
- Rotating log file at `%APPDATA%\OledGuard\oled_guard.log`.
- Single-instance guard to avoid duplicate tray apps.

## Run

```powershell
pip install -r requirements.txt
pythonw .\oled_guard.pyw
```

Right-click the tray icon to pause protection, change timeout/intensity, toggle
startup, or exit the app.

## Notes

The current implementation uses a click-through fullscreen overlay instead of
modifying the active window directly. This protects static UI across multiple
windows and monitors more consistently than changing only the foreground window.
