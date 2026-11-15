"""build_exe.py — 把项目打包成单个 exe (PyInstaller).

    .venv\\Scripts\\python.exe build_exe.py

产物: `dist\\任务栏计时器.exe` (单文件, 自带 Python 运行时和所有依赖)

要点:
* `--onefile` 让子进程也走同一个 exe (靠 `--role`, 见 app.py 的角色分发).
  冻结子进程设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，各自管理解包目录生命周期.
* `--add-data` 把 只读资源 (网页三件套 + tray.ps1 + 图标) 打进包里;
  运行时会落在 `sys._MEIPASS`, 由 paths.res_dir() 取.
* 打包版的数据和日志写到用户的 LocalAppData (paths.app_dir())。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import argparse
from pathlib import Path

from release_info import APP_NAME, VERSION

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = APP_NAME

# 只读资源: 源路径;打包后放到的相对位置 (都是根目录)
ADD_DATA = [
    "dashboard.html",
    "dashboard.css",
    "dashboard.js",
    "tray.ps1",
    "install_update.ps1",
    "tray_icon.ico",
]

# 用不到的重家伙, 排除掉能显著缩小体积
EXCLUDES = [
    "matplotlib", "numpy", "pandas", "scipy", "tkinter", "pytest",
    "IPython", "jupyter", "pydoc_data", "lib2to3", "unittest",
]


def write_version_info(path: Path) -> None:
    version = (*map(int, VERSION.split(".")), 0)
    path.write_text(f"""
VSVersionInfo(
    ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r}, mask=0x3f,
                      flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('080404b0', [
            StringStruct('FileDescription', {APP_NAME!r}),
            StringStruct('ProductName', {APP_NAME!r}),
            StringStruct('FileVersion', {VERSION!r}),
            StringStruct('ProductVersion', {VERSION!r}),
            StringStruct('OriginalFilename', {f"{APP_NAME}.exe"!r}),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0804, 1200])]),
    ],
)
""".strip(), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"构建{APP_NAME}")
    parser.add_argument("--output-dir", help="指定构建产物目录，不覆盖当前运行中的安装版")
    parser.add_argument("--build-only", action="store_true", help="只构建，不复制到项目根目录")
    options = parser.parse_args()
    out_dir = os.path.abspath(options.output_dir) if options.output_dir else os.path.join(ROOT, "dist")
    version_file = Path(ROOT) / "build" / "version_info.txt"
    version_file.parent.mkdir(exist_ok=True)
    write_version_info(version_file)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",
        "--name", NAME,
        "--icon", os.path.join(ROOT, "tray_icon.ico"),
        "--version-file", str(version_file),
        "--distpath", out_dir,
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        # pywebview 的 Assets / WEBVIEW2 加载器
        "--collect-all", "webview",
        # pythonnet: pywebview 的 winforms 后端靠它加载 .NET
        "--collect-all", "pythonnet",
        "--hidden-import", "clr",
        "--hidden-import", "win32timezone",
        # webview/pythonnet dynamically load this standard-library extension.
        "--hidden-import", "unicodedata",
        "--collect-all", "hid",
    ]
    for f in ADD_DATA:
        args += ["--add-data", f"{os.path.join(ROOT, f)}{os.pathsep}."]
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    args.append(os.path.join(ROOT, "app.py"))

    print("PyInstaller 参数:")
    for a in args[3:]:
        print("   ", a)
    print("\n开始构建 (几分钟)…\n", flush=True)

    # Keep dist/data intact: it contains the preview install's position,
    # feature switches, and local test history. PyInstaller replaces its exe
    # artifact; deleting the whole directory would reset those user settings.
    os.makedirs(out_dir, exist_ok=True)

    proc = subprocess.run(args, cwd=ROOT)
    if proc.returncode != 0:
        print(f"\n[失败] PyInstaller 返回 {proc.returncode}")
        return proc.returncode

    exe = os.path.join(out_dir, f"{NAME}.exe")
    if not os.path.exists(exe):
        print(f"\n[失败] 没找到产物: {exe}")
        return 1
    mb = os.path.getsize(exe) / 1024 / 1024
    print(f"\n[成功] {exe}")
    print(f"       大小 {mb:.1f} MB")

    if options.build_only or options.output_dir:
        return 0

    # 顺手放到项目根目录一份: 那里是"已安装"位置 —— 自启动和桌面快捷方式都指向它,
    # 而且它跟源码版共用同一份 data/ (历史记录不会分裂).
    installed = os.path.join(ROOT, f"{NAME}.exe")
    try:
        shutil.copy2(exe, installed)
        print(f"[已安装] {installed}")
    except OSError as e:
        print(f"[警告] 复制到项目根失败 (exe 可能正在运行): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
