$ErrorActionPreference = 'Stop'
$stage = $env:TIMER_UPDATE_STAGE
$target = $env:TIMER_UPDATE_TARGET
$digest = $env:TIMER_UPDATE_SHA256
$parentId = [int]$env:TIMER_UPDATE_PARENT
$backup = "$stage.bak"
$replaced = $false
$directory = Split-Path -Parent $target

function Start-Timer {
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    Start-Process -FilePath $target -WorkingDirectory (Get-Location).Path
}

try {
    $parent = Get-Process -Id $parentId -ErrorAction SilentlyContinue
    if ($parent -and -not $parent.WaitForExit(20000)) {
        throw '原程序没有及时退出。'
    }
    if ((Get-FileHash -LiteralPath $stage -Algorithm SHA256).Hash -ne $digest) {
        throw '更新文件校验不通过。'
    }
    # 统计页独立运行，更新时先关闭同一 EXE 的可见窗口。
    foreach ($process in @(Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($target)) -ErrorAction SilentlyContinue)) {
        if ($process.Path -eq $target -and $process.MainWindowHandle -ne 0) {
            [void]$process.CloseMainWindow()
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    while (-not $replaced) {
        try {
            [IO.File]::Replace($stage, $target, $backup)
            $replaced = $true
        } catch [System.IO.IOException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw }
            Start-Sleep -Milliseconds 400
        }
    }
    Start-Timer
} catch {
    $reason = $_.Exception.Message
    try {
        if ($replaced -and [IO.File]::Exists($backup)) {
            [IO.File]::Replace($backup, $target, $null)
            $replaced = $false
        }
        if (-not $replaced) { Start-Timer }
    } catch {
        $reason += "`r`n恢复原版本时出错：$($_.Exception.Message)"
    }
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(
        "更新未完成。`r`n$reason`r`n可稍后重试或从 GitHub 下载。",
        '可选更新', 'OK', 'Warning')
} finally {
    if ([IO.File]::Exists($stage)) { [IO.File]::Delete($stage) }
    # 如果恢复失败，留下旧程序备份供恢复。
    if ($replaced -and -not $reason -and [IO.File]::Exists($backup)) {
        [IO.File]::Delete($backup)
    }
}
