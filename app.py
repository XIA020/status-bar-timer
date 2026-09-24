"""Screen Time Tracker — 主程序 (socket server + tray 启动 + 子进程采集).

架构 (重写版, 2026-09-17; 任务栏 HUD 2026-09-24):
    app.py (主进程)
        ├─ tracker_worker.py  ← 子进程: 轮询 win32 前台窗口, 写 SQLite + state.json
        ├─ taskbar_hud.py     ← 子进程: 把计时文字嵌进任务栏 (WS_CHILD + SetParent)
        ├─ tray.ps1           ← 子进程: PowerShell NotifyIcon, 右键菜单
        └─ TCP server         ← 接受 tray 连接, 把当前 state 推给 tray

    IPC:
        data/control.json ← app.py 写 (paused=0/1, shutdown=0/1); HUD 也读它来退出
        data/state.json   → tracker_worker 写, app.py 读 (推送 tray tooltip 用)

为什么进程隔离: 在同一个 Python 进程里跑 win32GUI + TCP socket + sqlite, 在 Win 11
+ Python 3.13 下会偶发死锁 (recv 卡死). 拆成独立子进程稳定.
为什么 HUD 单独一个进程而不是塞进 tray.ps1: 任务栏停靠要精确控制窗口样式/分层
渲染/DPI 感知, PowerShell 5.1 下这套太脆; Python + ctypes 可控得多.
"""
from __future__ import annotations

import os
import sys
import socket
import subprocess
import threading
import time
import argparse
import json

import db
import paths

CHECK_INTERVAL = 2  # 备用, 不再用
SOCKET_HOST = "127.0.0.1"
DATA_DIR = os.path.join(paths.app_dir(), "data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CONTROL_PATH = os.path.join(DATA_DIR, "control.json")


# ==================== 打包后的角色分发 ====================
def _dispatch_frozen_role():
    """打包后: 同一个 exe 靠 --role 拉起各个子进程.

    返回 None 表示"不是子进程, 该跑主程序".
    """
    args = sys.argv[1:]
    if "--role" not in args:
        return None
    i = args.index("--role")
    if i + 1 >= len(args):
        print("[main] --role 后面缺少角色名", file=sys.stderr)
        return 2
    role = args[i + 1]
    # 把 --role xxx 从 argv 里摘掉, 免得被子模块的 argparse 当成未知参数
    sys.argv = [sys.argv[0]] + args[:i] + args[i + 2:]

    if role == "tracker":
        import tracker_worker

        tracker_worker.main()
        return 0
    if role == "hud":
        import taskbar_hud

        return taskbar_hud.main()
    if role == "dashboard":
        import dashboard

        dashboard.main()
        return 0
    print(f"[main] 未知角色: {role}", file=sys.stderr)
    return 2


# ==================== 工具函数 ====================
def _format_duration(seconds):
    """人类可读时长. 注意 divmod(s, 3600) 的第二项是**剩余秒数**, 不是分钟."""
    s = max(0, int(seconds))
    if s >= 3600:
        h, rem = divmod(s, 3600)
        return f"{h}h {rem // 60}m"
    m, sec = divmod(s, 60)
    if m > 0:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _read_state():
    """读 tracker_worker 写的 state.json."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"running": False, "paused": False, "process": None, "window": None, "duration": "0s"}


def _write_control(updates: dict):
    """合并更新 control.json, 通知 tracker_worker."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(CONTROL_PATH, "r", encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = {}
        cur.update(updates)
        tmp = CONTROL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False)
        os.replace(tmp, CONTROL_PATH)
    except Exception as e:
        print(f"[server] write_control err: {e}", file=sys.stderr)


def _toggle_paused():
    """读当前 paused, 翻转写回."""
    cur_paused = False
    try:
        with open(CONTROL_PATH, "r", encoding="utf-8") as f:
            cur_paused = bool(json.load(f).get("paused", False))
    except Exception:
        pass
    _write_control({"paused": not cur_paused, "shutdown": False})
    return not cur_paused


def _request_shutdown():
    _write_control({"shutdown": True, "paused": False})


