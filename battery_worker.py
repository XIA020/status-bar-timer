"""低频只读读取 HECATE G3M Pro 电量，并写入 data/battery.json。"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

import paths
import app_settings

ROOT = paths.app_dir()
DATA_DIR = os.path.join(ROOT, "data")
BATTERY_PATH = os.path.join(DATA_DIR, "battery.json")
LOG_PATH = os.path.join(DATA_DIR, "battery_debug.log")
POLL_SECONDS = 5
VID = 0x320F
PRODUCT_PRIORITY = (0x706B, 0x706E)  # 有线优先，其次 2.4G 接收器
USAGE_PAGE = 0xFF1C
USAGE = 0x0092


def log(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


def _packet() -> bytes:
    packet = bytearray(64)
    packet[0] = 0x04
    packet[3] = 0x1A
    packet[4] = 0x06
    checksum = sum(packet[3:]) & 0xFFFF
    packet[1] = checksum & 0xFF
    packet[2] = checksum >> 8
    return bytes(packet)


def _response_bytes(raw) -> bytes | None:
    if not raw:
        return None
    data = bytes(raw)
    # HIDAPI 在 Windows 上要求写入报告号前缀；部分接口读取时也会带回该前缀。
    if len(data) >= 65 and data[0] == 0:
        data = data[1:]
    if len(data) < 64 or data[0] != 0x04 or data[3] != 0x1A:
        return None
    return data[:64]


def read_once() -> dict:
    import hid

    devices = []
    for product in PRODUCT_PRIORITY:
        try:
            found = hid.enumerate(VID, product)
        except Exception:
            continue
        for item in found:
            if (item.get("usage_page") == USAGE_PAGE and item.get("usage") == USAGE
                    and item.get("path")):
                devices.append((product, item["path"]))

    if not devices:
        return {"available": False, "error": "未发现 G3M Pro 设备"}

    last_error = "设备暂时无法读取"
    for product, path in devices:
        dev = hid.device()
        try:
            dev.open_path(path)
            # 清掉打开设备前积压的响应，避免把旧包当成本次电量。
            dev.set_nonblocking(1)
            while dev.read(65, 0):
                pass
            dev.set_nonblocking(0)
            # 本设备 HID 输出报告本身就是 64 字节；直接写协议包，不能附加 Report ID。
            written = dev.write(_packet())
            if written <= 0:
                raise OSError("HID 命令发送失败")

            deadline = time.monotonic() + 1.5
            response = None
            while time.monotonic() < deadline:
                raw = dev.read(65, 100)
                response = _response_bytes(raw)
                if response:
                    break
            if response is None:
                raise TimeoutError("等待电量响应超时")
            if response[7] in (0xFE, 0xFF):
                raise ValueError("设备拒绝电量查询命令")
            percent, flag = response[8], response[9]
            if percent > 100:
                raise ValueError(f"设备返回无效电量 {percent}")

            connection = "wired" if product == 0x706B else "2.4G"
            if flag == 0x00:
                charging = "normal"
            elif flag == 0x02:
                charging = "charging"
            elif flag == 0x01 and connection == "wired":
                charging = "full" if percent >= 100 else "charging"
            else:
                charging = "unknown"
            return {
                "available": True,
                "percent": percent,
                "charging": charging,
                "connection": connection,
                "updated_at": time.time(),
            }
        except Exception as exc:
            last_error = str(exc)
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return {"available": False, "error": last_error}


def _write_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # 每个 worker 使用独立临时文件，避免并发写入时互相替换同一个 .tmp。
    tmp = f"{BATTERY_PATH}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, BATTERY_PATH)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _with_wireless_since(state: dict) -> dict:
    """记录从有线切换到 2.4G 后的累计使用起点。"""
    try:
        with open(BATTERY_PATH, "r", encoding="utf-8") as f:
            previous = json.load(f)
    except Exception:
        previous = {}
    if not state.get("available"):
        try:
            if previous.get("wireless_since"):
                state["wireless_since"] = previous["wireless_since"]
            if previous.get("connection"):
                state["connection"] = previous["connection"]
        except Exception:
            pass
        return state
    if state.get("connection") == "2.4G":
        wireless_since = previous.get("wireless_since")
        previous_connection = previous.get("connection")
        if not wireless_since or previous_connection != "2.4G":
            wireless_since = state.get("updated_at", time.time())
        state["wireless_since"] = wireless_since
    else:
        state["wireless_since"] = None
    return state


def run() -> None:
    log("G3M Pro 电量读取线程启动（只读，5 秒轮询）")
    while True:
        if not app_settings.feature_enabled("show_mouse_battery"):
            try:
                _write_state({"available": False, "disabled": True, "updated_at": time.time()})
            except Exception:
                pass
            time.sleep(1)
            continue
        started = time.monotonic()
        try:
            _write_state(_with_wireless_since(read_once()))
        except Exception as exc:
            log(f"电量读取失败：{exc}")
            try:
                # 写入异常状态时仍保留最近一次连接方式和无线计时起点。
                _write_state(_with_wireless_since({"available": False, "error": str(exc)}))
            except Exception:
                pass
        time.sleep(max(0.2, POLL_SECONDS - (time.monotonic() - started)))
