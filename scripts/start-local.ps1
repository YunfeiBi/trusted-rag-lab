$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
foreach ($taskPort in 8000,5173) {
    $taskListener = Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue
    if ($taskListener) { throw "Port $taskPort is already occupied. Stop the prior project process or use its existing URL." }
}
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskNode = (Get-Command node.exe -ErrorAction Stop).Source
$taskVite = Join-Path $taskRoot 'frontend/node_modules/vite/bin/vite.js'
if (-not (Test-Path $taskPython) -or -not (Test-Path $taskVite)) { throw 'Install the locked Python and frontend dependencies first.' }
$taskLog = Join-Path $taskRoot '.local'
New-Item -ItemType Directory -Force -Path $taskLog | Out-Null
$taskBackend = Start-Process -FilePath $taskPython -ArgumentList '-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-access-log' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLog 'backend.out.log') -RedirectStandardError (Join-Path $taskLog 'backend.err.log')
try {
    $taskWorker = Start-Process -FilePath $taskPython -ArgumentList '-m backend.worker' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLog 'worker.out.log') -RedirectStandardError (Join-Path $taskLog 'worker.err.log')
    $taskFrontend = Start-Process -FilePath $taskNode -ArgumentList ('"' + $taskVite + '" --host 127.0.0.1 --port 5173 --strictPort') -WorkingDirectory (Join-Path $taskRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLog 'frontend.out.log') -RedirectStandardError (Join-Path $taskLog 'frontend.err.log')
} catch {
    Stop-Process -Id $taskBackend.Id -ErrorAction SilentlyContinue
    if ($taskWorker) { Stop-Process -Id $taskWorker.Id -ErrorAction SilentlyContinue }
    throw
}
@{backend=$taskBackend.Id; frontend=$taskFrontend.Id; worker=$taskWorker.Id; root=$taskRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskLog 'local-processes.json')
$taskReady = $false
for ($taskAttempt=0; $taskAttempt -lt 30; $taskAttempt++) {
    try {
        $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health/ready' -TimeoutSec 1
        $taskPage = Invoke-WebRequest -Uri 'http://127.0.0.1:5173' -TimeoutSec 1
        if ($taskHealth.status -eq 'ok' -and $taskPage.StatusCode -eq 200) { $taskReady=$true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $taskReady) { throw 'Project processes were launched but readiness failed; inspect .local logs and run stop-local.ps1 before retrying.' }
if ($taskWorker.HasExited) { throw 'Worker exited during startup; inspect .local/worker.err.log.' }
Write-Output 'Frontend: http://127.0.0.1:5173  API docs: http://127.0.0.1:8000/docs'
Write-Output 'PIDs and logs are in .local. See docs/首批验收.md for login and shutdown instructions.'
