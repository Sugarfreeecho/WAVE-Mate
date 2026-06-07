"""本机文件/目录选择对话框，供 Web UI 配置项与聊天输入使用。"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Callable, Literal, Optional, Tuple

PathPickKind = Literal["file", "directory"]

HRESULT_CANCELLED = 0x800704C7


def _hresult_unsigned(hr: int) -> int:
    return int(hr) & 0xFFFFFFFF


def _hresult_succeeded(hr: int) -> bool:
    return _hresult_unsigned(hr) < 0x80000000


def _is_cancelled_hr(hr: int) -> bool:
    return _hresult_unsigned(hr) == HRESULT_CANCELLED


def _is_user_cancelled_exc(exc: BaseException) -> bool:
    if isinstance(exc, OSError):
        for arg in exc.args:
            if isinstance(arg, int) and _is_cancelled_hr(arg):
                return True
    text = str(exc).lower()
    return "800704c7" in text or "2147023673" in text or "操作已被用户取消" in str(exc)

_hidden_owner_hwnd: Optional[int] = None
_wndproc_ref = None  # 防止回调被 GC


def _initial_dir(initial: str) -> Optional[str]:
    raw = (initial or "").strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        p = Path(raw).expanduser()
        if p.is_file():
            return str(p.parent.resolve())
        if p.is_dir():
            return str(p.resolve())
        parent = p.parent
        if parent.is_dir():
            return str(parent.resolve())
    except OSError:
        pass
    return None


def _is_windows_network_path(path: str) -> bool:
    s = str(path or "").strip().replace("/", "\\")
    if s.startswith("\\\\"):
        return True
    if len(s) >= 2 and s[1] == ":" and platform.system() == "Windows":
        try:
            import ctypes

            root = s[:3] if len(s) >= 3 and s[2] == "\\" else s[:2] + "\\"
            return ctypes.windll.kernel32.GetDriveTypeW(root) == 4  # DRIVE_REMOTE
        except Exception:
            return False
    return False


def _default_local_initial_dir() -> str:
    candidates = [
        os.getenv("WORK_DIR", ""),
        str(Path(__file__).resolve().parent.parent / "workspace"),
        os.path.expanduser("~"),
        os.getenv("USERPROFILE", ""),
        os.getcwd(),
    ]
    for raw in candidates:
        if not raw:
            continue
        try:
            p = Path(raw).expanduser()
            if p.is_file():
                p = p.parent
            if p.is_dir():
                resolved = str(p.resolve())
                if platform.system() == "Windows" and _is_windows_network_path(resolved):
                    continue
                return resolved
        except OSError:
            continue
    return os.path.abspath(os.sep)


def _safe_initial_dir(initial: str) -> str:
    init = _initial_dir(initial)
    if init and not (platform.system() == "Windows" and _is_windows_network_path(init)):
        return init
    return _default_local_initial_dir()


def _norm_result(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    s = str(path).strip()
    if not s:
        return None
    return os.path.normpath(s)


def _dialog_owner_hwnd() -> int:
    """对话框父窗口：优先当前前台窗口，否则用不显示在任务栏的隐藏宿主。"""
    global _hidden_owner_hwnd, _wndproc_ref
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    if fg:
        return int(fg)

    if _hidden_owner_hwnd and user32.IsWindow(_hidden_owner_hwnd):
        return _hidden_owner_hwnd

    WndProcType = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
    )

    def _wndproc(hwnd, msg, wparam, lparam):
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _wndproc_ref = WndProcType(_wndproc)
    wndproc = _wndproc_ref

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wt.UINT),
            ("lpfnWndProc", WndProcType),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HANDLE),
            ("hCursor", wt.HANDLE),
            ("hbrBackground", wt.HANDLE),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
        ]

    wc = WNDCLASSW()
    wc.lpfnWndProc = wndproc
    wc.lpszClassName = "MyAgentPathPickerOwner"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        pass

    WS_EX_TOOLWINDOW = 0x00000080
    hwnd = user32.CreateWindowExW(
        WS_EX_TOOLWINDOW,
        "MyAgentPathPickerOwner",
        "",
        0,
        0,
        0,
        0,
        0,
        None,
        None,
        None,
        None,
    )
    if not hwnd:
        return 0
    _hidden_owner_hwnd = int(hwnd)
    return _hidden_owner_hwnd


def _pick_windows_ifiledialog(kind: PathPickKind, initial: str) -> Optional[str]:
    path, cancelled = _pick_windows_ifiledialog_impl(kind, initial)
    if cancelled:
        return None
    if not path:
        raise RuntimeError("Windows IFileDialog 未返回有效路径")
    return path


def _pick_windows_ifiledialog_impl(
    kind: PathPickKind, initial: str
) -> Tuple[Optional[str], bool]:
    """返回 (路径, 是否用户取消)。"""
    import ctypes
    import ctypes.wintypes as wt
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wt.DWORD),
            ("Data2", wt.WORD),
            ("Data3", wt.WORD),
            ("Data4", wt.BYTE * 8),
        ]

        @classmethod
        def from_str(cls, s: str) -> "GUID":
            u = uuid.UUID(s)
            g = cls()
            g.Data1 = u.time_low
            g.Data2 = u.fields[1]
            g.Data3 = u.fields[2]
            g.Data4 = (wt.BYTE * 8).from_buffer_copy(u.bytes[8:])
            return g

    CLSID_FileOpenDialog = GUID.from_str("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")
    IID_IFileOpenDialog = GUID.from_str("d57c7288-d4ad-4768-be02-9d969532d960")
    IID_IShellItem = GUID.from_str("43826d1e-e718-42ee-bc55-a1e261c37bfe")

    FOS_PICKFOLDERS = 0x20
    FOS_FORCEFILESYSTEM = 0x40
    FOS_PATHMUSTEXIST = 0x800
    FOS_FILEMUSTEXIST = 0x1000
    SIGDN_FILESYSPATH = 0x80058000

    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.OleDLL("shell32")

    def _shell_item_path(item: ctypes.c_void_p) -> Optional[str]:
        if not item:
            return None
        item_vtbl = ctypes.cast(
            item, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        get_display_name = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.LPWSTR)
        )(item_vtbl[5])
        psz = wt.LPWSTR()
        hr = get_display_name(item, SIGDN_FILESYSPATH, ctypes.byref(psz))
        if hr < 0:
            return None
        try:
            return _norm_result(psz.value)
        finally:
            ole32.CoTaskMemFree(psz)

    ole32.CoInitializeEx(None, 0x2)
    dialog = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(CLSID_FileOpenDialog),
        None,
        1,
        ctypes.byref(IID_IFileOpenDialog),
        ctypes.byref(dialog),
    )
    if hr < 0:
        ole32.CoUninitialize()
        raise OSError(f"CoCreateInstance 失败: {hr:#010x}")

    try:
        vtbl = ctypes.cast(dialog, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        def _fn(idx: int, restype, *argtypes):
            proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
            return proto(vtbl[idx])

        set_options = _fn(9, ctypes.HRESULT, wt.DWORD)
        set_folder = _fn(12, ctypes.HRESULT, ctypes.c_void_p)
        get_folder = _fn(13, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))
        set_title = _fn(17, ctypes.HRESULT, wt.LPCWSTR)
        show = _fn(3, ctypes.HRESULT, wt.HWND)
        get_result = _fn(20, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))

        options = FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
        dlg_title = "选择文件夹" if kind == "directory" else "选择文件"
        if kind == "directory":
            options |= FOS_PICKFOLDERS
        else:
            options |= FOS_FILEMUSTEXIST

        hr = set_options(dialog, options)
        if hr < 0:
            raise OSError(f"SetOptions 失败: {hr:#010x}")

        set_title(dialog, dlg_title)

        init_dir = _safe_initial_dir(initial)
        folder_item = ctypes.c_void_p()
        hr = shell32.SHCreateItemFromParsingName(
            wt.LPCWSTR(init_dir),
            None,
            ctypes.byref(IID_IShellItem),
            ctypes.byref(folder_item),
        )
        if hr >= 0 and folder_item:
            set_folder(dialog, folder_item)

        owner = _dialog_owner_hwnd()

        hr = show(dialog, owner or None)
        if _is_cancelled_hr(hr):
            return None, True
        if not _hresult_succeeded(hr):
            raise OSError(hr, f"Show 失败: {_hresult_unsigned(hr):#010x}")

        if kind == "directory":
            folder_item = ctypes.c_void_p()
            hr = get_folder(dialog, ctypes.byref(folder_item))
            if _hresult_succeeded(hr) and folder_item:
                path = _shell_item_path(folder_item)
                if path:
                    return path, False
            result = ctypes.c_void_p()
            hr = get_result(dialog, ctypes.byref(result))
            if _hresult_succeeded(hr) and result:
                path = _shell_item_path(result)
                if path:
                    return path, False
            return None, False

        result = ctypes.c_void_p()
        hr = get_result(dialog, ctypes.byref(result))
        if _hresult_succeeded(hr) and result:
            path = _shell_item_path(result)
            if path:
                return path, False
        return None, False
    finally:
        ole32.CoUninitialize()


def _pick_tkinter(kind: PathPickKind, initial: str) -> Optional[str]:
    import tkinter as tk
    from tkinter import filedialog

    init_dir = _safe_initial_dir(initial)
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        if kind == "directory":
            chosen = filedialog.askdirectory(
                initialdir=init_dir,
                mustexist=False,
                parent=root,
            )
        else:
            chosen = filedialog.askopenfilename(
                initialdir=init_dir,
                parent=root,
            )
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return _norm_result(chosen)


def _pick_tkinter_multi_files(initial: str) -> list[str]:
    import tkinter as tk
    from tkinter import filedialog

    init_dir = _safe_initial_dir(initial)
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        chosen = filedialog.askopenfilenames(
            initialdir=init_dir,
            parent=root,
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    out: list[str] = []
    for item in chosen or []:
        p = _norm_result(item)
        if p:
            out.append(p)
    return out


def _ps_quote(s: str) -> str:
    return "'" + str(s or "").replace("'", "''") + "'"


def _pick_windows_powershell(
    kind: PathPickKind, initial: str, multiple: bool = False
) -> Optional[str] | list[str]:
    init_dir = _safe_initial_dir(initial)
    if multiple and kind != "file":
        raise ValueError("multiple=true 仅支持 kind=file")
    if kind == "directory":
        script = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "$d.Description='选择文件夹';"
            "$d.ShowNewFolderButton=$true;"
            f"$init={_ps_quote(init_dir)};"
            "if($init -and [System.IO.Directory]::Exists($init)){$d.SelectedPath=$init};"
            "$r=$d.ShowDialog();"
            "if($r -eq [System.Windows.Forms.DialogResult]::OK){[Console]::WriteLine($d.SelectedPath)}"
        )
    else:
        script = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.OpenFileDialog;"
            "$d.Title='选择文件';"
            "$d.CheckFileExists=$true;"
            f"$d.Multiselect={'$true' if multiple else '$false'};"
            f"$init={_ps_quote(init_dir)};"
            "if($init -and [System.IO.Directory]::Exists($init)){$d.InitialDirectory=$init};"
            "$r=$d.ShowDialog();"
            "if($r -eq [System.Windows.Forms.DialogResult]::OK){"
            "if($d.Multiselect){$d.FileNames | ForEach-Object {[Console]::WriteLine($_)}}"
            "else{[Console]::WriteLine($d.FileName)}}"
        )
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=float(os.getenv("MYAGENT_PATH_PICKER_TIMEOUT", "45")),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "PowerShell 文件选择器失败").strip()
        raise RuntimeError(err)
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if multiple:
        return [_norm_result(line) for line in lines if _norm_result(line)]
    return _norm_result(lines[0]) if lines else None


def _pick_zenity(kind: PathPickKind, initial: str) -> Optional[str]:
    init = _initial_dir(initial)
    cmd = ["zenity"]
    if kind == "directory":
        cmd.append("--file-selection")
        cmd.append("--directory")
    else:
        cmd.append("--file-selection")
    if init:
        cmd.append(f"--filename={init}{os.sep}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "zenity 失败").strip())
    return _norm_result((proc.stdout or "").strip())


def _pick_macos(kind: PathPickKind, initial: str) -> Optional[str]:
    if kind == "directory":
        script = 'POSIX path of (choose folder with prompt "选择文件夹")'
    else:
        script = 'POSIX path of (choose file with prompt "选择文件")'
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "User canceled" in err or proc.returncode == 1:
            return None
        raise RuntimeError(err or "osascript 失败")
    return _norm_result((proc.stdout or "").strip())


def _backends() -> list[tuple[str, Callable[[PathPickKind, str], Optional[str]]]]:
    sysname = platform.system()
    order: list[tuple[str, Callable[[PathPickKind, str], Optional[str]]]] = []
    if sysname == "Windows":
        order.append(("windows-powershell", lambda kind, initial: _pick_windows_powershell(kind, initial, False)))  # type: ignore[arg-type]
        order.append(("tkinter", _pick_tkinter))
        if os.getenv("MYAGENT_PATH_PICKER_USE_IFILEDIALOG", "").lower() in ("1", "true", "yes"):
            order.append(("windows-ifiledialog", _pick_windows_ifiledialog))
    else:
        order.append(("tkinter", _pick_tkinter))
        if sysname == "Darwin":
            order.append(("macos-osascript", _pick_macos))
        else:
            order.append(("zenity", _pick_zenity))
    return order


def pick_native_path(
    kind: PathPickKind, initial: str = "", multiple: bool = False
) -> Optional[str] | list[str]:
    """弹出系统文件/目录选择框。用户取消时返回 None。"""
    if kind not in ("file", "directory"):
        raise ValueError("kind 须为 file 或 directory")
    if multiple and kind != "file":
        raise ValueError("multiple=true 仅支持 kind=file")
    if multiple:
        failures: list[str] = []
        if platform.system() == "Windows":
            try:
                result = _pick_windows_powershell("file", initial, True)
                return result if isinstance(result, list) else ([result] if result else [])
            except Exception as e:
                if _is_user_cancelled_exc(e):
                    return []
                failures.append(f"windows-powershell: {e}")
        try:
            return _pick_tkinter_multi_files(initial)
        except Exception as e:
            if _is_user_cancelled_exc(e):
                return []
            failures.append(f"tkinter: {e}")
        single = pick_native_path("file", initial, multiple=False)
        return [single] if single else []

    failures: list[str] = []
    for name, fn in _backends():
        try:
            return fn(kind, initial)
        except ImportError as e:
            failures.append(f"{name}: 未安装（{e}）")
        except FileNotFoundError as e:
            failures.append(f"{name}: 找不到程序（{e}）")
        except Exception as e:
            if _is_user_cancelled_exc(e):
                return None
            failures.append(f"{name}: {e}")

    hint = (
        "无法打开本机文件选择对话框。"
        " Windows：请从桌面正常启动 MyAgent；或重装 Python 时勾选 tcl/tk。"
        f" 详情：{' | '.join(failures)}"
    )
    raise RuntimeError(hint)


def tkinter_available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False

