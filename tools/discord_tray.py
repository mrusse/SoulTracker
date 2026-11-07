# /// script
# requires-python = ">=3.9"
# dependencies = ["pypresence>=4.6", "pystray>=0.19", "pillow>=10"]
# ///
"""SoulTracker Discord status, as a Windows tray app (built into SoulTracker-Discord.exe).

Lives only in the tray. Right-click it to:
  - set the Discord app ID / SoulTracker address / password from the clipboard (saved to
    %APPDATA%\\SoulTracker\\discord.json)
  - turn "Launch with Windows" on or off
  - open SoulTracker, or quit
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pystray
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discord_presence import PresenceUpdater  # noqa: E402

APP_NAME = "SoulTracker Discord"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "SoulTracker"
CONFIG_FILE = CONFIG_DIR / "discord.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INTERVAL = 5

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(CONFIG_DIR / "discord.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger = logging.getLogger("soultracker-discord")
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ---------- settings ----------

def load_config() -> dict:
    try:
        return {"url": "http://localhost:8484", **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"url": "http://localhost:8484"}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------- Windows bits (ctypes / winreg, no extra dependencies) ----------

def clipboard_text() -> str | None:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr).strip() if ptr else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def launch_command() -> str:
    if getattr(sys, "frozen", False):  # the PyInstaller exe
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else sys.executable}" "{Path(__file__).resolve()}"'


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def taskbar_is_light() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def already_running() -> bool:
    """One copy at a time (e.g. started by Windows and again by hand)."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    already_running.handle = kernel32.CreateMutexW(None, False, "Local\\SoulTrackerDiscordTray")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "assets"))
    return base / name


# ---------- the tray app ----------

class TrayApp:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.updater = self._new_updater()
        logo = "logo_dark.png" if taskbar_is_light() else "logo_light.png"
        self.icon = pystray.Icon("soultracker-discord", Image.open(resource(logo)), "SoulTracker", self._menu())

    def _new_updater(self) -> PresenceUpdater:
        return PresenceUpdater(self.cfg.get("url", "http://localhost:8484"), self.cfg.get("app_id"),
                               self.cfg.get("password"), on_log=logger.info)

    def _restart_updater(self) -> None:
        with self.lock:
            self.updater.close()
            self.updater = self._new_updater()
        self.icon.update_menu()

    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: self.updater.status, None, enabled=False),
            item("Open SoulTracker", lambda: webbrowser.open(self.cfg.get("url", "http://localhost:8484"))),
            pystray.Menu.SEPARATOR,
            item("Set Discord app ID from clipboard", self.set_app_id),
            item("Set SoulTracker address from clipboard", self.set_url),
            item(lambda _: "Clear password" if self.cfg.get("password") else "Set password from clipboard", self.set_password),
            item("Launch with Windows", self.toggle_autostart, checked=lambda _: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            item("Quit", self.quit),
        )

    def notify(self, msg: str) -> None:
        logger.info(msg)
        try:
            self.icon.notify(msg, "SoulTracker")
        except Exception:
            pass

    def set_app_id(self) -> None:
        clip = clipboard_text() or ""
        if not re.fullmatch(r"\d{15,22}", clip):
            self.notify("The clipboard doesn't contain a Discord application ID (copy it from the Developer Portal first).")
            return
        self.cfg["app_id"] = clip
        save_config(self.cfg)
        self._restart_updater()
        self.notify(f"Discord app ID saved: {clip}")

    def set_url(self) -> None:
        clip = (clipboard_text() or "").strip().rstrip("/")
        if clip and not re.match(r"https?://", clip):
            clip = "http://" + clip
        if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", clip):
            self.notify("The clipboard doesn't contain a SoulTracker address, e.g. http://192.168.1.10:8484")
            return
        self.cfg["url"] = clip
        save_config(self.cfg)
        self._restart_updater()
        self.notify(f"SoulTracker address saved: {clip}")

    def set_password(self) -> None:
        if self.cfg.get("password"):
            self.cfg.pop("password")
            msg = "Password cleared"
        else:
            clip = clipboard_text()
            if not clip:
                self.notify("The clipboard is empty: copy your SoulTracker password first.")
                return
            self.cfg["password"] = clip
            msg = "Password saved"
        save_config(self.cfg)
        self._restart_updater()
        self.notify(msg)

    def toggle_autostart(self) -> None:
        set_autostart(not autostart_enabled())
        self.icon.update_menu()

    def quit(self) -> None:
        self.stop.set()
        with self.lock:
            self.updater.close()
        self.icon.stop()

    def worker(self) -> None:
        shown = None
        while not self.stop.is_set():
            with self.lock:
                try:
                    self.updater.step()
                except Exception:
                    logger.exception("update failed")
                status = self.updater.status
            if status != shown:  # refresh the menu's status line and the hover text
                shown = status
                self.icon.title = f"SoulTracker – {status}"[:127]
                self.icon.update_menu()
            self.stop.wait(INTERVAL)

    def run(self) -> None:
        def setup(icon: pystray.Icon) -> None:
            icon.visible = True
            if not self.cfg.get("app_id"):
                self.notify("Right-click the tray icon to set your Discord app ID and SoulTracker address.")
            threading.Thread(target=self.worker, daemon=True).start()
        self.icon.run(setup)


def main() -> None:
    if already_running():
        return
    logger.info("started")
    TrayApp().run()


if __name__ == "__main__":
    main()
