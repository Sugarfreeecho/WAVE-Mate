# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import argparse
import os
import socket
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

import win32api
import win32con
import win32event
import win32gui
import winerror
from PIL import Image, ImageDraw, ImageFont


APP_NAME = "Agent \u667a\u80fd\u4f1a\u8bdd\u52a9\u624b"
MSG_STARTING = "\u6b63\u5728\u542f\u52a8 Agent\uff0c\u8bf7\u7a0d\u5019..."
MSG_LOG = "\u7ec8\u7aef\u65e5\u5fd7"
MSG_RUNNING = "Agent \u6258\u76d8\u542f\u52a8\u5668\u5df2\u5728\u8fd0\u884c\uff0c\u6b63\u5728\u6253\u5f00 WebUI..."
MSG_PRESS_ENTER = "\u6309\u56de\u8f66\u9000\u51fa..."
MSG_DETECTED = "\u68c0\u6d4b\u5230 Agent \u5df2\u5728\u8fd0\u884c\uff0c\u63a5\u7ba1\u6258\u76d8\u83dc\u5355\u3002"
MSG_FAILED = "Agent \u542f\u52a8\u5931\u8d25\uff0c\u9000\u51fa\u7801"
MSG_CHECK_LOG = "\u8bf7\u67e5\u770b\u65e5\u5fd7"
MSG_READY = "Agent \u5df2\u542f\u52a8\uff0c\u6b63\u5728\u6253\u5f00 WebUI \u5e76\u6536\u8d77\u7ec8\u7aef\u7a97\u53e3..."
MSG_LOADING = "\u52a0\u8f7d\u4e2d"
MSG_TIMEOUT = "\u7b49\u5f85 Agent \u542f\u52a8\u8d85\u65f6\uff0c\u8bf7\u67e5\u770b\u65e5\u5fd7"
MSG_NOT_READY = "Agent \u5c1a\u672a\u5c31\u7eea\uff0c\u8bf7\u7a0d\u5019\u3002"
MSG_EMPTY_LOG = "Agent \u7ec8\u7aef\u65e5\u5fd7\u5c1a\u672a\u751f\u6210\u3002\n"
TITLE_TERMINAL = "Agent \u7ec8\u7aef\u4fe1\u606f"

MENU_TEXT_WEBUI = "\u6253\u5f00WebUI"
MENU_TEXT_ENV = "\u6253\u5f00\u9ad8\u7ea7\u8bbe\u7f6e"
MENU_TEXT_MCP = "\u6253\u5f00MCP\u914d\u7f6e"
MENU_TEXT_TERMINAL = "\u67e5\u770b\u7ec8\u7aef\u4fe1\u606f"
MENU_TEXT_EXIT = "\u9000\u51faAgent"

HOST = "127.0.0.1"
PORT = 8192
BASE_URL = f"http://{HOST}:{PORT}"
WM_TRAY = win32con.WM_USER + 20

MENU_OPEN_WEBUI = 1001
MENU_OPEN_ENV = 1002
MENU_OPEN_MCP = 1003
MENU_VIEW_TERMINAL = 1004
MENU_EXIT = 1005

SW_HIDE = 0
SW_SHOW = 5
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6

ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = ROOT / "python" / "python.exe"
MAIN_PY = ROOT / "app" / "main.py"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "agent_terminal.log"
PYTHONW_EXE = ROOT / "python" / "pythonw.exe"
COLORED_LOG_VIEWER = ROOT / "app" / "colored_log_viewer.ps1"


def _append_log(line: str = "") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8", buffering=1) as f:
        f.write(line + "\n")


def _reset_log() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")


def _is_port_listening() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.4):
            return True
    except OSError:
        return False


def _open_url_in_browser(path: str = "/", refresh: bool = True) -> None:
    url = f"{BASE_URL}{path}"
    if refresh:
        url = f"{url}{'&' if '?' in url else '?'}_={int(time.time())}"
    try:
        os.startfile(url)
    except OSError:
        webbrowser.open(url, new=0, autoraise=True)


