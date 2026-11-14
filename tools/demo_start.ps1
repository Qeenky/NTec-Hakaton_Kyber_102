# Запуск демо за 60 секунд (Windows PowerShell)
#
#   powershell -ExecutionPolicy Bypass -File tools\demo_start.ps1
#
# Проверяет и поднимает всё, что нужно для показа:
#   1. backend на 8000 (если не запущен)
#   2. adb reverse для USB-режима + keepalive в фоне
#   3. страницу AR-планшета в браузере
#   4. приложение на телефоне

param(
    [string]$Apk = "",
    [string]$Adb = "",
    [string]$Root = "",
    [switch]$SkipBoard
)

$ErrorActionPreference = "Continue"
if (-not $Root) { $Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
if (-not $Apk) { $Apk = Join-Path $Root "release\ntec-inspector.apk" }
if (-not $Adb) { $Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe" }
function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    !!  $text" -ForegroundColor Yellow }

# 1. сервер
Step "Сервер"
$alive = $false
try {
    $h = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 3
    $alive = $true
    Ok "уже работает: детектор $($h.detector), правил $($h.rules_count), БД $($h.database)"
} catch { }

if (-not $alive) {
    Warn "не запущен, поднимаю"
    $py = Join-Path $Root "backend\.venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { Warn "нет venv: $py"; exit 1 }
    Start-Process -FilePath $py `
        -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" `
        -WorkingDirectory (Join-Path $Root "backend") -WindowStyle Minimized
    # Под нагрузкой старт занимает до 40-60 с (импорт OpenCV, миграции, сид).
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        try { $h = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2; $alive = $true; break } catch { }
    }
    if ($alive) { Ok "поднялся за $i с" } else { Warn "не поднялся за 90 с — смотрите окно uvicorn"; exit 1 }
}

# 2. телефон и туннель
Step "Телефон"
$devices = & $Adb devices
if ($devices -match "\sdevice$") {
    Ok "устройство на связи"
    & $Adb reverse tcp:8000 tcp:8000 | Out-Null
    Ok "adb reverse: $((& $Adb reverse --list) -join ', ')"

    # keepalive: туннель иногда сбрасывается сам
    Start-Job -ScriptBlock {
        param($adb)
        while ($true) { & $adb reverse tcp:8000 tcp:8000 2>$null | Out-Null; Start-Sleep -Seconds 2 }
    } -ArgumentList $Adb | Out-Null
    Ok "keepalive запущен в фоне"

    if (Test-Path $Apk) {
        $installed = (& $Adb shell pm list packages) -match "ru.ntec.ntec_inspector"
        if (-not $installed) {
            Warn "приложение не установлено, ставлю"
            & $Adb install -r $Apk | Out-Null
        }
        & $Adb shell am force-stop ru.ntec.ntec_inspector
        & $Adb shell am start -n ru.ntec.ntec_inspector/.MainActivity | Out-Null
        Ok "приложение запущено"
    } else {
        Warn "APK не найден: $Apk"
    }
} else {
    Warn "телефон не виден в adb. Проверьте кабель, режим «Передача файлов» и «Отладка по USB»"
}

# 2б. локальная vision-модель (Ollama)
Step "Локальная модель (Ollama)"
$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (Test-Path $ollama) {
    $api = $false
    try { Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; $api = $true } catch { }
    if (-not $api) {
        Warn "API не отвечает, запускаю ollama serve"
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 1
            try { Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null; $api = $true; break } catch { }
        }
    }
    if ($api) {
        $models = & $ollama list 2>$null
        if ($models -match "qwen2.5vl") {
            Ok "qwen2.5vl:3b на месте"
            # Прогрев: первый ответ всегда медленный (загрузка в VRAM),
            # на сцене этого ждать нельзя. Короткий запрос заранее.
            $sw = [Diagnostics.Stopwatch]::StartNew()
            try {
                $warm = '{"model":"qwen2.5vl:3b","prompt":"ok","stream":false,"options":{"num_predict":1}}'
                Invoke-RestMethod -Method Post "http://127.0.0.1:11434/api/generate" `
                    -ContentType "application/json" -Body $warm -TimeoutSec 120 | Out-Null
                Ok "модель прогрета за $([math]::Round($sw.Elapsed.TotalSeconds,1)) с"
            } catch {
                Warn "прогрев не удался (не критично)"
            }
        }
        else { Warn "модель qwen2.5vl:3b не найдена: ollama pull qwen2.5vl:3b" }
    } else {
        Warn "Ollama не поднялась — канал VLM будет недоступен (остальное работает)"
    }
} else {
    Warn "Ollama не установлена — работаем без VLM"
}

# 3. планшет на экране
if (-not $SkipBoard) {
    Step "AR-планшет"
    $page = Join-Path $Root "ar_board\screen\board_switch.html"
    if (Test-Path $page) {
        Start-Process $page
        Ok "открыт в браузере, нажмите на страницу для полного экрана"
    } else {
        Warn "нет файла $page"
    }
}

Write-Host ""
Write-Host "Готово. Проверка за 10 секунд:" -ForegroundColor Cyan
Write-Host "  1) в приложении сверху зелёное «В сети»"
Write-Host "  2) «Выбрать оборудование вручную» -> карточка PUMP-001"
Write-Host "  3) «Начать осмотр» -> наведите на экран с планшетом -> должны появиться 5 рамок"
Write-Host "  4) завершите осмотр -> «сохранён»"
Write-Host ""
Write-Host "Если что-то не так: docs\runbook.md, раздел «типовые сбои»." -ForegroundColor DarkGray
