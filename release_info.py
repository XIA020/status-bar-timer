"""版本及发布位置，构建和应用内更新共用。"""

VERSION = "1.2.0"
APP_NAME = "任务栏计时器"
OWNER = "XIA020"
REPO = "status-bar-timer"


def asset_name(tag: str) -> str:
    return f"{REPO}-{tag}.exe"
