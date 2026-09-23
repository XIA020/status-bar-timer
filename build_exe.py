"""build_exe.py — 把项目打包成单个 exe (PyInstaller).

    .venv\\Scripts\\python.exe build_exe.py

产物: `dist\\状态栏记时器.exe` (单文件, 自带 Python 运行时和所有依赖)

要点:
* `--onefile` 让子进程也走同一个 exe (靠 `--role`, 见 app.py 的角色分发).
  PyInstaller 会通过 `_MEIPASS2` 让子进程复用父进程已经解开的临时目录, 不会重复解包.
* `--add-data` 把 只读资源 (网页三件套 + tray.ps1 + 图标) 打进包里;
  运行时会落在 `sys._MEIPASS`, 由 paths.res_dir() 取.
* 数据和日志写到 exe 旁边 (paths.app_dir()), 所以把 exe 放进项目目录就能接着用同一份历史数据.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "状态栏记时器"

# 只读资源: 源路径;打包后放到的相对位置 (都是根目录)
ADD_DATA = [
    "dashboard.html",
    "dashboard.css",
    "dashboard.js",
    "tray.ps1",
    "tray_icon.ico",
]

# 用不到的重家伙, 排除掉能显著缩小体积
EXCLUDES = [
    "matplotlib", "numpy", "pandas", "scipy", "tkinter", "pytest",
    "IPython", "jupyter", "pydoc_data", "lib2to3", "unittest",
]


def main() -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",
        "--name", NAME,
        "--icon", os.path.join(ROOT, "tray_icon.ico"),
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        # pywebview 的 Assets / WEBVIEW2 加载器
        "--collect-all", "webview",
        # pythonnet: pywebview 的 winforms 后端靠它加载 .NET
        "--collect-all", "pythonnet",
        "--hidden-import", "clr",
        "--hidden-import", "win32timezone",
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

    out_dir = os.path.join(ROOT, "dist")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)

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
