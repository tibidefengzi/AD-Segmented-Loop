"""Windows 原生文件/文件夹选择框（不依赖 tkinter）。

ComfyUI 便携版的 python_embeded 通常没有 tkinter，因此改用系统自带 PowerShell +
WinForms 打开标准对话框；只在 Windows 上可用，失败时返回空并附带错误信息。
"""
from __future__ import annotations

import base64
import os
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DialogError(RuntimeError):
    pass


def _run(script: str) -> str:
    if os.name != "nt":
        raise DialogError("系统选择框仅支持 Windows")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    command = ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=600, creationflags=CREATE_NO_WINDOW,
                                  cwd=os.environ.get("TEMP") or None)
    except (OSError, subprocess.SubprocessError) as error:
        raise DialogError(f"无法打开系统选择框：{error}") from error
    if finished.returncode != 0:
        message = (finished.stderr or finished.stdout or "").strip()
        raise DialogError(message or "系统选择框打开失败")
    return finished.stdout or ""


def pick_files(initial_dir: str = "", title: str = "选择素材", patterns: tuple[str, ...] = ()) -> list[str]:
    """多选文件；取消返回空列表。patterns 形如 ('*.png', '*.mp4')。"""
    pattern = ";".join(patterns) if patterns else "*.*"
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "$dialog = New-Object System.Windows.Forms.OpenFileDialog\n"
        f"$dialog.Title = {title!r}\n"
        f"$dialog.InitialDirectory = {initial_dir!r}\n"
        "$dialog.Multiselect = $true\n"
        f"$dialog.Filter = '素材文件|{pattern}|所有文件|*.*'\n"
        "$dialog.CheckFileExists = $true\n"
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        " $dialog.FileNames | ForEach-Object { Write-Output $_ } }\n"
    )
    output = _run(script)
    return [line.strip() for line in output.splitlines() if line.strip()]


def pick_folder(initial_dir: str = "", title: str = "选择保存文件夹") -> str:
    """选择文件夹；取消返回空字符串。"""
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog\n"
        f"$dialog.Description = {title!r}\n"
        f"if ({initial_dir!r} -ne '') {{ $dialog.SelectedPath = {initial_dir!r} }}\n"
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        " Write-Output $dialog.SelectedPath }\n"
    )
    output = _run(script)
    return output.strip().splitlines()[0].strip() if output.strip() else ""


def save_file(default_name: str = "", initial_dir: str = "", title: str = "选择保存位置",
              patterns: tuple[str, ...] = ()) -> str:
    """标准 Windows「另存为」框：选择文件夹和文件名；取消返回空字符串。"""
    pattern = ";".join(patterns) if patterns else "*.*"
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "$dialog = New-Object System.Windows.Forms.SaveFileDialog\n"
        f"$dialog.Title = {title!r}\n"
        f"$dialog.FileName = {default_name!r}\n"
        f"if ({initial_dir!r} -ne '') {{ $dialog.InitialDirectory = {initial_dir!r} }}\n"
        f"$dialog.Filter = '媒体文件|{pattern}|所有文件|*.*'\n"
        "$dialog.OverwritePrompt = $true\n$dialog.AddExtension = $true\n$dialog.CheckPathExists = $true\n"
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        " Write-Output $dialog.FileName }\n"
    )
    output = _run(script)
    return output.strip().splitlines()[0].strip() if output.strip() else ""
