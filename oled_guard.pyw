# -*- coding: utf-8 -*-

import ctypes
import json
import logging
import logging.handlers
import os
import sys
import threading
import winreg
from ctypes import wintypes

import pystray
import win32api
import win32con
import win32gui
from PIL import Image, ImageDraw


APP_NAME = "OledGuard"
CHECK_INTERVAL_SECONDS = 2
WAKE_CHECK_INTERVAL_SECONDS = 0.1
OVERLAY_START_TIMEOUT_SECONDS = 5
MUTEX_NAME = r"Local\OledGuardSingleInstance"
ERROR_ALREADY_EXISTS = 183
DEFAULT_SETTINGS = {
    "idle_threshold_seconds": 180,
    "dim_alpha": 128,
}
VALID_TIMEOUTS = {10, 60, 180, 300, 600}
VALID_DIM_ALPHAS = {51, 102, 128, 153, 204}

# Custom Win32 message identifiers
WM_START_FADE_IN = win32con.WM_USER + 1
WM_CANCEL_FADE = win32con.WM_USER + 2
WM_ENSURE_TOPMOST = win32con.WM_USER + 3


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class OledGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._paused = False
        self._mutex_handle = None

        self.config_dir = os.path.join(self._get_appdata_dir(), APP_NAME)
        self.config_path = os.path.join(self.config_dir, "config.json")
        self.log_path = os.path.join(self.config_dir, "oled_guard.log")
        self._ensure_config_dir()
        self._setup_logging()
        self._mutex_handle = self._acquire_single_instance()
        if not self._mutex_handle:
            logging.info("Another OledGuard instance is already running.")
            sys.exit(0)

        self.settings = self._load_settings()

        self._overlay_hwnd = None
        self._overlay_ready = threading.Event()
        self._dimmed = False
        self._overlay_available = False

        self._start_overlay_thread()

        self._monitor_thread = threading.Thread(
            target=self._monitor_idle,
            name="OledGuardMonitor",
            daemon=True,
        )
        self.icon = pystray.Icon(
            APP_NAME,
            self._create_icon_image(),
            APP_NAME,
            self._create_menu(),
        )

    def run(self):
        logging.info("OledGuard started.")
        self._monitor_thread.start()
        self.icon.run()

    def _get_appdata_dir(self):
        return os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")

    def _ensure_config_dir(self):
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except OSError:
            pass

    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                logging.handlers.RotatingFileHandler(
                    self.log_path,
                    maxBytes=256 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
            ],
        )

    def _acquire_single_instance(self):
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            logging.warning("Failed to create single-instance mutex.")
            return None

        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            ctypes.windll.kernel32.CloseHandle(handle)
            return None

        return handle

    def _load_settings(self):
        settings = dict(DEFAULT_SETTINGS)

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        settings.update(data)
            except Exception:
                logging.exception("Failed to load settings; using defaults.")

        return self._validate_settings(settings)

    def _validate_settings(self, settings):
        validated = dict(DEFAULT_SETTINGS)

        try:
            timeout = int(settings.get("idle_threshold_seconds", DEFAULT_SETTINGS["idle_threshold_seconds"]))
            if timeout in VALID_TIMEOUTS:
                validated["idle_threshold_seconds"] = timeout
            else:
                logging.warning("Invalid idle timeout in config: %r", timeout)
        except (TypeError, ValueError):
            logging.warning("Invalid idle timeout type in config: %r", settings.get("idle_threshold_seconds"))

        try:
            alpha = int(settings.get("dim_alpha", DEFAULT_SETTINGS["dim_alpha"]))
            if alpha in VALID_DIM_ALPHAS:
                validated["dim_alpha"] = alpha
            else:
                logging.warning("Invalid dim alpha in config: %r", alpha)
        except (TypeError, ValueError):
            logging.warning("Invalid dim alpha type in config: %r", settings.get("dim_alpha"))

        return validated

    def _save_settings(self):
        self._ensure_config_dir()
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
        except Exception:
            logging.exception("Failed to save settings.")

    def _create_menu(self):
        # Timeouts submenu
        timeouts_menu = pystray.Menu(
            pystray.MenuItem("10 секунд (Тест)", lambda icon, item: self._set_timeout(10), checked=lambda item: self.settings["idle_threshold_seconds"] == 10),
            pystray.MenuItem("1 минута", lambda icon, item: self._set_timeout(60), checked=lambda item: self.settings["idle_threshold_seconds"] == 60),
            pystray.MenuItem("3 минуты", lambda icon, item: self._set_timeout(180), checked=lambda item: self.settings["idle_threshold_seconds"] == 180),
            pystray.MenuItem("5 минут", lambda icon, item: self._set_timeout(300), checked=lambda item: self.settings["idle_threshold_seconds"] == 300),
            pystray.MenuItem("10 минут", lambda icon, item: self._set_timeout(600), checked=lambda item: self.settings["idle_threshold_seconds"] == 600),
        )

        # Intensity submenu
        intensity_menu = pystray.Menu(
            pystray.MenuItem("Слабая (20%)", lambda icon, item: self._set_intensity(51), checked=lambda item: self.settings["dim_alpha"] == 51),
            pystray.MenuItem("Умеренная (40%)", lambda icon, item: self._set_intensity(102), checked=lambda item: self.settings["dim_alpha"] == 102),
            pystray.MenuItem("Средняя (50%)", lambda icon, item: self._set_intensity(128), checked=lambda item: self.settings["dim_alpha"] == 128),
            pystray.MenuItem("Сильная (60%)", lambda icon, item: self._set_intensity(153), checked=lambda item: self.settings["dim_alpha"] == 153),
            pystray.MenuItem("Максимальная (80%)", lambda icon, item: self._set_intensity(204), checked=lambda item: self.settings["dim_alpha"] == 204),
        )

        return pystray.Menu(
            pystray.MenuItem(lambda item: self.status_text, None, enabled=False),
            pystray.MenuItem("Пауза", self.toggle_pause, checked=lambda item: self._is_paused()),
            pystray.MenuItem("Время ожидания", timeouts_menu),
            pystray.MenuItem("Интенсивность затемнения", intensity_menu),
            pystray.MenuItem("Запускать при старте Windows", self.toggle_startup, checked=lambda item: self._is_startup_enabled()),
            pystray.MenuItem("Выход", self.exit_app),
        )

    @property
    def status_text(self):
        with self._lock:
            return "Статус: На паузе" if self._paused else "Статус: Активен"

    def _set_timeout(self, seconds):
        with self._lock:
            self.settings["idle_threshold_seconds"] = seconds
        self._save_settings()
        logging.info("Idle timeout set to %s seconds.", seconds)
        self.icon.menu = self._create_menu()
        self.icon.update_menu()

    def _set_intensity(self, alpha):
        with self._lock:
            self.settings["dim_alpha"] = alpha
            if self._dimmed:
                self._post_start_fade(alpha)
        self._save_settings()
        logging.info("Dim alpha set to %s.", alpha)
        self.icon.menu = self._create_menu()
        self.icon.update_menu()

    def toggle_pause(self, icon=None, item=None):
        with self._lock:
            self._paused = not self._paused
            paused = self._paused

        if paused:
            self._dimmed = False
            self._post_cancel_fade()

        logging.info("Protection paused: %s.", paused)
        self.icon.icon = self._create_icon_image()
        self.icon.menu = self._create_menu()
        self.icon.update_menu()

    def _is_paused(self):
        with self._lock:
            return self._paused

    def _is_startup_enabled(self):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                try:
                    winreg.QueryValueEx(key, APP_NAME)
                    return True
                except FileNotFoundError:
                    return False
        except OSError:
            return False

    def toggle_startup(self, icon=None, item=None):
        enabled = self._is_startup_enabled()
        self._set_startup(not enabled)
        self.icon.menu = self._create_menu()
        self.icon.update_menu()

    def _set_startup(self, enable):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            if enable:
                command = self._startup_command()
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            logging.info("Startup enabled: %s.", enable)
        except OSError:
            logging.exception("Failed to update startup registry value.")

    def exit_app(self, icon=None, item=None):
        logging.info("OledGuard exiting.")
        self._stop_event.set()
        self._post_cancel_fade()
        self._destroy_overlay()
        if self._mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None
        self.icon.stop()

    def _start_overlay_thread(self):
        self._overlay_hwnd = None
        self._overlay_ready = threading.Event()
        self._overlay_thread = threading.Thread(
            target=self._run_overlay,
            name="OledGuardOverlayThread",
            daemon=True
        )
        self._overlay_thread.start()
        if not self._overlay_ready.wait(OVERLAY_START_TIMEOUT_SECONDS):
            logging.error("Overlay window did not initialize within %s seconds.", OVERLAY_START_TIMEOUT_SECONDS)

    def _run_overlay(self):
        target_fade_alpha = 0
        current_fade_alpha = 0

        def wnd_proc(hwnd, msg, wparam, lparam):
            nonlocal target_fade_alpha, current_fade_alpha
            if msg == win32con.WM_DESTROY:
                self._overlay_available = False
                self._dimmed = False
                win32gui.PostQuitMessage(0)
                return 0
            elif msg == win32con.WM_DISPLAYCHANGE:
                try:
                    x = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
                    y = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
                    cx = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
                    cy = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOPMOST,
                        x, y, cx, cy,
                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
                    )
                except Exception:
                    pass
                return 0
            elif msg == WM_START_FADE_IN:
                target_fade_alpha = wparam
                current_fade_alpha = 0
                win32gui.SetTimer(hwnd, 1, 30, None)
                return 0
            elif msg == WM_CANCEL_FADE:
                try:
                    win32gui.KillTimer(hwnd, 1)
                except Exception:
                    pass
                current_fade_alpha = 0
                try:
                    win32gui.SetLayeredWindowAttributes(hwnd, 0, 0, win32con.LWA_ALPHA)
                except Exception:
                    pass
                return 0
            elif msg == WM_ENSURE_TOPMOST:
                try:
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOPMOST,
                        0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                    )
                except Exception:
                    pass
                return 0
            elif msg == win32con.WM_TIMER:
                if current_fade_alpha < target_fade_alpha:
                    # 15 steps of 30ms -> ~450ms total transition time
                    step = max(5, int(target_fade_alpha / 15))
                    current_fade_alpha = min(target_fade_alpha, current_fade_alpha + step)
                    try:
                        win32gui.SetLayeredWindowAttributes(hwnd, 0, current_fade_alpha, win32con.LWA_ALPHA)
                    except Exception:
                        pass
                else:
                    try:
                        win32gui.KillTimer(hwnd, 1)
                    except Exception:
                        pass
                    # Final safety check to make sure overlay is topmost
                    try:
                        win32gui.SetWindowPos(
                            hwnd,
                            win32con.HWND_TOPMOST,
                            0, 0, 0, 0,
                            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                        )
                    except Exception:
                        pass
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        try:
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = wnd_proc
            wc.lpszClassName = "OledGuardOverlayClass"
            wc.hbrBackground = win32gui.CreateSolidBrush(0)  # solid black brush
            wc.hInstance = win32gui.GetModuleHandle(None)

            try:
                win32gui.RegisterClass(wc)
            except Exception:
                logging.debug("Overlay window class registration skipped or failed.", exc_info=True)

            x = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            y = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
            cx = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            cy = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)

            hwnd = win32gui.CreateWindowEx(
                win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW,
                wc.lpszClassName,
                "OledGuardOverlayWindow",
                win32con.WS_POPUP,
                x, y, cx, cy,
                0, 0, wc.hInstance, None
            )

            win32gui.SetLayeredWindowAttributes(hwnd, 0, 0, win32con.LWA_ALPHA)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

            self._overlay_hwnd = hwnd
            self._overlay_available = True
            self._overlay_ready.set()
            logging.info("Overlay window initialized.")

            win32gui.PumpMessages()
        except Exception:
            logging.exception("Overlay thread failed.")
            self._overlay_ready.set()

    def _destroy_overlay(self):
        if self._overlay_hwnd and win32gui.IsWindow(self._overlay_hwnd):
            try:
                win32gui.PostMessage(self._overlay_hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
            self._overlay_hwnd = None
            self._overlay_available = False
            self._dimmed = False

    def _post_start_fade(self, target_alpha):
        if self._overlay_hwnd and win32gui.IsWindow(self._overlay_hwnd):
            try:
                win32gui.PostMessage(self._overlay_hwnd, WM_START_FADE_IN, target_alpha, 0)
            except Exception:
                pass

    def _post_cancel_fade(self):
        if self._overlay_hwnd and win32gui.IsWindow(self._overlay_hwnd):
            try:
                win32gui.PostMessage(self._overlay_hwnd, WM_CANCEL_FADE, 0, 0)
            except Exception:
                pass

    def _post_ensure_topmost(self):
        if self._overlay_hwnd and win32gui.IsWindow(self._overlay_hwnd):
            try:
                win32gui.PostMessage(self._overlay_hwnd, WM_ENSURE_TOPMOST, 0, 0)
            except Exception:
                pass

    def _monitor_idle(self):
        while not self._stop_event.is_set():
            try:
                if not self._overlay_available:
                    self._stop_event.wait(CHECK_INTERVAL_SECONDS)
                    continue

                if self._is_paused():
                    if self._dimmed:
                        self._post_cancel_fade()
                        self._dimmed = False
                else:
                    idle_seconds = self._get_idle_seconds()

                    with self._lock:
                        threshold = self.settings["idle_threshold_seconds"]
                        target_alpha = self.settings["dim_alpha"]

                    if idle_seconds >= threshold:
                        if not self._dimmed:
                            self._post_start_fade(target_alpha)
                            self._dimmed = True
                        else:
                            self._post_ensure_topmost()
                    else:
                        if self._dimmed:
                            self._post_cancel_fade()
                            self._dimmed = False
            except Exception:
                logging.exception("Idle monitor iteration failed.")

            interval = (
                WAKE_CHECK_INTERVAL_SECONDS
                if self._dimmed
                else CHECK_INTERVAL_SECONDS
            )
            self._stop_event.wait(interval)

    def _get_idle_seconds(self):
        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)

        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
            return 0

        tick_count = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = tick_count - last_input.dwTime
        if elapsed_ms < 0:
            elapsed_ms += 0xFFFFFFFF + 1
        return max(0, elapsed_ms / 1000.0)

    def _startup_command(self):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'

        script_path = os.path.abspath(__file__)
        pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        runner = pythonw_path if os.path.exists(pythonw_path) else sys.executable
        return f'"{runner}" "{script_path}"'

    def _create_icon_image(self):
        paused = self._is_paused()
        bg_color = "#111827"
        badge_color = "#6b7280" if paused else "#22c55e"

        image = Image.new("RGB", (64, 64), bg_color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 12, 52, 52), fill=badge_color)
        draw.rectangle((20, 20, 44, 44), fill="#0f172a")
        return image


if __name__ == "__main__":
    OledGuard().run()
