# Windows Görev Zamanlayıcı (yönetici izni gerekmez): her 1 dakikada bir "run.py tick" çalıştırır, pencere açmaz (pythonw).
# Bilgisayar yeniden başlayınca görev devam eder; kapalıyken gelen DM'ler açılışta taranır, kaçan yayın slotları telafi edilir.
# Kur:    powershell -ExecutionPolicy Bypass -File batch\auto\install_task.ps1
# Kaldır: schtasks /Delete /TN ClaudeVideoAuto /F
$py = (Get-Command pythonw).Source
$run = Join-Path $PSScriptRoot "run.py"
schtasks /Create /SC MINUTE /MO 1 /TN "ClaudeVideoAuto" /TR "`"$py`" `"$run`" tick" /F
if ($LASTEXITCODE -ne 0) { Write-Error "Görev kurulamadı"; exit 1 }
Write-Host "Görev kuruldu: ClaudeVideoAuto (1 dk'da bir)"

