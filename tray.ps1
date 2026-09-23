# Tray daemon (pure .NET, Win 11 right-click menu works).
# 启动参数: $args[0] = port (app.py 的 socket server)
#
# 协议 (newline-delimited JSON):
#   app.py -> tray: {"type": "state", "process": "...", "duration": 5, "running": true, "paused": false}
#   tray   -> app:  {"cmd": "dashboard"|"pause"|"quit"}
#
# 注意: 原来这里还有个"右下角小窗"HUD, 已由 taskbar_hud.py 取代
# (它把文字真正嵌进任务栏, 见 taskbar_hud.py 头部说明).
# 想退回旧的那套浮窗, 加 -LegacyHud 即可.
param([int]$Port = 0, [switch]$LegacyHud)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# === Win 11 AUMID + Shell_NotifyIcon P/Invoke ===
# Win 11 要求每个托盘应用有 AppUserModelID, 否则 shell 静默丢弃 tooltip 内容.
# 用 P/Invoke 设 (PowerShell 没有 .NET 等价 API).
Add-Type -Namespace Win32 -Name TrayApi -MemberDefinition @'
[DllImport("shell32.dll", PreserveSig = false, CharSet = CharSet.Unicode, SetLastError = true)]
public static extern void SetCurrentProcessExplicitAppUserModelID(
    [MarshalAs(System.Runtime.InteropServices.UnmanagedType.LPWStr)] string AppID);

[DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool Shell_NotifyIcon(int dwMessage, ref NOTIFYICONDATA pnid);

[System.Runtime.InteropServices.StructLayout(
    System.Runtime.InteropServices.LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct NOTIFYICONDATA {
    public int cbSize;
    public System.IntPtr hWnd;
    public int uID;
    public int uFlags;
    public int uCallbackMessage;
    public System.IntPtr hIcon;
    [MarshalAs(System.Runtime.InteropServices.UnmanagedType.ByValTStr, SizeConst = 128)]
    public string szTip;
    public int dwState;
    public int dwStateMask;
    [MarshalAs(System.Runtime.InteropServices.UnmanagedType.ByValTStr, SizeConst = 256)]
    public string szInfo;
    [MarshalAs(System.Runtime.InteropServices.UnmanagedType.ByValTStr, SizeConst = 64)]
    public string szInfoTitle;
    public int dwInfoFlags;
    public int uVersion;
    public System.IntPtr hBalloonIcon;
}
'@

# 设 AUMID (必须在 WinForms / NotifyIcon 创建前)
[Win32.TrayApi]::SetCurrentProcessExplicitAppUserModelID('MiniMaxCode.ScreenTimeTracker.1')
Write-Host '[tray] AUMID set'

# ---- TCP client ----
$client = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $Port)
$stream = $client.GetStream()
# UTF-8 NO BOM (默认 .NET UTF8 带 BOM, Python 会 JSON 解析失败)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$reader = New-Object System.IO.StreamReader($stream, $utf8NoBom)
$writer = New-Object System.IO.StreamWriter($stream, $utf8NoBom)
$writer.AutoFlush = $true

function Send-Command($cmd) {
    try {
        $msg = '{"cmd":"' + $cmd + '"}'
        $writer.WriteLine($msg)
        $writer.Flush()
        Write-Host "[tray] sent: $msg"
    } catch {
        Write-Host "[tray] send failed: $_"
    }
}

# ---- NotifyIcon + menu ----
# ExtractAssociatedIcon 单参数版本 (size 参数需要 Size 对象, 这里不用)
$icon = [System.Drawing.Icon]::ExtractAssociatedIcon('C:\Windows\System32\cmd.exe')
$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = $icon
$ni.Text = "Screen Time Tracker"
$ni.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miDash = $menu.Items.Add("Open Dashboard")
$miDash.add_Click({ Write-Host "[tray] menu: dashboard"; Send-Command 'dashboard' })

$miPause = $menu.Items.Add("Pause / Resume")
$miPause.add_Click({ Write-Host "[tray] menu: pause"; Send-Command 'pause' })

$menu.Items.Add("-") | Out-Null

$miQuit = $menu.Items.Add("Quit")
$miQuit.add_Click({ Write-Host "[tray] menu: quit"; Send-Command 'quit' })

$ni.ContextMenuStrip = $menu

# Double click = Open Dashboard
$ni.add_MouseDoubleClick({
    if ($_.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        Send-Command 'dashboard'
    }
})

# ---- Initial balloon tip ----
$ni.BalloonTipTitle = "Screen Time Tracker"
$ni.BalloonTipText = "Right-click tray icon for menu. Double-click = Open Dashboard."
$ni.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
$ni.ShowBalloonTip(5000)

# ---- Always-on-top mini window (像日期时间一样直接显示) ----
# 已被 taskbar_hud.py 取代 (真正嵌进任务栏), 默认不创建.
# Win32 API NotifyIcon.Tooltip 必须 hover 才显示, 没法绕过, 所以当时用这个窗体兜底.
if ($LegacyHud) {
try { [System.IO.File]::AppendAllText($logPath, "[tray] before HUD new`n") } catch {}
$hud = New-Object System.Windows.Forms.Form
$hud.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$hud.TopMost = $true
$hud.ShowInTaskbar = $false
$hud.BackColor = [System.Drawing.Color]::FromArgb(15, 17, 23)
$hud.Opacity = 0.92
$hud.AutoSize = $false
$hud.ClientSize = New-Object System.Drawing.Size(360, 40)
$hud.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual

# 用 AllScreens 找 Primary 屏, 计算右下角坐标. 如果算出来 < 0 或 > 10000 就 fallback 到硬编码.
$primaryScr = ([System.Windows.Forms.Screen]::AllScreens | Where-Object { $_.Primary } | Select-Object -First 1)
if ($null -eq $primaryScr) { $primaryScr = [System.Windows.Forms.Screen]::PrimaryScreen }
$wa = $primaryScr.WorkingArea
$hudX = $wa.Right - $hud.Width - 8
$hudY = $wa.Bottom - $hud.Height - 8
if ($hudX -lt 0 -or $hudX -gt 10000 -or $hudY -lt 0 -or $hudY -gt 10000) {
    # fallback: 用户主屏 1707x1019 working area
    $hudX = 1707 - $hud.Width - 8
    $hudY = 1019 - $hud.Height - 8
    Write-Host "[tray] HUD pos fallback: $hudX,$hudY"
}
$hud.Location = New-Object System.Drawing.Point($hudX, $hudY)

# 自绘文字 (用 Form.Paint + e.Graphics, 不用 Label — Label 在 BorderStyle=None 的 Form 上经常不显示)
$hudBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(230, 230, 230))
$hudFont = New-Object System.Drawing.Font('Segoe UI', 13, [System.Drawing.FontStyle]::Regular)
$hud.add_Paint({
    param($sender, $e)
    $e.Graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $e.Graphics.DrawString($hud.Text, $hudFont, $hudBrush, 10, 9)
})

# 双击 HUD 打开 dashboard
$hud.add_DoubleClick({ Send-Command 'dashboard' })

# === 拖动 HUD ===
# 用户左键拖动 HUD 移动位置, 启动时记忆上次位置 (data\hud_pos.txt)
$script:dragActive = $false
$script:dragStartHudPos = New-Object System.Drawing.Point(0, 0)  # HUD 起始位置 (屏幕)
$script:dragStartMousePos = New-Object System.Drawing.Point(0, 0)  # 鼠标起始位置 (屏幕)
$hudPosFile = Join-Path $PSScriptRoot 'hud_pos.txt'
if (Test-Path $hudPosFile) {
    try {
        $savedPos = Get-Content $hudPosFile -ErrorAction Stop | ForEach-Object { [int]$_ }
        if ($savedPos.Count -eq 2) {
            $hud.Location = New-Object System.Drawing.Point($savedPos[0], $savedPos[1])
        }
    } catch {}
}

$hudLabel = New-Object System.Windows.Forms.Label
$hudLabel.Dock = [System.Windows.Forms.DockStyle]::Fill
$hudLabel.BackColor = [System.Drawing.Color]::Transparent
$hudLabel.Cursor = [System.Windows.Forms.Cursors]::SizeAll
$hud.Controls.Add($hudLabel)

# 用 Cursor.Position (屏幕坐标) + 全 cast 到 int, 避免 PSObject 包装导致的 op_Addition 失败
# 备用 Win32 GetCursorPos (系统 API, 不走 WinForms wrapper)
Add-Type -Namespace Win32 -Name MouseApi -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)]
public struct POINT { public int X; public int Y; }
[DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT lpPoint);
'@

