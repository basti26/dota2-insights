$ErrorActionPreference = 'Stop'
$insightsRoot = Split-Path -Parent $PSScriptRoot
$pythonCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
}
$insightsPython = $pythonCommand.Source
$entry = Join-Path $PSScriptRoot 'win_insights.py'
Start-Process -FilePath $insightsPython -ArgumentList @('-B', '-X', 'utf8', ('"{0}"' -f $entry)) -WorkingDirectory $insightsRoot -WindowStyle Hidden