# ==================== Socket server ====================
class TrayServer:
    """TCP server on 127.0.0.1:random port. tray.ps1 连接, 收菜单命令 + 推 state."""

    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.port = self._pick_port()
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((SOCKET_HOST, self.port))
        self.srv.listen(1)
        self._stop = False
        self.hud_proc = None      # taskbar_hud.py 子进程句柄, 退出时要收掉
        self.tray_proc = None     # tray.ps1 子进程句柄, 退出时要收掉
        self._last_push = 0.0
        self._loop_thread = threading.Thread(target=self._loop, daemon=True, name="TrayServerLoop")
        self._loop_thread.start()

    @staticmethod
    def _pick_port():
        import socket as _s
        s = _s.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _loop(self):
        """单 select 循环 (200ms tick). 监听 server + 当前 conn."""
        import select as _sel
        import traceback as _tb

        def _dbg(msg):
            try:
                sys.stderr.write(msg + "\n"); sys.stderr.flush()
            except Exception:
                pass

        try:
            self.srv.setblocking(False)
            _dbg(f"[server] event loop started, port={self.port}")

            counter = 0
            conn = None
            buf = b""
            while not self._stop:
                counter += 1
                read_list = [self.srv]
                if conn is not None:
                    read_list.append(conn)
                try:
                    r, _, _ = _sel.select(read_list, [], [], 0.2)
                except Exception as e:
                    _dbg(f"[server] select err: {e}")
                    continue

                if self.srv in r:
                    try:
                        new, addr = self.srv.accept()
                        new.setblocking(False)
                        try:
                            new.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        except Exception:
                            pass
                        _dbg(f"[server] accepted from {addr}")
                        if conn is not None:
                            try:
                                conn.close()
                            except Exception:
                                pass
                        conn = new
                        buf = b""
                    except Exception as e:
                        _dbg(f"[server] accept err: {e}")

                if conn is not None and conn in r:
                    try:
                        data = conn.recv(4096)
                        if not data:
                            _dbg("[server] client closed")
                            try:
                                conn.close()
                            except Exception:
                                pass
                            conn = None
                            buf = b""
                        else:
                            buf += data
                            while b"\n" in buf:
                                line, _, buf = buf.partition(b"\n")
                                line = line.decode("utf-8", errors="replace").strip()
                                if not line:
                                    continue
                                _dbg(f"[server] recv: {line!r}")
                                try:
                                    self._handle_command(json.loads(line))
                                except json.JSONDecodeError as e:
                                    _dbg(f"[server] json err: {e} line={line!r}")
                    except Exception as e:
                        _dbg(f"[server] recv err: {e}")
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                        buf = b""

                now = time.time()
                if conn is not None and now - self._last_push >= 0.5:
                    try:
                        state = _read_state()
                        # 加 type 字段以兼容 tray.ps1 协议
                        state["type"] = "state"
                        line = (json.dumps(state, ensure_ascii=False) + "\n").encode("utf-8")
                        conn.sendall(line)
                        self._last_push = now
                    except Exception as e:
                        _dbg(f"[server] push err: {e}")
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
        except Exception:
            _dbg(f"[server] FATAL: {_tb.format_exc()}")
        _dbg("[server] event loop exited")

    def _handle_command(self, msg):
        cmd = msg.get("cmd")
        if cmd == "dashboard":
            self._open_dashboard()
        elif cmd == "pause":
            new_paused = _toggle_paused()
            print(f"[server] pause toggled -> paused={new_paused}", flush=True)
        elif cmd == "quit":
            self._quit()
        else:
            print(f"[server] unknown cmd: {cmd}", file=sys.stderr)

    def _open_dashboard(self):
        try:
            subprocess.Popen(
                paths.child_cmd("dashboard", "dashboard.py"),
                cwd=paths.app_dir(),
                creationflags=0x08000000,
            )
            print("[server] dashboard launched", flush=True)
        except Exception as e:
            print(f"[server] dashboard failed: {e}", file=sys.stderr)

    def _quit(self):
        print("[server] quit command received", flush=True)
        _request_shutdown()
        self._stop = True
        try:
            self.srv.close()
        except Exception:
            pass
        # 收掉任务栏 HUD. 它自己也会因为 control.json 的 shutdown 标志退出,
        # 这里再 terminate 一次保证不留下孤儿进程.
        if self.hud_proc is not None:
            try:
                self.hud_proc.terminate()
                print("[server] taskbar_hud terminated", flush=True)
            except Exception as e:
                print(f"[server] hud terminate failed: {e}", file=sys.stderr)
        # 托盘进程同样要显式收掉.
        # 只靠它自己发现"socket 断了"是不够的: .NET 的 TcpClient.Connected 反映的是
        # 上一次 I/O 的状态, 对端关闭后它可能一直是 True, 于是进程残留 -> 幽灵托盘图标.
        if self.tray_proc is not None:
            try:
                self.tray_proc.terminate()
                print("[server] tray terminated", flush=True)
            except Exception as e:
                print(f"[server] tray terminate failed: {e}", file=sys.stderr)
        # 给 tracker 1s 收尾, 然后退出
        time.sleep(1.0)
        os._exit(0)

    def launch_tray(self):
        script = os.path.join(paths.res_dir(), "tray.ps1")
        if not os.path.exists(script):
            print(f"[server] tray.ps1 not found: {script}", file=sys.stderr)
            return None
        powershell = self._find_powershell()
        # ⚠️ 不要用 open(logfile, "wb") 预打开日志文件 — Win 下 Python 的 open() 默认
        # FILE_SHARE=None, tray.ps1 的 AppendAllText 会静默失败.
        # tray.ps1 自己用 AppendAllText 写 tray_stdout.log, 我们只需把它的 stdout/stderr
        # 接到 DEVNULL 即可.
        try:
            proc = subprocess.Popen(
                [powershell, "-ExecutionPolicy", "Bypass", "-File", script, "-Port", str(self.port)],
                cwd=self.project_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
            )
            print(f"[server] tray.ps1 launched pid={proc.pid} port={self.port}", flush=True)
            return proc
        except Exception as e:
            print(f"[server] tray launch failed: {e}", file=sys.stderr)
            return None

    @staticmethod
    def _find_powershell():
        import shutil
        for exe in ["powershell.exe", "pwsh.exe"]:
            try:
                p = shutil.which(exe)
                if p:
                    return p
            except Exception:
                continue
        return "powershell.exe"