def run_starter() -> int:
    print("=" * 50)
    print(f"              {APP_NAME}")
    print("=" * 50)
    print(MSG_STARTING)
    print(f"{MSG_LOG}: {LOG_FILE}")

    if not PYTHON_EXE.exists():
        print(f"Missing: {PYTHON_EXE}")
        input(MSG_PRESS_ENTER)
        return 1
    if not MAIN_PY.exists():
        print(f"Missing: {MAIN_PY}")
        input(MSG_PRESS_ENTER)
        return 1

    if _is_port_listening():
        print(MSG_RUNNING)
        _open_url_in_browser("/", refresh=True)
        return 0

    _reset_log()
    _append_log("=" * 50)
    _append_log(f"              {APP_NAME}")
    _append_log("=" * 50)
    _append_log(MSG_STARTING)
    _append_log(f"{MSG_LOG}: {LOG_FILE}")

    daemon_python = PYTHONW_EXE if PYTHONW_EXE.exists() else PYTHON_EXE
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.Popen(
        [str(daemon_python), str(Path(__file__).resolve()), "--daemon"],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    )

    deadline = time.monotonic() + 120
    dots = 0
    while time.monotonic() < deadline:
        if _is_port_listening():
            print(f"\n{MSG_READY}")
            _append_log(MSG_READY)
            _open_url_in_browser("/", refresh=True)
            return 0
        print("." if dots else MSG_LOADING, end="", flush=True)
        dots = (dots + 1) % 24
        time.sleep(0.5)

    print(f"\n{MSG_TIMEOUT}: {LOG_FILE}")
    _append_log(f"{MSG_TIMEOUT}: {LOG_FILE}")
    input(MSG_PRESS_ENTER)
    return 1


