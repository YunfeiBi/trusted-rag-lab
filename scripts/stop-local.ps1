$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPidFile = Join-Path $taskRoot '.local/local-processes.json'
if (-not (Test-Path $taskPidFile)) { Write-Output 'No managed process record.'; exit 0 }
$taskSaved = Get-Content -LiteralPath $taskPidFile -Raw | ConvertFrom-Json
if ($taskSaved.root -ne $taskRoot) { throw 'Project root does not match managed process record.' }
foreach ($taskKind in 'backend','frontend','worker') {
    $taskId = $taskSaved.$taskKind
    if (-not $taskId) { continue }
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskId" -ErrorAction SilentlyContinue
    if (-not $taskProcess) { continue }
    if ($taskKind -in 'backend','worker') {
        $taskExpectedPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
        $taskExpectedCommand = if ($taskKind -eq 'worker') { '*-m backend.worker*' } else { '*uvicorn backend.app.main:app*' }
        $taskMatches = $taskProcess.ExecutablePath -eq $taskExpectedPython -and $taskProcess.CommandLine -like $taskExpectedCommand
    } else {
        $taskExpectedVite = Join-Path $taskRoot 'frontend/node_modules/vite/bin/vite.js'
        $taskMatches = $taskProcess.CommandLine -like ('*' + $taskExpectedVite + '*') -and $taskProcess.CommandLine -like '*--port 5173*'
    }
    if (-not $taskMatches) { throw "Process $taskId no longer matches the project. Refusing to stop it." }
    Stop-Process -Id $taskId
}
Remove-Item -LiteralPath $taskPidFile
Write-Output 'Managed project processes stopped.'