# ==================== Taskbar HUD subprocess ====================
def launch_hud(project_dir: str):
    """把计时文字嵌进任务栏的 HUD (独立进程).

    它在自己的消息循环里读 data/state.json 刷新文字, 所以这里只负责拉起来.
    退出由两条路保证: control.json 的 shutdown 标志 (HUD 自己轮询), 以及
    TrayServer._quit 里显式 terminate.
    """
    script = os.path.join(project_dir, "taskbar_hud.py")
    if not paths.is_frozen() and not os.path.exists(script):
        print(f"[main] taskbar_hud.py not found: {script}", file=sys.stderr)
        return None
    try:
        proc = subprocess.Popen(
            paths.child_cmd("hud", "taskbar_hud.py"),
            cwd=project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        print(f"[main] taskbar_hud.py launched pid={proc.pid}", flush=True)
        return proc
    except Exception as e:
        print(f"[main] hud launch failed: {e}", file=sys.stderr)
        return None


# ==================== Tracker subprocess ====================
def launch_tracker(project_dir: str):
    """Spawn tracker_worker as a detached subprocess."""
    worker = os.path.join(project_dir, "tracker_worker.py")
    if not paths.is_frozen() and not os.path.exists(worker):
        print(f"[main] tracker_worker.py not found: {worker}", file=sys.stderr)
        return None
    try:
        proc = subprocess.Popen(
            paths.child_cmd("tracker", "tracker_worker.py"),
            cwd=project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        print(f"[main] tracker_worker.py launched pid={proc.pid}", flush=True)
        return proc
    except Exception as e:
        print(f"[main] tracker launch failed: {e}", file=sys.stderr)
        return None


# ==================== 开机自启动 ====================
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "ScreenTimeTracker"


def _autostart_command() -> str:
    """要写进注册表 Run 值的命令行.

    打包后就是 exe 自己; 源码模式是 venv 的 pythonw + app.py.
    """
    if paths.is_frozen():
        return f'"{os.path.abspath(sys.executable)}"'
    pyw = os.path.join(paths.app_dir(), ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return f'"{pyw}" "{os.path.join(paths.app_dir(), "app.py")}" --autostart'


def install_autostart() -> int:
    import winreg

    cmd = _autostart_command()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
    print(f"[autostart] 已写入 HKCU\\...\\Run\\{AUTOSTART_NAME}")
    print(f"            {cmd}")
    return 0


def uninstall_autostart() -> int:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, AUTOSTART_NAME)
        print(f"[autostart] 已移除 HKCU\\...\\Run\\{AUTOSTART_NAME}")
    except FileNotFoundError:
        print("[autostart] 本来就没注册, 无需移除")
    return 0


# ==================== 入口 ====================
def main():
    # 这两个开关要在重定向输出之前处理, 否则看不到提示
    if "--install-autostart" in sys.argv:
        sys.exit(install_autostart())
    if "--uninstall-autostart" in sys.argv:
        sys.exit(uninstall_autostart())

    # 打包后 _MEIPASS 是临时目录 (退出即删), 日志和数据必须落到 exe 旁边
    project_dir = paths.app_dir()
    project_log_dir = project_dir
    stdout_log = os.path.join(project_log_dir, "app_stdout.log")
    stderr_log = os.path.join(project_log_dir, "app_stderr.log")

    # 重定向 stdout/stderr 到 log 文件 (pythonw 无控制台, 默认丢日志)
    try:
        sys.stdout = open(stdout_log, "a", encoding="utf-8", buffering=1)
        sys.stderr = open(stderr_log, "a", encoding="utf-8", buffering=1)
    except Exception as e:
        sys.__stderr__.write(f"[main] redirect failed: {e}\n")

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--autostart", action="store_true")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    db.init_db()

    # 写初始 control.json (确保 tracker_worker 起来时能读到)
    _write_control({"paused": False, "shutdown": False})

    if not args.no_start:
        launch_tracker(project_dir)

    server = TrayServer(project_dir)
    if not args.no_start:
        server.hud_proc = launch_hud(project_dir)
    tray_proc = server.launch_tray()
    server.tray_proc = tray_proc

    print(f"[main] running. port={server.port}", flush=True)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    _rc = _dispatch_frozen_role()
    if _rc is None:
        main()
    else:
        sys.exit(_rc)