$hudLabel.add_MouseDown({
    param($s, $e)
    if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        $script:dragActive = $true
        $script:dragStartHudPos = New-Object System.Drawing.Point([int]$hud.Left, [int]$hud.Top)
        $p = New-Object Win32.MouseApi+POINT
        [void][Win32.MouseApi]::GetCursorPos([ref]$p)
        $script:dragStartMousePos = New-Object System.Drawing.Point($p.X, $p.Y)
        $hud.Capture = $true
    } elseif ($e.Button -eq [System.Windows.Forms.MouseButtons]::Right) {
        Send-Command 'dashboard'
    }
})
$hudLabel.add_MouseMove({
    param($s, $e)
    if ($script:dragActive) {
        $p = New-Object Win32.MouseApi+POINT
        [void][Win32.MouseApi]::GetCursorPos([ref]$p)
        $dx = $p.X - $script:dragStartMousePos.X
        $dy = $p.Y - $script:dragStartMousePos.Y
        $hud.Location = New-Object System.Drawing.Point(
            $script:dragStartHudPos.X + $dx,
            $script:dragStartHudPos.Y + $dy
        )
    }
})
$hudLabel.add_MouseUp({
    param($s, $e)
    if ($script:dragActive -and $e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
        $script:dragActive = $false
        $hud.Capture = $false
        try {
            "$($hud.Location.X)`n$($hud.Location.Y)" | Out-File -FilePath $hudPosFile -Encoding utf8
        } catch {}
    }
})

