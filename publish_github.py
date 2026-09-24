"""publish_github.py — 把本项目发布到 GitHub.

做四件事:
    1. 从 Windows 凭据管理器里取已存好的 git 凭据 (git credential fill)
    2. 建仓库 (公开)
    3. 推送代码 (git push)
    4. 建 Release 并把 exe 作为附件上传

⚠️ 凭据处理: token 只在本进程内存里使用, **不打印、不写文件、不写进 .git/config**.
   推送那一步交给 git 自己的凭据助手, 所以远程 URL 里也不含 token.

用法:
    .venv\\Scripts\\python.exe publish_github.py --check      # 只看凭据能不能用 (不改任何东西)
    .venv\\Scripts\\python.exe publish_github.py --repo-only  # 只建仓库 + 推送代码
    .venv\\Scripts\\python.exe publish_github.py              # 全流程 (含 Release + exe)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
OWNER = "XIA020"
# ⚠️ GitHub 仓库名只允许 ASCII (字母/数字/./-/_)。用中文名的话会被剥成 "-",
# 所以这里用英文名, 中文显示名放在 description 和 README 标题里。
REPO = "status-bar-timer"
TAG = "v1.0.0"
DESCRIPTION = "把当前应用的已用时长嵌进 Windows 任务栏，并统计屏幕使用时长"
EXE = os.path.join(ROOT, "dist", "状态栏记时器.exe")
ASSET_NAME = f"{REPO}-{TAG}.exe"

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"

RELEASE_BODY = """## 状态栏记时器 v1.0.0

把「当前应用 + 已用时长」直接**嵌进 Windows 任务栏**，并统计每天的屏幕使用时长。

### 主要功能

- **任务栏内实时计时**：文字直接画在任务栏里，只有字、没有方块背景，自动避让任务按钮和托盘区
- **时长累计**：切走再切回来接着涨，不是从 0 重新开始
- **旁边列出今日用得最久的几个应用**（用更淡的颜色，不抢主计时的焦点）
- **管理页面**：每天 / 每周两种视图，大字总时长 + 与昨天（上周）对比 + 柱状图 + 应用列表（带真实图标）
- **纯本地**：数据存 SQLite，无云同步、无遥测、无账号

### 下载

下载下面的 `{asset}`，放到一个长期保留的目录，双击运行即可。

- 自包含：Python 运行时和依赖都打包在这一个文件里，机器上不需要装 Python
- 绿色：设置、历史数据、图标缓存都写在 exe 旁边的 `data\\` 目录，删掉整个目录就干净卸载
- 开机自启：`{asset} --install-autostart` 装，`--uninstall-autostart` 卸

首次运行如果 SmartScreen 提示「未知发布者」，点「更多信息 → 仍要运行」（没有做代码签名）。

### 环境要求

Windows 10 1809+ / Windows 11（需要 WebView2 运行时；Win 11 自带，Win 10 可能要装一次）。

### 已知限制

- 只跟随主显示器的任务栏
- 应用图标是从 exe 现抽的，少数系统宿主进程抽不到，会显示成首字母头像
"""


# ---------------- 凭据 ----------------

def _fill(extra: str = "") -> dict:
    """跑一次 git credential fill. 不打印内容.

    带超时: 凭据失效时 GCM 可能会尝试弹浏览器重新授权, 那样会一直卡住.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n" + extra + "\n",
            capture_output=True, text=True, env=env, timeout=25,
        )
    except subprocess.TimeoutExpired:
        print(f"  [警告] git credential fill 超时 (extra={extra!r}) —— 跳过这个候选")
        return {}
    if proc.returncode != 0:
        return {}
    info = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info


def _describe(info: dict) -> str:
    """安全的描述 (字段名/长度/类型), 绝不打印内容."""
    pw = info.get("password") or ""
    kind = "未知"
    for pfx, label in (("ghp_", "经典 PAT"), ("github_pat_", "细粒度 PAT"),
                       ("gho_", "OAuth token"), ("ghu_", "用户 token"),
                       ("ghs_", "App 安装 token")):
        if pw.startswith(pfx):
            kind = label
    return f"username={info.get('username','?')} 长度={len(pw)} 类型={kind}"


def credential_candidates(debug: bool = False) -> list[dict]:
    """凭据管理器里可能同时存着多条 (比如换账号/重新授权之后旧的那条还在).

    注意: 不带 username 时拿到的是"通用"那条, 可能已经失效.
    所以这里把候选都取出来, 由调用方逐个验证, 用第一个能过的.
    """
    cands = []
    for extra in ("", f"username={OWNER}"):
        info = _fill(extra)
        if info.get("password"):
            cands.append(info)
    seen, out = set(), []
    for c in cands:
        if c["password"] not in seen:
            seen.add(c["password"])
            out.append(c)
    if debug:
        print(f"  [诊断] 凭据候选 {len(out)} 条:")
        for c in out:
            print("      -", _describe(c))
    return out


def get_credential(debug: bool = False) -> tuple[str, str]:
    """取一组**能用**的凭据 (逐个候选调 /user 验证). 不打印内容."""
    cands = credential_candidates(debug=debug)
    if not cands:
        raise SystemExit("[凭据] 凭据管理器里没有 github.com 的凭据")
    last = ""
    for c in cands:
        token = c["password"]
        status, data = api("GET", "/user", token)
        if status == 200 and isinstance(data, dict):
            login = data.get("login") or c.get("username") or ""
            print(f"  用可用凭据: {_describe(c)}  账号: {login}")
            if len(cands) > 1:
                print(f"  （另有 {len(cands)-1} 条候选不可用, 应该是重新授权前的旧条目）")
            return login, token
        last = f"{status}: {str(data)[:160]}"
    raise SystemExit(f"[凭据] {len(cands)} 条候选都验证失败, 最后一条返回 {last}")


