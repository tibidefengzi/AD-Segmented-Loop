"""Windows Common Item Dialogs, using IFileDialog directly in an isolated STA process.

The worker uses only Python's standard library. No PowerShell/WinForms fallback:
open, save and folder selection share the same Windows native dialog API.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DialogError(RuntimeError):
    pass


def _file_patterns(patterns) -> str:
    return ";".join("*" + item if item.startswith(".") else item for item in patterns) if patterns else "*.*"


def _invoke(request: dict):
    if os.name != "nt":
        raise DialogError("系统选择框仅支持 Windows")
    try:
        result = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--native-dialog"],
            input=json.dumps(request, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8",
            timeout=600, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        raise DialogError(f"无法打开系统选择框：{error}") from error
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as error:
        raise DialogError("系统选择框未返回有效结果") from error
    if result.returncode or payload.get("error"):
        raise DialogError(str(payload.get("error") or result.stderr or "系统选择框打开失败"))
    return payload.get("result", [])


def pick_files(initial_dir: str = "", title: str = "打开", patterns: tuple[str, ...] = ()) -> list[str]:
    return _invoke({"mode": "open", "initial_dir": initial_dir, "title": title, "patterns": list(patterns)})


def pick_folder(initial_dir: str = "", title: str = "选择保存文件夹") -> str:
    result = _invoke({"mode": "folder", "initial_dir": initial_dir, "title": title})
    return result[0] if result else ""


def save_file(default_name: str = "", initial_dir: str = "", title: str = "另存为",
              patterns: tuple[str, ...] = ()) -> str:
    result = _invoke({"mode": "save", "initial_dir": initial_dir, "title": title,
                      "default_name": default_name, "patterns": list(patterns)})
    return result[0] if result else ""


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _COM:
    """Small typed vtable adapter; slots follow the Windows SDK interface order."""
    def __init__(self, pointer):
        self.pointer = pointer

    def call(self, slot, types=(), *args, checked=True):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[slot])
        status = method(self.pointer, *args)
        if checked and status < 0:
            raise DialogError(f"Windows文件窗口调用失败：0x{status & 0xffffffff:08X}")
        return status

    def query(self, iid):
        pointer = ctypes.c_void_p()
        guid = _GUID.parse(iid)
        self.call(0, (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(guid), ctypes.byref(pointer))
        return _COM(pointer)

    def release(self):
        if self.pointer:
            self.call(2, checked=False)
            self.pointer = None


class _ThumbnailEvents:
    """IFileDialogEvents: use thumbnail view when entering a folder, like image loading."""
    def __init__(self):
        self.references = 1
        self.view_mode = None
        self.icon_size = None
        self.error = ""
        pointer = ctypes.c_void_p
        hresult = ctypes.c_long
        callback = ctypes.WINFUNCTYPE

        def query(this, iid, result):
            accepted = {bytes(_GUID.parse(value))
                        for value in ("00000000-0000-0000-c000-000000000046", "973510db-7d7f-452b-8975-74a85828d354")}
            if bytes(iid.contents) not in accepted:
                result[0] = None
                return ctypes.c_long(0x80004002).value
            result[0] = ctypes.addressof(self.instance)
            self.references += 1
            return 0

        def add_ref(this):
            self.references += 1
            return self.references

        def release(this):
            self.references = max(0, self.references - 1)
            return self.references

        def folder_change(this, dialog_pointer):
            provider = view = None
            try:
                provider = _COM(dialog_pointer).query("6d5140c1-7436-11ce-8034-00aa006009fa")
                service = _GUID.parse("cde725b0-ccc9-4519-917e-325d72fab4ce")  # SID_SFolderView
                iid = _GUID.parse("1af3a467-214f-4298-908e-06b03e0b39f9")  # IID_IFolderView2
                result = pointer()
                provider.call(3, (ctypes.POINTER(_GUID), ctypes.POINTER(_GUID), ctypes.POINTER(pointer)),
                              ctypes.byref(service), ctypes.byref(iid), ctypes.byref(result))
                view = _COM(result)
                view.call(35, (ctypes.c_uint32, ctypes.c_int), 1, 96)  # icon view, 96px thumbnails
                mode = ctypes.c_uint32()
                size = ctypes.c_int()
                view.call(36, (ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_int)),
                          ctypes.byref(mode), ctypes.byref(size))
                self.view_mode = mode.value
                self.icon_size = size.value
                self.error = ""
            except Exception as error:
                self.error = str(error)
            finally:
                if view: view.release()
                if provider: provider.release()
            return 0

        def default_response(this, dialog, item, response):
            response[0] = 0  # Keep Windows' share/overwrite confirmation behavior.
            return 0

        self.callbacks = [
            callback(hresult, pointer, ctypes.POINTER(_GUID), ctypes.POINTER(pointer))(query),
            callback(ctypes.c_uint32, pointer)(add_ref),
            callback(ctypes.c_uint32, pointer)(release),
            callback(hresult, pointer, pointer)(lambda this, dialog: 0),
            callback(hresult, pointer, pointer, pointer)(lambda this, dialog, folder: 0),
            callback(hresult, pointer, pointer)(folder_change),
            callback(hresult, pointer, pointer)(lambda this, dialog: 0),
            callback(hresult, pointer, pointer, pointer, ctypes.POINTER(ctypes.c_uint32))(default_response),
            callback(hresult, pointer, pointer)(lambda this, dialog: 0),
            callback(hresult, pointer, pointer, pointer, ctypes.POINTER(ctypes.c_uint32))(default_response),
        ]
        self.table = (pointer * len(self.callbacks))(*[ctypes.cast(cb, pointer).value for cb in self.callbacks])

        class Instance(ctypes.Structure):
            _fields_ = [("vtable", ctypes.POINTER(pointer))]
        self.instance = Instance(self.table)


class _NativeDialog:
    IID_ITEM = "43826d1e-e718-42ee-bc55-a1e261c37bfe"
    IID_OPEN = "d57c7288-d4ad-4768-be02-9d969532d960"
    IID_DIALOG2 = "61744fc7-85b5-4791-a9b0-272276309b13"

    def __init__(self, request):
        self.request = request
        self.dialog = None
        self.initialized = False
        self.events = None
        self.cookie = ctypes.c_uint32()
        self.ole = ctypes.OleDLL("ole32")
        self.ole.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        self.ole.CoInitializeEx.restype = ctypes.c_long
        self.ole.CoCreateInstance.argtypes = (ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        self.ole.CoCreateInstance.restype = ctypes.c_long
        self.ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        self.ole.CoTaskMemFree.restype = None
        self.shell = ctypes.OleDLL("shell32")
        self.shell.SHCreateItemFromParsingName.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p,
                                                         ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        self.shell.SHCreateItemFromParsingName.restype = ctypes.c_long

    def prepare(self):
        mode = self.request.get("mode")
        if mode not in {"open", "save", "folder"}:
            raise DialogError("未知文件窗口类型")
        self.ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        self.initialized = True
        clsid = _GUID.parse("c0b4e2f3-ba21-4773-8dba-335ec946eb8b" if mode == "save"
                            else "dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")
        iid = _GUID.parse("42f85136-db7e-439c-85f1-e4075d135fc8")
        pointer = ctypes.c_void_p()
        self.ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer))
        self.dialog = _COM(pointer)
        options = ctypes.c_uint32()
        self.dialog.call(10, (ctypes.POINTER(ctypes.c_uint32),), ctypes.byref(options))
        flags = options.value | 0x40 | 0x800 | 0x8  # filesystem, existing path, no cwd changes
        flags |= 0x2 if mode == "save" else 0x20 if mode == "folder" else 0x200 | 0x1000
        self.dialog.call(9, (ctypes.c_uint32,), flags)
        self.dialog.call(17, (ctypes.c_wchar_p,), str(self.request.get("title") or ("另存为" if mode == "save" else "打开")))
        self.dialog.call(18, (ctypes.c_wchar_p,), "保存(&S)" if mode == "save" else "选择文件夹" if mode == "folder" else "打开(&O)")
        self.dialog.call(19, (ctypes.c_wchar_p,), "文件名(&N):")
        dialog2 = self.dialog.query(self.IID_DIALOG2)
        try:
            dialog2.call(27, (ctypes.c_wchar_p,), "取消")
        finally:
            dialog2.release()
        initial = str(self.request.get("initial_dir") or "")
        if initial:
            if not Path(initial).is_dir():
                raise DialogError("初始目录不存在：" + initial)
            item = ctypes.c_void_p()
            item_iid = _GUID.parse(self.IID_ITEM)
            self.shell.SHCreateItemFromParsingName(initial, None, ctypes.byref(item_iid), ctypes.byref(item))
            folder = _COM(item)
            try:
                self.dialog.call(12, (ctypes.c_void_p,), folder.pointer)  # exact folder, overrides MRU
            finally:
                folder.release()
        if mode != "folder":
            class FilterSpec(ctypes.Structure):
                _fields_ = [("name", ctypes.c_wchar_p), ("pattern", ctypes.c_wchar_p)]
            self.filters = (FilterSpec * 2)(
                FilterSpec("媒体文件" if mode == "save" else "素材文件", _file_patterns(self.request.get("patterns") or ())),
                FilterSpec("所有文件", "*.*"))
            self.dialog.call(4, (ctypes.c_uint32, ctypes.POINTER(FilterSpec)), 2, self.filters)
        filename = str(self.request.get("default_name") or "")
        if mode == "save" and filename:
            self.dialog.call(15, (ctypes.c_wchar_p,), filename)
            suffix = Path(filename).suffix.lstrip(".")
            if suffix:
                self.dialog.call(22, (ctypes.c_wchar_p,), suffix)
        self.events = _ThumbnailEvents()
        self.dialog.call(7, (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)),
                         ctypes.byref(self.events.instance), ctypes.byref(self.cookie))

    def item_path(self, item):
        text = ctypes.c_void_p()
        item.call(5, (ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)), 0x80058000, ctypes.byref(text))
        try:
            return ctypes.wstring_at(text.value)
        finally:
            self.ole.CoTaskMemFree(text)

    def audit(self):
        options = ctypes.c_uint32()
        self.dialog.call(10, (ctypes.POINTER(ctypes.c_uint32),), ctypes.byref(options))
        pointer = ctypes.c_void_p()
        self.dialog.call(13, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(pointer))
        folder = _COM(pointer)
        try:
            return {"engine": "Windows IFileDialog", "initial_dir": self.item_path(folder), "options": options.value}
        finally:
            folder.release()

    def show(self):
        status = self.dialog.call(3, (ctypes.c_void_p,), None, checked=False)
        if status & 0xffffffff == 0x800704C7:  # HRESULT_FROM_WIN32(ERROR_CANCELLED)
            return []
        if status < 0:
            raise DialogError(f"无法显示文件窗口：0x{status & 0xffffffff:08X}")
        if self.request["mode"] != "open":
            pointer = ctypes.c_void_p()
            self.dialog.call(20, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(pointer))
            item = _COM(pointer)
            try:
                return [self.item_path(item)]
            finally:
                item.release()
        opened = self.dialog.query(self.IID_OPEN)
        array = None
        try:
            pointer = ctypes.c_void_p()
            opened.call(27, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(pointer))
            array = _COM(pointer)
            count = ctypes.c_uint32()
            array.call(7, (ctypes.POINTER(ctypes.c_uint32),), ctypes.byref(count))
            result = []
            for index in range(count.value):
                pointer = ctypes.c_void_p()
                array.call(8, (ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)), index, ctypes.byref(pointer))
                item = _COM(pointer)
                try:
                    result.append(self.item_path(item))
                finally:
                    item.release()
            return result
        finally:
            if array:
                array.release()
            opened.release()

    def close(self):
        if self.dialog:
            if self.events:
                self.dialog.call(8, (ctypes.c_uint32,), self.cookie.value, checked=False)
            self.dialog.release()
        if self.initialized:
            self.ole.CoUninitialize()


def _worker():
    native = None
    try:
        native = _NativeDialog(json.loads(sys.stdin.read()))
        native.prepare()
        result = native.audit() if native.request.get("audit") else native.show()
        print(json.dumps({"result": result, "view_mode": native.events.view_mode, "icon_size": native.events.icon_size,
                          "view_error": native.events.error}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    finally:
        if native:
            native.close()
    return 0


if __name__ == "__main__" and "--native-dialog" in sys.argv:
    raise SystemExit(_worker())