# 初始文字
$hud.Text = "Screen Time Tracker"
try { [System.IO.File]::AppendAllText($logPath, "[tray] hud pre-show`n") } catch {}
$hud.Show()
try { [System.IO.File]::AppendAllText($logPath, "[tray] hud shown at $($hud.Location.X),$($hud.Location.Y)`n") } catch {}
$script:lastHudText = ''

# ---- 取消任务栏上方 bar (视觉上跟 Edge tab 冲突) ----
# 只保留右下角 HUD
} else {
    Write-Host "[tray] legacy HUD disabled (taskbar_hud.py 负责显示)"
}

Write-Host "[tray] started, port=$Port"

# ---- Polling reader: WinForms Timer 在 UI 线程上轮询 DataAvailable ----
# 不用 Start-Job (独立 runspace, 拿不到 $reader/$ni) 也不用 .NET Thread (runspace 同问题).
# Timer tick 在 UI 线程, 能直接读写 $reader/$ni.
$logPath = Join-Path $PSScriptRoot 'tray_stdout.log'

# === 方案 B: 旧定义已移到文件顶部 Update-WindowTitle 函数 (此处删掉避免重复 Add-Type) ===

$pollTimer = New-Object System.Windows.Forms.Timer
$pollTimer.Interval = 250  # 4 Hz, 比 app.py 推 2 Hz 频繁点
$pollBuf = ''
$lastText = ''  # 缓存上次 tooltip, 避免每次都 toggle Visible (会闪)

$pollTimer.add_Tick({
    try {
        # 流是否还活着
        if (-not $client.Connected) {
            [System.IO.File]::AppendAllText($logPath, "[tray] disconnected, exit`n")
            $pollTimer.Stop()
            $ni.Visible = $false
            $ni.Dispose()
            [System.Windows.Forms.Application]::Exit()
            return
        }
        # 一次性读所有可用字节到 buffer. NetworkStream.Read() 一次返回所有可读数据,
        # 不会出现 StreamReader.Read() 单字符模式丢字节的问题.
        if ($stream.DataAvailable) {
            $ms = New-Object System.IO.MemoryStream
            $buf = New-Object byte[] 4096
            while ($stream.DataAvailable) {
                $n = $stream.Read($buf, 0, $buf.Length)
                if ($n -le 0) { break }
                $ms.Write($buf, 0, $n)
            }
            $bytes = $ms.ToArray()
            $ms.Dispose()
            if ($bytes.Length -eq 0) { return }
            # UTF-8 解码 (app.py 用 UTF-8 NO BOM 写)
            $text = [System.Text.Encoding]::UTF8.GetString($bytes)
            $pollBuf += $text

            # 按 \n 切完整行
            while ($true) {
                $idx = $pollBuf.IndexOf("`n")
                if ($idx -lt 0) { break }
                $line = $pollBuf.Substring(0, $idx).Trim()
                $pollBuf = $pollBuf.Substring($idx + 1)
                if ($line.Length -eq 0) { continue }

                try {
                    $msg = $line | ConvertFrom-Json
                } catch {
                    [System.IO.File]::AppendAllText($logPath, "[tray] json err: $_ | line=$line`n")
                    continue
                }

                if ($msg.type -eq 'state') {
                    $st = if ($msg.paused) { 'Paused' } elseif (-not $msg.running) { 'Stopped' } else { 'Tracking' }
                    $proc = $msg.process
                    if ($proc) {
                        $text2 = "$proc ($($msg.duration))"
                        if ($msg.window) {
                            $w = $msg.window
                            if ($w.Length -gt 40) { $w = $w.Substring(0, 37) + '...' }
                            $text2 += " - $w"
                        }
                    } else {
                        $text2 = "Screen Time Tracker ($st)"
                    }
                    if ($text2.Length -gt 127) { $text2 = $text2.Substring(0, 124) + '...' }
                    if ($text2 -ne $script:lastText) {
                        $ni.Text = $text2
                        # 旧浮窗 HUD (默认关闭) 才需要同步文字
                        if ($LegacyHud) {
                            if ($hud.InvokeRequired) {
                                $hud.Invoke([Action[string]]{ param($t) $hud.Text = $t; $hud.Invalidate() }, $text2)
                            } else {
                                $hud.Text = $text2
                                $hud.Invalidate()
                            }
                        }
                        $script:lastText = $text2
                        $script:lastHudText = $text2
                    }
                } elseif ($msg.type -eq 'quit') {
                    $pollTimer.Stop()
                    $ni.Visible = $false
                    $ni.Dispose()
                    if ($LegacyHud) {
                        $hud.Close()
                        $hud.Dispose()
                    }
                    [System.Windows.Forms.Application]::Exit()
                    return
                }
            }
        }
    } catch {
        try { [System.IO.File]::AppendAllText($logPath, "[tray] tick err: $_`n") } catch {}
    }
})
$pollTimer.Start()

# ---- Run WinForms message loop ----
[System.Windows.Forms.Application]::Run()

# Cleanup
try { $client.Close() } catch {}
Write-Host "[tray] exited"
