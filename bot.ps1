# Menjalankan bot di background lewat Windows Task Scheduler.
#   .\bot.ps1 install    pasang: bot jalan otomatis setiap login Windows (tanpa jendela), lalu langsung start
#   .\bot.ps1 uninstall  hapus dari autostart dan hentikan bot
#   .\bot.ps1 start | stop | restart | status
#   .\bot.ps1 log        lihat log terbaru (Ctrl+C untuk keluar)
param([ValidateSet("install", "uninstall", "start", "stop", "restart", "status", "log")]
      [string]$Action = "status")

$ErrorActionPreference = "Stop"
$TaskName = "CVBot"
$Root = $PSScriptRoot
$LogFile = Join-Path $Root "data\bot.log"

function Get-Pythonw {
    $venv = Join-Path $Root ".venv\Scripts\pythonw.exe"
    if (Test-Path $venv) { return $venv }
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) { throw "Python tidak ditemukan di PATH." }
    $pythonw = Join-Path (Split-Path $python) "pythonw.exe"
    if (-not (Test-Path $pythonw)) { throw "pythonw.exe tidak ditemukan di sebelah $python." }
    return $pythonw
}

function Get-BotTask { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

function Stop-Bot {
    if (Get-BotTask) { Stop-ScheduledTask -TaskName $TaskName }
    # Jaga-jaga kalau bot dijalankan manual di luar Task Scheduler
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*bot.py*" -and $_.CommandLine -notlike "*bot.ps1*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

switch ($Action) {
    "install" {
        if (-not (Test-Path (Join-Path $Root ".env"))) { throw "File .env belum ada. Salin dari .env.example lalu isi dulu." }
        $pythonw = Get-Pythonw
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument "bot.py" -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Description "CV Bot Telegram" -Force | Out-Null
        Stop-Bot
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Terpasang. Bot jalan di background dan otomatis start setiap login ($pythonw)."
        Write-Host "Cek log: .\bot.ps1 log"
    }
    "uninstall" {
        Stop-Bot
        if (Get-BotTask) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
        Write-Host "Bot dihentikan dan dihapus dari autostart."
    }
    "start" {
        if (-not (Get-BotTask)) { throw "Belum terpasang. Jalankan dulu: .\bot.ps1 install" }
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Bot dijalankan."
    }
    "stop" { Stop-Bot; Write-Host "Bot dihentikan." }
    "restart" {
        Stop-Bot
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Bot di-restart."
    }
    "status" {
        $task = Get-BotTask
        if (-not $task) { Write-Host "Belum terpasang. Jalankan: .\bot.ps1 install"; break }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Status: $($task.State)  (terakhir jalan: $($info.LastRunTime), hasil: $($info.LastTaskResult))"
        if (Test-Path $LogFile) { Write-Host "`nLog terakhir:"; Get-Content $LogFile -Tail 10 }
    }
    "log" {
        if (-not (Test-Path $LogFile)) { throw "Belum ada log di $LogFile" }
        Get-Content $LogFile -Tail 40 -Wait
    }
}