class TrayLauncher:
    def __init__(self) -> None:
        self.hwnd = None
        self.hicon = None
        self.proc = None
        self.exiting = False
        self.mutex = win32event.CreateMutex(None, True, "MyAgentTrayLauncher")
        self.already_running = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
        self.console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        self._ctrl_handler = self._make_ctrl_handler()
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl_handler, True)

    def run(self) -> int:
        if self.already_running:
            _append_log(MSG_RUNNING)
            self._open_url("/", refresh=True)
            return 0
        if not self._check_files():
            return 1

        self._create_window()
        self._add_tray_icon()
        if self._is_listening():
            _append_log(MSG_DETECTED)
        else:
            self._start_agent()
        self._watch_startup()
        win32gui.PumpMessages()
        return 0

    def _print_banner(self) -> None:
        print("=" * 50)
        print(f"              {APP_NAME}")
        print("=" * 50)
        print(MSG_STARTING)
        print(f"{MSG_LOG}: {LOG_FILE}")

    def _check_files(self) -> bool:
        ok = True
        if not PYTHON_EXE.exists():
            print(f"Missing: {PYTHON_EXE}")
            ok = False
        if not MAIN_PY.exists():
            print(f"Missing: {MAIN_PY}")
            ok = False
        return ok

    def _create_window(self) -> None:
        message_map = {
            WM_TRAY: self._on_tray,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_DESTROY: self._on_destroy,
        }
        wnd_class = win32gui.WNDCLASS()
        wnd_class.hInstance = win32api.GetModuleHandle(None)
        wnd_class.lpszClassName = "MyAgentTrayLauncherWindow"
        wnd_class.lpfnWndProc = message_map
        try:
            win32gui.RegisterClass(wnd_class)
        except win32gui.error:
            pass
        self.hwnd = win32gui.CreateWindow(
            wnd_class.lpszClassName,
            APP_NAME,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            wnd_class.hInstance,
            None,
        )

    def _add_tray_icon(self) -> None:
        self.hicon = self._create_icon()
        nid = (
            self.hwnd,
            0,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            WM_TRAY,
            self.hicon,
            APP_NAME,
        )
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _create_icon(self) -> int:
        icon_path = Path(tempfile.gettempdir()) / "myagent_sugar_tray_v8.ico"
        if not icon_path.exists():
            image = self._create_sugar_icon_image(256)
            sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (256, 256)]
            image.save(icon_path, sizes=sizes)
        return win32gui.LoadImage(
            0,
            str(icon_path),
            win32con.IMAGE_ICON,
            0,
            0,
            win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
        )

    def _create_sugar_icon_image(self, size: int) -> Image.Image:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        margin = int(size * 0.082)
        radius = int(size * 0.18)
        rect = (margin, margin, size - margin, size - margin)

        # Match the WebUI brand gradient: #6366f1 -> #8b5cf6 -> #a78bfa.
        top = (99, 102, 241)
        mid = (139, 92, 246)
        bottom = (167, 139, 250)
        grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(grad)
        for y in range(size):
            t = y / max(1, size - 1)
            if t < 0.5:
                k = t / 0.5
                c = tuple(int(top[i] * (1 - k) + mid[i] * k) for i in range(3))
            else:
                k = (t - 0.5) / 0.5
                c = tuple(int(mid[i] * (1 - k) + bottom[i] * k) for i in range(3))
            gdraw.line((0, y, size, y), fill=(*c, 255))

        mask = Image.new("L", (size, size), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle(rect, radius=radius, fill=255)
        image.alpha_composite(Image.composite(grad, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))

        shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.rounded_rectangle(rect, radius=radius, outline=(255, 255, 255, 54), width=max(2, size // 48))
        image.alpha_composite(shadow)

        font = self._load_sugar_font(size)
        text = "Sugar"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (size - tw) / 2 - bbox[0]
        x = x + size * 0.01
        y = (size - th) / 2 - bbox[1] + size * 0.015
        pad = int(size * 0.35)
        canvas_size = size + pad * 2
        txt = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(txt)
        tx = x + pad
        ty = y + pad
        tdraw.text((tx + size * 0.012, ty + size * 0.014), text, font=font, fill=(58, 42, 120, 82))
        stroke = max(1, size // 96)
        for dx in range(-stroke, stroke + 1):
            for dy in range(-stroke, stroke + 1):
                if dx * dx + dy * dy <= stroke * stroke:
                    tdraw.text((tx + dx, ty + dy), text, font=font, fill=(255, 255, 255, 248))
        txt = txt.rotate(6, resample=Image.Resampling.BICUBIC, center=(canvas_size / 2, canvas_size / 2))
        image.alpha_composite(txt, dest=(-pad, -pad))
        return image

    def _load_sugar_font(self, size: int) -> ImageFont.ImageFont:
        font_size = max(16, int(size * 0.342))
        candidates = [
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoesc.ttf",
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoescb.ttf",
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguisbi.ttf",
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "ariali.ttf",
        ]
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), font_size)
                except OSError:
                    pass
        return ImageFont.load_default()

    def _start_agent(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG_FILE.open("a", encoding="utf-8", buffering=1)
        log.write("\n" + "=" * 80 + "\n")
        log.write(time.strftime("Agent started by tray launcher at %Y-%m-%d %H:%M:%S\n"))
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["OPEN_BROWSER"] = "0"
        self.proc = subprocess.Popen(
            [str(PYTHON_EXE), str(MAIN_PY)],
            cwd=str(ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )

    def _watch_startup(self) -> None:
        deadline = time.monotonic() + 120
        dots = 0
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                _append_log(f"{MSG_FAILED}: {self.proc.returncode}")
                _append_log(f"{MSG_CHECK_LOG}: {LOG_FILE}")
                return
            if self._is_listening():
                _append_log(MSG_READY)
                return
            dots = (dots + 1) % 24
            time.sleep(0.5)
        _append_log(f"{MSG_TIMEOUT}: {LOG_FILE}")

    def _is_listening(self) -> bool:
        return _is_port_listening()

    def _on_tray(self, hwnd, msg, wparam, lparam):
        try:
            if lparam == win32con.WM_LBUTTONDBLCLK:
                self._open_url("/", refresh=True)
            elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
                self._show_menu()
        except Exception as exc:
            print(f"Tray handler error: {exc}")
        return True

    def _show_menu(self) -> None:
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_OPEN_WEBUI, MENU_TEXT_WEBUI)
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_OPEN_ENV, MENU_TEXT_ENV)
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_OPEN_MCP, MENU_TEXT_MCP)
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_VIEW_TERMINAL, MENU_TEXT_TERMINAL)
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, MENU_EXIT, MENU_TEXT_EXIT)
        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    def _on_command(self, hwnd, msg, wparam, lparam):
        command = win32api.LOWORD(wparam)
        if command == MENU_OPEN_WEBUI:
            self._open_url("/", refresh=True)
        elif command == MENU_OPEN_ENV:
            self._open_url("/setup/env", refresh=True)
        elif command == MENU_OPEN_MCP:
            self._open_url("/setup/mcp", refresh=True)
        elif command == MENU_VIEW_TERMINAL:
            self._open_terminal_viewer()
        elif command == MENU_EXIT:
            self._exit_agent()
        return True

    def _open_url(self, path: str, refresh: bool = False) -> None:
        if not self._is_listening():
            self._show_console()
            print(MSG_NOT_READY)
            return
        url = f"{BASE_URL}{path}"
        if refresh:
            url = f"{url}{'&' if '?' in url else '?'}_={int(time.time())}"
        self._open_named_browser_window(url)

    def _open_named_browser_window(self, url: str) -> None:
        _open_url_in_browser(url.replace(BASE_URL, "", 1) or "/", refresh=False)

    def _open_terminal_viewer(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not LOG_FILE.exists():
            LOG_FILE.write_text(MSG_EMPTY_LOG, encoding="utf-8")
        if COLORED_LOG_VIEWER.exists():
            args = [
                "-NoExit",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(COLORED_LOG_VIEWER),
                "-Path",
                str(LOG_FILE),
            ]
        else:
            log_path = str(LOG_FILE).replace("'", "''")
            ps = (
                "chcp 65001 > $null; "
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
                f"$Host.UI.RawUI.WindowTitle='{TITLE_TERMINAL}'; "
                f"Get-Content -LiteralPath '{log_path}' -Encoding UTF8 -Wait"
            )
            args = ["-NoExit", "-ExecutionPolicy", "Bypass", "-Command", ps]
        subprocess.Popen(
            ["powershell.exe", *args],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    def _hide_console(self) -> None:
        if self.console_hwnd:
            ctypes.windll.user32.ShowWindow(self.console_hwnd, SW_HIDE)

    def _show_console(self) -> None:
        if self.console_hwnd:
            ctypes.windll.user32.ShowWindow(self.console_hwnd, SW_SHOW)
            ctypes.windll.user32.SetForegroundWindow(self.console_hwnd)

    def _exit_agent(self) -> None:
        self.exiting = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.send_signal(CTRL_BREAK_EVENT)
                self.proc.wait(timeout=6)
            except Exception:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        else:
            self._stop_process_on_port()
        win32gui.DestroyWindow(self.hwnd)

    def _stop_process_on_port(self) -> None:
        try:
            output = subprocess.check_output(
                ["netstat", "-ano", "-p", "tcp"],
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            print(f"Unable to inspect port {PORT}: {exc}")
            return

        pids = set()
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local_addr = parts[1]
            state = parts[3].upper()
            pid = parts[4]
            if state == "LISTENING" and local_addr.endswith(f":{PORT}") and pid.isdigit():
                pids.add(pid)

        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as exc:
                print(f"Unable to stop PID {pid}: {exc}")

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except win32gui.error:
            pass
        if self.hicon:
            win32gui.DestroyIcon(self.hicon)
        win32gui.PostQuitMessage(0)
        return True

    def _make_ctrl_handler(self):
        def handler(ctrl_type):
            if ctrl_type in (
                CTRL_C_EVENT,
                CTRL_BREAK_EVENT,
                CTRL_CLOSE_EVENT,
                CTRL_LOGOFF_EVENT,
                CTRL_SHUTDOWN_EVENT,
            ):
                if not self.exiting:
                    self._hide_console()
                    return True
            return False

        return ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--daemon", action="store_true")
    args, _ = parser.parse_known_args()
    if args.daemon:
        raise SystemExit(TrayLauncher().run())
    raise SystemExit(run_starter())
