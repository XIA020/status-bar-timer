"""从 aria2 的分段记录读取实际进度，并估算下载速度。"""

from pathlib import Path
import struct
import time


class DownloadProgress:
    def __init__(self):
        self._samples = {}
        self._items = ()
        self._next_poll = 0

    def read(self, directory):
        now = time.monotonic()
        if now < self._next_poll:
            return self._items
        self._next_poll = now + 2
        items = []
        active = set()
        root = Path(directory)
        for checkpoint in sorted(root.glob("*.aria2")):
            try:
                data = checkpoint.read_bytes()
                if data[:2] != b"\x00\x01" or data[2:6] != bytes(4):
                    continue
                offset = 10 + struct.unpack_from(">I", data, 6)[0]
                piece, total, _, length = struct.unpack_from(">IQQI", data, offset)
                offset += 24
                if not piece or not total:
                    continue
                count = (total + piece - 1) // piece
                if length != (count + 7) // 8 or len(data) < offset + length:
                    continue
                field = data[offset:offset + length]
                complete = sum(value.bit_count() for value in field) * piece
                if field[(count - 1) // 8] & (128 >> ((count - 1) % 8)):
                    complete -= count * piece - total
                saved_at = checkpoint.stat().st_mtime
            except (OSError, struct.error):
                continue
            active.add(checkpoint)
            previous_at, previous_bytes, speed = self._samples.get(checkpoint, (saved_at, complete, 0))
            if saved_at > previous_at:
                speed = max(0, (complete - previous_bytes) / (saved_at - previous_at))
                self._samples[checkpoint] = (saved_at, complete, speed)
            else:
                self._samples.setdefault(checkpoint, (saved_at, complete, speed))
            if time.time() - saved_at > 90:
                speed = 0
            items.append((checkpoint.name[:-6], min(100, int(100 * complete / total)), int(speed)))
        self._samples = {key: samples for key, samples in self._samples.items() if key in active}
        self._items = tuple(items)
        return self._items


def speed_label(speed):
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.1f} MB/s"
    return f"{speed // 1024} KB/s"