# ---------------- HTTP ----------------

def api(method: str, path: str, token: str, body=None, base: str = API,
        raw: bytes | None = None, ctype: str | None = None):
    url = base + path
    data = raw if raw is not None else (
        json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "status-bar-timer-publish")
    if data is not None:
        req.add_header("Content-Type", ctype or "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(payload)
            except json.JSONDecodeError:
                return r.status, payload
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def step(t):
    print(f"\n=== {t} ===", flush=True)


# ---------------- 各步骤 ----------------

def create_repo(token: str) -> None:
    step("2/4 建仓库 (公开)")
    status, data = api("POST", "/user/repos", token, {
        "name": REPO,
        "description": DESCRIPTION,
        "private": False,
        "has_issues": True,
        "has_wiki": False,
        "auto_init": False,
    })
    if status == 201:
        print(f"  已创建: {data.get('html_url')}")
        return
    if status == 422:
        print("  仓库已存在 (422), 继续用现有的")
        return
    if status == 403:
        raise SystemExit(
            "  [失败] 403 —— token 没有建仓库的权限。\n"
            "         细粒度 token 建新仓库需要 Administration 权限; 经典 token 需要 repo 权限。\n"
            "         替代做法: 你在 https://github.com/new 手动建一个空的公开仓库\n"
            f"         名字必须是「{REPO}」(不要勾 README / .gitignore / license),\n"
            "         然后重新跑本脚本, 它会跳过建仓库这一步。")
    raise SystemExit(f"  [失败] 建仓库返回 {status}: {str(data)[:400]}")


def push_code() -> None:
    step("3/4 推送代码")
    url = f"https://github.com/{OWNER}/{urllib.parse.quote(REPO)}.git"
    subprocess.run(["git", "remote", "remove", "origin"],
                   cwd=ROOT, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=ROOT, check=True)
    print(f"  remote: {url}")
    proc = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=ROOT,
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    print("  " + out.strip().replace("\n", "\n  "))
    if proc.returncode != 0:
        raise SystemExit("  [失败] git push 返回非 0")


def create_release(token: str, login: str) -> str:
    step("4/4 建 Release 并上传 exe")
    if not os.path.exists(EXE):
        raise SystemExit(f"  [跳过] 找不到 exe: {EXE}\n         先跑 build_exe.py")
    status, data = api("POST", f"/repos/{login}/{REPO}/releases", token, {
        "tag_name": TAG,
        "target_commitish": "main",
        "name": f"状态栏记时器 {TAG}",
        "body": RELEASE_BODY.format(asset=ASSET_NAME),
        "draft": False,
        "prerelease": False,
    })
    if status not in (200, 201):
        raise SystemExit(f"  [失败] 建 Release 返回 {status}: {str(data)[:400]}")
    rel = data
    print(f"  Release: {rel.get('html_url')}")

    size = os.path.getsize(EXE)
    print(f"  上传 {os.path.basename(EXE)} ({size/1024/1024:.1f} MB) 作为 {ASSET_NAME} …")
    with open(EXE, "rb") as f:
        blob = f.read()
    q = urllib.parse.urlencode({"name": ASSET_NAME})
    status, data = api("POST",
                       f"/repos/{login}/{REPO}/releases/{rel['id']}/assets?{q}",
                       token, base=UPLOADS, raw=blob,
                       ctype="application/octet-stream")
    if status not in (200, 201):
        raise SystemExit(f"  [失败] 上传附件返回 {status}: {str(data)[:400]}")
    print(f"  附件: {data.get('browser_download_url')}")
    return rel.get("html_url", "")


def replace_asset(token: str, login: str) -> int:
    """把 Release 里的 exe 附件换成当前 dist 里构建出来的那份 (同名覆盖)."""
    step("替换 Release 附件")
    if not os.path.exists(EXE):
        raise SystemExit(f"  [失败] 找不到 exe: {EXE}")
    status, rel = api("GET", f"/repos/{login}/{REPO}/releases/tags/{TAG}", token)
    if status != 200 or not isinstance(rel, dict):
        raise SystemExit(f"  [失败] 取 Release 返回 {status}: {str(rel)[:300]}")
    for a in rel.get("assets", []):
        if a.get("name") == ASSET_NAME:
            s2, _ = api("DELETE", f"/repos/{login}/{REPO}/releases/assets/{a['id']}", token)
            print(f"  删除旧附件 {ASSET_NAME} -> {s2}")
    with open(EXE, "rb") as f:
        blob = f.read()
    q = urllib.parse.urlencode({"name": ASSET_NAME})
    s3, data = api("POST", f"/repos/{login}/{REPO}/releases/{rel['id']}/assets?{q}",
                   token, base=UPLOADS, raw=blob, ctype="application/octet-stream")
    if s3 not in (200, 201):
        raise SystemExit(f"  [失败] 上传返回 {s3}: {str(data)[:400]}")
    print(f"  新附件: {data.get('browser_download_url')}  ({len(blob)/1024/1024:.1f} MB)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    login, token = get_credential(debug="--debug-cred" in args)

    if "--check" in args:
        print("\n(--check 模式, 没有做任何改动)")
        return 0

    if "--replace-asset" in args:
        return replace_asset(token, login)

    if not os.path.isdir(os.path.join(ROOT, ".git")):
        raise SystemExit("当前目录不是 git 仓库")

    create_repo(token)
    push_code()

    if "--repo-only" in args:
        print("\n(--repo-only 模式, 跳过 Release)")
        print(f"仓库地址: https://github.com/{login}/{REPO}")
        return 0

    url = create_release(token, login)
    print(f"\n[完成] 仓库: https://github.com/{login}/{REPO}")
    print(f"       Release: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
