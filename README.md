# 状态栏记时器

Windows 前台应用计时器：**把"当前应用 + 已用时长"直接嵌进任务栏**，自动记录，按日 / 周 / 月汇总，现代 Web UI。

跟 [What-did-I-do](https://github.com/f-u-t-u-r-e/What-did-I-do) 比，区别：

| | What-did-I-do | This |
|---|---|---|
| 启动方式 | 手动点"开始记录" | **启动后自动开始** |
| 实时显示 | 没有 | **文字嵌进任务栏**（不是浮窗、不是托盘提示） |
| 存储 | 按日 CSV | **SQLite**（跨日 / 周 / 月查询快） |
| UI | tkinter 丑 | **pywebview + ECharts** 现代深色主题 |
| 图表 | matplotlib 柱状 + 饼图 | ECharts 柱状 + 时段分布 + 趋势 |
| 跨日汇总 | 没有 | 本周 / 本月 Top 应用 + 每日总时长 |
| 自动启动 | 没有 | 注册表 HKCU\Run 一键安装 |

## 下载即用（免安装）

到 **[Releases](https://github.com/XIA020/status-bar-timer/releases)** 页面下载
`status-bar-timer-v1.0.0.exe`（就是本程序，约 21 MB），放到一个长期保留的目录，双击即可：

- **自包含**：Python 运行时和依赖都打进这一个文件里，机器上不需要装 Python
- **绿色**：设置、历史数据、图标缓存都写在 exe 旁边的 `data\` 目录，删掉整个目录就干净卸载了
- **开机自启**：`status-bar-timer-v1.0.0.exe --install-autostart` 装，`--uninstall-autostart` 卸
- 首次运行如果 Windows SmartScreen 提示「未知发布者」，点「更多信息 → 仍要运行」即可（没做代码签名）

启动后你会看到两样东西：**任务栏里的计时文字**，和**右下角托盘图标**（右键有菜单）。

## 功能

- 🕐 **任务栏内实时计时**：文字直接画在任务栏里，只有字、没有方块背景
- 🔁 **时长累计**：切走再切回来接着涨，不是从 0 重新开始
- 📊 **旁边列出今日用得最多的几个应用**（暗色显示，不抢主计时的焦点）
- 📈 **今日 Top 应用柱状图 + 时段分布热图**
- 📅 **本周 / 本月 Top 应用 + 每日趋势**
- 📋 **最近 20 条会话列表**（起止时间 + 时长 + 应用 + 窗口标题）
- ⏸️ **Pause / Resume**（不采集特定时段，比如开会）
- 🚀 **开机自启**（一键安装到注册表）
- 🔒 **纯本地**：数据存 SQLite，无云同步、无遥测、无账号

## 架构

```
app.py (主进程)
    ├─ tracker_worker.py   子进程: 每秒轮询前台窗口 → 写 SQLite + data/state.json
    ├─ taskbar_hud.py      子进程: 把计时文字嵌进任务栏 (WS_CHILD + SetParent)
    ├─ tray.ps1            子进程: PowerShell NotifyIcon, 右键菜单 + tooltip
    └─ TCP server (127.0.0.1)  tray 连上来收命令 / 收 state
```

- `data/control.json` ← app.py 写（`paused` / `shutdown`），tracker_worker 和 HUD 都读它
- `data/state.json` → tracker_worker 写，HUD 读它来刷新文字

`state.json` 里的字段：

| 字段 | 含义 |
|---|---|
| `process` / `paused` / `running` | 当前前台应用 / 是否暂停 |
| `duration` | **本次连续**时长（一切换就归零） |
| `today_total` | 当前应用**今日累计**时长（切走再切回来接着涨） |
| `top_apps[]` | 今日按累计时长排序的应用，每项含 `name` / `seconds` / `time`（紧凑）/ `time_long` |

**「时长累计」是怎么算的**：tracker_worker 在内存里维护一份「今日累计基数」`base`，
每次前台窗口切换就把当前段落库、并把这段时长累加进 `base`。切回来的时候计时从 `base` 往上继续，
所以不会归零。`base` 在启动时从数据库载入（`db.query_today_totals()`），跨零点时重新载入一次。

## 任务栏 HUD 怎么做到的

`taskbar_hud.py` 走的是"独立进程 + SetParent"路线，**不注入 explorer.exe**：

1. `FindWindow("Shell_TrayWnd")` 拿到任务栏
2. 自己的窗口去掉 `WS_POPUP`、加上 `WS_CHILD`，`SetParent` 进任务栏
3. 遍历任务栏子窗口的屏幕矩形，算出**空闲区间**（自动避开开始按钮 / 任务按钮区 / 托盘区）
4. `WS_EX_LAYERED` + `UpdateLayeredWindow` 做逐像素 alpha → 屏幕上只有文字
5. 监听 `TaskbarCreated` → explorer 崩溃重启后自动重新停靠；另有 5 秒一次的健康检查兜底

这一行是**两段**画上去的：主计时用满不透明，后面的 Top 列表用较低不透明度（`dim_alpha`），
所以两者有明显的主次层次，而不是一坨同样粗细的字。Top 列表是**逐项试放**的——
当前空闲宽度放不下就停，不会截出半个应用名。

这样它自动跟随任务栏：任务栏自动隐藏、改分辨率、换缩放、explorer 重启都能跟上。

### 交互

| 操作 | 效果 |
|---|---|
| 左键拖动 | 调整在任务栏里的水平位置（松手后记住） |
| 双击 / 右键 | 打开 dashboard |

光标移动不超过 `drag_threshold_px`（默认 4px）不算拖动 —— 否则**轻点一下就会把位置钉死**，
再也回不到自动贴边。

### 配置

改 `data/hud_config.json`（改完 1 秒内自动生效，不用重启）：

| 键 | 默认 | 说明 |
|---|---|---|
| `anchor` | `"right"` | `right` 贴托盘区左边 / `left` 贴任务栏最左 / `center` 最长空闲段居中 / `manual` 用拖动记住的位置 |
| `gap_px` | `10` | 与相邻任务栏元素的间距 |
| `max_width_px` | `460` | 内容宽度上限（还会被"当前空闲段宽度"二次限制） |
| `min_width_px` | `60` | 下限；空闲段比这个还窄时改用最长的空闲段 |
| `font_name` / `font_size_px` / `font_bold` | `Segoe UI` / `12` / `false` | 字号按 96 DPI 的逻辑像素写，实际按屏幕缩放换算。12 ≈ Win11 任务栏时钟的字号 |
| `text_color` | `"auto"` | `auto` = 跟随系统浅色/深色主题，也可写 `"#E6E6E6"` |
| `background` / `bg_color` / `bg_alpha` | `false` / `#1C1C1C` / `90` | 想在文字后面加半透明圆角底就打开 |
| `text_offset_y_px` | `0` | 垂直微调（觉得偏高/偏低时用，负值上移） |
| `duration_mode` | `"today_total"` | `today_total` = 显示今日累计（切回来接着涨）/ `current` = 只显示本次连续 |
| `format_active` | `"{process} ({duration})"` | 可用 `{process}` `{duration}` |
| `format_paused` / `format_idle` | `"已暂停"` / `"空闲"` | |
| `hide_when_paused` | `false` | 暂停时完全隐藏 |
| `show_top_apps` | `true` | 是否显示后面的 Top 列表 |
| `top_apps_count` | `3` | 最多列几个（放不下会自动少列） |
| `top_apps_include_current` | `false` | 列表里要不要再列一次当前应用（主计时已经显示了，默认不重复） |
| `top_apps_name_max` | `12` | 应用名超过这个长度截断（会自动去掉 `.exe` 尾巴）；`0` = 不截断 |
| `top_apps_time_format` | `"compact"` | `compact` = `12m` / `2h3m`；`long` = `12m 30s` / `2h 3m` |
| `top_apps_gap` / `top_apps_item_sep` | `"   "` / `" · "` | 计时与列表之间 / 列表项之间的分隔 |
| `dim_alpha` | `150` | 列表那段的不透明度（0-255），越低越"退后" |
| `drag_threshold_px` | `4` | 光标移动超过这个距离才算拖动 |
| `enabled` | `true` | 设成 `false` 会让 HUD 自己退出 |
| `poll_ms` / `health_every` | `500` / `10` | 刷新间隔 / 每 N 次刷新做一次停靠健康检查 |

拖动过之后 `anchor` 会变成 `manual`，位置存在 `data/hud_pos.json`。
**想恢复自动贴边**：把 `anchor` 改回 `"right"` 即可。

**想让它多列几个应用**：把 `anchor` 改成 `"center"`（用最长的空闲段，空间大得多），
或调小 `font_size_px` / `top_apps_name_max`，或调大 `max_width_px`。

### 管理页面

托盘右键 → **Open Dashboard**（或双击托盘图标 / 双击任务栏那行计时）打开。

布局参考手机上的「屏幕使用时长」，两种视图：

| | 每天 | 每周 |
|---|---|---|
| 大字 | 今天总时长 | 本周总时长 |
| 对比 | 比昨天增加 / 减少多少 | 比上周增加 / 减少多少 |
| 柱状图 | 24 小时，纵轴刻度自动取整到 30 分钟 | 周一至今天，每根一天的柱子 |
| 列表 | 今天用得最多的应用（图标 + 时长 + 占比） | 本周同上 |

- 应用图标是从 exe 里现抽的（`app_icons.py`），抽不到就退化成首字母圆形头像
- 悬停柱子会显示那个时段的具体时长
- 页面每 30 秒自更新一次；URL 支持 `#week` / `#day` 直接定位到某个视图

### 诊断命令

```powershell
.\.venv\Scripts\python.exe taskbar_hud.py --check
```
只读地报告：是否停靠在任务栏、窗口矩形、当前显示的文字**以及分段明细**（每段的文字/颜色/不透明度）。

```powershell
.\.venv\Scripts\python.exe verify_today.py                     # 核对界面上的数字跟数据库对不对得上
.\.venv\Scripts\python.exe taskbar_hud.py --selftest           # 停靠+渲染一次并打印全部指标
.\.venv\Scripts\python.exe taskbar_hud.py --test-recovery      # 测 explorer 重建任务栏后能否自动重停靠
.\.venv\Scripts\python.exe taskbar_hud.py --test-recovery-hard # 再测健康检查自愈（会有几秒浮窗）
.\.venv\Scripts\python.exe probe_taskbar_dock.py              # 打印任务栏结构与子窗口树（排错用）
```

`verify_today.py` 是最有用的那个：它把「HUD 正在显示的数」和「数据库里的真实汇总」摆在一起，
并说明界面上显示的是"所有历史段之和 + 当前这一段"，可以一眼确认累计逻辑没出问题。

日志在 `hud.log`，是 UTF-8 —— 用 `Get-Content hud.log -Encoding UTF8` 看，否则中文会显示成乱码。

## 安装

**方式一：直接用 exe（推荐）**

```powershell
# 下载 Releases 里的 状态栏记时器.exe, 然后:
.\状态栏记时器.exe --install-autostart     # 装开机自启
.\状态栏记时器.exe --uninstall-autostart   # 卸载
```

**方式二：从源码跑**

```powershell
.\start.cmd                                # 一键启动
.\.venv\Scripts\pythonw.exe app.py          # 或者手动
```

依赖（`.venv` 里已装好）：`pywin32` `psutil` `pywebview` `bottle` `pillow`。

**自己打包 exe**（需要 PyInstaller）：

```powershell
.\.venv\Scripts\python.exe build_exe.py     # 产物: dist\状态栏记时器.exe, 并复制一份到项目根
```

打包后仍是**多进程**：子进程用同一个 exe 加 `--role tracker|hud|dashboard` 起来
（`app.py` 里的 `_dispatch_frozen_role`）。资源路径与数据目录的统一解析在 `paths.py`。

## 托盘菜单

托盘图标在右下角：

- **Open Dashboard** — 打开管理页面
- **Pause / Resume** — 暂停 / 继续采集
- **Quit** — 退出整个程序（含任务栏 HUD）

双击托盘图标 = 打开管理页面。

## 开机自启

**exe 方式（推荐）**：见上面「安装」里的 `--install-autostart`。

**源码方式**：

```powershell
.\install-autostart.cmd    # 安装
.\uninstall-autostart.cmd  # 卸载
```

注册表项：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\ScreenTimeTracker`
（键名沿用了旧名，程序显示名已改成"状态栏记时器"。）

## 项目结构

```
状态栏记时器/
├── app.py                  # 主程序: TCP server + 拉起各子进程 + 打包后的角色分发
├── paths.py                # 资源目录/数据目录/子进程命令行的统一解析 (源码 & 打包通用)
├── tracker_worker.py       # 采集前台窗口 → SQLite + state.json (含"今日累计"基数)
├── taskbar_hud.py          # 任务栏内嵌计时显示 (本项目的核心)
├── tray.ps1                # PowerShell 托盘图标 + 右键菜单
├── dashboard.py            # 管理页面 (独立子进程): pywebview + bottle/multithread
├── db.py                   # SQLite 数据层 + 管理页面的聚合查询
├── app_icons.py            # 从 exe 抽应用图标 (SHDefExtractIcon + DIB + PNG 缓存)
├── dashboard.html/css/js   # 管理页面前端 (深色, 纯 CSS 画柱状图, 不依赖图表库)
├── build_exe.py            # 打包成单个 exe (PyInstaller)
├── start.cmd               # 源码模式一键启动
├── install-autostart.cmd   # 源码模式: 安装开机自启
├── uninstall-autostart.cmd # 源码模式: 卸载开机自启
├── create_desktop_shortcut.py  # 创建桌面快捷方式 (优先指向 exe)
├── verify_today.py         # 诊断: 核对界面数字 vs 数据库真实汇总 + 格式化回归自检
├── backfill_exe.py         # 维护: 给历史会话回填 exe 路径 (为了图标)
├── probe_taskbar_dock.py   # 诊断: 任务栏结构 + 停靠可行性探测
├── diag_child_pos.py       # 诊断: 子窗口坐标/分层渲染
├── fix_ps1_bom.ps1         # 工具: 给 .ps1 补 UTF-8 BOM
├── seed_demo.py            # 生成模拟数据 (测试用)
├── echarts.min.js          # (旧版图表库, 新页面已不用, 可以删)
├── data/
│   ├── screen_time.db      # SQLite 数据库
│   ├── state.json          # 当前前台状态 (tracker 写, HUD/tray 读)
│   ├── control.json        # 控制标志 (app.py 写)
│   ├── hud_config.json     # 任务栏 HUD 配置
│   ├── hud_pos.json        # 拖动记住的位置
│   └── hud_status.json     # HUD 当前显示内容 (给 --check 读)
└── .venv/                  # 虚拟环境
```

## 数据 Schema

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts TEXT NOT NULL,    -- 'YYYY-MM-DD HH:MM:SS' 本地时间
    end_ts TEXT NOT NULL,
    process TEXT NOT NULL,
    window_title TEXT
);
CREATE INDEX idx_sessions_start ON sessions(start_ts);
CREATE INDEX idx_sessions_process ON sessions(process);
```

每次前台窗口切换就写一行。切换时把上一段落库，退出 / 暂停时也会刷写最后一段。

## API 接口 (dashboard.py 起的 HTTP server)

| Endpoint | 返回 |
|----------|------|
| `GET /api/overview` | `{today_seconds, week_seconds, month_seconds, total_seconds, today_top[]}` |
| `GET /api/today` | `{top_apps[], hourly[24], recent[]}` |
| `GET /api/week` | `{top_apps[], daily[]}` |
| `GET /api/month` | `{top_apps[], daily[]}` |
| `GET /api/status` | `{has_data, total_seconds}` |
| `GET /static/<file>` | 静态文件 (HTML/CSS/JS/ECharts) |

## 已知坑（踩过的，别再踩一遍）

**任务栏 HUD 相关**

1. **`UpdateLayeredWindow` 对子窗口必须传 `pptDst = NULL`。** 传屏幕坐标会被再叠加一次父窗口偏移
   —— 实测子窗口 y=0（屏幕 1528）传 `(x, 1528)` 之后窗口跑到 3056，也就是屏幕外，什么都看不见。
2. **必须声明进程 DPI 感知（`per-monitor-v2`）。** 不声明的话进程看到的是 96 DPI 虚拟坐标，
   而 explorer/任务栏跑在真实 DPI（本机 144）；把"不感知"的窗口 SetParent 给"感知"的任务栏，
   Windows 会给子窗口套一层缩放，窗口尺寸会被乘 `96/144 = 2/3`，位置也全错。
3. **算空闲区间时要过滤掉"几乎整条任务栏宽"的窗口。** 有
   `Windows.UI.Composition.DesktopWindowContentBridge` 这类背景合成层占满全宽，把它们当占用
   就会得出"任务栏全满"。
4. **任务按钮区要递归找。** 它有时是 `ReBarWindow32` 的直接子窗口，有时藏在合成层里，只扫一层会漏。
5. **文字抗锯齿不能用 ClearType。** 逐像素 alpha 的前提是"R 通道 = 覆盖率"，ClearType 的亚像素
   渲染会让 R/G/B 不相等，破坏这个前提 → 用 `ANTIALIASED_QUALITY`。
6. **窗口类要加 `CS_DBLCLKS`**，否则双击只会来两次 `WM_LBUTTONDOWN`。
7. **`WM_MOUSEACTIVATE` 要返回 `MA_NOACTIVATE`**，否则点计时器会抢走当前应用的焦点。

8. **任务栏定位必须"先定位置、再裁内容"。** 反过来的写法（先按最长的空闲段算宽度、再去找放得下这个宽度的区间）
   会"乱跳"：右边空档哪怕只比内容窄 1px，整条就跳到任务栏左半去了。正确顺序是位置只由 `anchor` 决定，
   空间不够就少列几个应用。
9. **轻点一下不能算拖动。** 没有 `drag_threshold_px` 阈值的话，单纯点一下就会写 `data/hud_pos.json`
   并把 `anchor` 钉成 `manual`，位置再也回不到自动贴边（而且拖到边界会留下 `-29` 这种越界坐标）。

**PowerShell / 环境相关**

10. **`.ps1` 必须是 UTF-8 *带 BOM*。** PowerShell 5.1 对无 BOM 的 `.ps1` 按 GBK 解码，中文注释会被
   拆成乱码字节，其中可能吐出 `\` 吃掉后面的引号 → 语法报错且报错位置完全误导
   （`Try 语句缺少自己的 Catch 或 Finally 块`）。`fix_ps1_bom.ps1` 就是干这个的。
11. **venv 的 `pythonw.exe` 是个跳板**，会再拉起 `pythonw3.13.exe`，所以进程列表里每个逻辑进程会看到两条。
   杀进程要两个都杀。
12. **pywebview 6.2.1 winforms backend 的 `js_api` 不工作**（`js_callback` 从未注册）。
    所以 dashboard.py 自己起 bottle HTTP server，前端用 fetch；pywebview 只负责弹窗口。
13. **WebView2 runtime 必须装**（Win 11 默认装了，Win 10 需要手动装一次）。
14. **`FileDescription` 解析**对某些 exe 返回 `None`（Win 11 版本信息格式变化），
    已加 `NAME_MAP` fallback 覆盖常见程序。你的程序显示成原始 exe 名的话，
    把它加到 `tracker_worker.py` 的 `NAME_MAP` 里。
15. **旧的 PowerShell 浮窗 HUD 还留着**，加 `-LegacyHud` 参数才会启用（`tray.ps1` 里）。
    默认关闭，显示交给 `taskbar_hud.py`。

**打包成 exe 相关**

16. **`--onefile` 之后子进程必须用「同一个 exe + `--role`」起来。** 冻结后 `sys.executable`
    就是 exe 本身，再想用「pythonw 脚本」那套就起不来了。子进程那边先按角色分发再进各自的 `main()`
    （`app.py` 的 `_dispatch_frozen_role`），并把 `--role xxx` 从 `sys.argv` 里摘掉，
    否则子模块的 `argparse` 会报未知参数。
17. **`_MEIPASS` 是临时目录（退出即删），只能放只读资源。** 日志、`data/`、图标缓存必须写到
    **exe 旁边**（`paths.app_dir()`），否则每次退出数据就没了。把 exe 放在项目目录里跑，
    还能直接复用源码版的历史数据。
18. **URL 查询串里不要放非 ASCII。** 实测 `?exe=D:\抖音\douyin\douyin.exe` 到 bottle 那层解码
    出来是另一个字符串（`isdir` 直接 False，图标抽不到）；改成 base64url 传就没有任何编码问题，
    顺带也不把用户的真实路径暴露在 URL 里。**任何进程间/HTTP 传路径的场合都优先编码。**

**其他**

19. **`divmod(s, 3600)` 的第二项是「剩余秒数」，不是分钟。** 早先误当分钟用，5610 秒被显示成
    `1h 2010m`。低于一小时不会触发，所以只有累计超一小时才暴露。
    `verify_today.py` 里有回归断言（含 0~40000 秒的兜底扫描）。

## 二次开发

- 改采样间隔：`tracker_worker.py` 顶部 `CHECK_INTERVAL`（秒）
- 加应用名映射：`tracker_worker.py` 的 `NAME_MAP` 字典
- 改"累计"口径 / 加新字段：`tracker_worker.py` 的 `main()`（写 `state.json` 的那一段）+ `db.query_today_totals()`
- 改任务栏显示格式 / 列表项数 / 配色：`data/hud_config.json`（不用改代码）
- 改 HUD 行为：`taskbar_hud.py`，配置项都在 `DEFAULT_CONFIG`
- 加图表 / 改管理页面布局：`dashboard.js` 的 `renderHead` / `renderChart` / `renderApps`，样式在 `dashboard.css`
- 改管理页面的主题色：`dashboard.css` 开头的 `:root` CSS 变量
- 改管理页面的数据口径 / 加板块：`db.py` 的 `page_day()` / `page_week()`
- 图标抽不出来（显示成首字母）：`app_icons.py`，或先跑 `backfill_exe.py` 补 exe 路径
- 加数据导出：写 `export.py`，调 `db.query_top_apps(...)` 输出 CSV/JSON

## License

MIT
