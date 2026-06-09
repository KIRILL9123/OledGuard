# -*- coding: utf-8 -*-

import ctypes
import os
import sys
import threading
import winreg
from ctypes import wintypes

import pystray
import win32con
import win32gui
from PIL import Image, ImageDraw


APP_NAME = "OledGuard"
CHECK_INTERVAL_SECONDS = 2
WAKE_CHECK_INTERVAL_SECONDS = 0.1
IDLE_THRESHOLD_SECONDS = 10  # Change to 180 for normal 3-minute protection.
DIM_ALPHA = 128  # 50% opacity.
FULL_ALPHA = 255


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
        self._dimmed_hwnd = None
        self._dimmed_original_ex_style = None
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
        self._monitor_thread.start()
        self.icon.run()

    def _create_menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda item: self.status_text, None, enabled=False),
            pystray.MenuItem("Пауза (Toggle)", self.toggle_pause),
            pystray.MenuItem("Добавить в автозапуск", self.add_to_startup),
            pystray.MenuItem("Выход", self.exit_app),
        )

    @property
    def status_text(self):
        with self._lock:
            return "Статус: На паузе" if self._paused else "Статус: Активен"

    def toggle_pause(self, icon=None, item=None):
        with self._lock:
            self._paused = not self._paused
            paused = self._paused

        if paused:
            self._restore_dimmed_window()
        self.icon.update_menu()

    def add_to_startup(self, icon=None, item=None):
        try:
            command = self._startup_command()
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        except OSError:
            pass

    def exit_app(self, icon=None, item=None):
        self._stop_event.set()
        self._restore_dimmed_window()
        self.icon.stop()

    def _monitor_idle(self):
        while not self._stop_event.is_set():
            try:
                if self._is_paused():
                    self._restore_dimmed_window()
                else:
                    idle_seconds = self._get_idle_seconds()
                    if idle_seconds >= IDLE_THRESHOLD_SECONDS:
                        self._dim_foreground_window()
                    else:
                        self._restore_dimmed_window()
            except Exception:
                # Background WinAPI failures should never terminate the tray app.
                pass

            interval = (
                WAKE_CHECK_INTERVAL_SECONDS
                if self._has_dimmed_window()
                else CHECK_INTERVAL_SECONDS
            )
            self._stop_event.wait(interval)

    def _is_paused(self):
        with self._lock:
            return self._paused

    def _has_dimmed_window(self):
        with self._lock:
            return self._dimmed_hwnd is not None

    def _get_idle_seconds(self):
        last_input = LASTINPUTINFO()
        last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)

        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):
            return 0

        tick_count = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = tick_count - last_input.dwTime
        return max(0, elapsed_ms / 1000.0)

    def _dim_foreground_window(self):
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return

        with self._lock:
            if self._dimmed_hwnd == hwnd:
                return

        self._restore_dimmed_window()

        try:
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            layered_style = ex_style | win32con.WS_EX_LAYERED
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, layered_style)
            win32gui.SetLayeredWindowAttributes(hwnd, 0, DIM_ALPHA, win32con.LWA_ALPHA)
        except Exception:
            return

        with self._lock:
            self._dimmed_hwnd = hwnd
            self._dimmed_original_ex_style = ex_style

    def _restore_dimmed_window(self):
        with self._lock:
            hwnd = self._dimmed_hwnd
            original_ex_style = self._dimmed_original_ex_style
            self._dimmed_hwnd = None
            self._dimmed_original_ex_style = None

        if not hwnd:
            return

        try:
            if win32gui.IsWindow(hwnd):
                win32gui.SetLayeredWindowAttributes(
                    hwnd,
                    0,
                    FULL_ALPHA,
                    win32con.LWA_ALPHA,
                )
                if original_ex_style is not None:
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, original_ex_style)
        except Exception:
            pass

    def _startup_command(self):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'

        script_path = os.path.abspath(__file__)
        pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        runner = pythonw_path if os.path.exists(pythonw_path) else sys.executable
        return f'"{runner}" "{script_path}"'

    @staticmethod
    def _create_icon_image():
        image = Image.new("RGB", (64, 64), "#111827")
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 12, 52, 52), fill="#22c55e")
        draw.rectangle((20, 20, 44, 44), fill="#0f172a")
        return image


if __name__ == "__main__":
    OledGuard().run()
