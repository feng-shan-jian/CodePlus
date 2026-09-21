# The only maintained RAG quality dataset. Product CLI contracts remain unchanged.
[CmdletBinding(DefaultParameterSetName = 'Check')]
param(
    [ValidateSet('all', 'original', 'crud', 'multihop', 'openrag', 'rgb', 'smoke')]
    [string]$Dataset = 'all',
    [Parameter(ParameterSetName = 'Check')]
    [switch]$Check,
    [Parameter(Mandatory, ParameterSetName = 'Retrieval')]
    [string]$KbId,
    [Parameter(ParameterSetName = 'Retrieval')]
    [ValidateSet('dense', 'bm25', 'hybrid')]
    [string]$Mode = 'hybrid',
    [Parameter(ParameterSetName = 'Retrieval')]
    [switch]$ManagedLocal,
    [Parameter(Mandatory, ParameterSetName = 'Replay')]
    [string]$Replay
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Install the project Windows environment: uv sync --locked --extra knowledge'
}
if ($Dataset -eq 'all' -and $PSCmdlet.ParameterSetName -ne 'Check') {
    throw 'Choose one dataset and its dedicated base: -Dataset original|crud|multihop|openrag|smoke'
}
if ($Dataset -eq 'rgb' -and $PSCmdlet.ParameterSetName -ne 'Check') {
    throw 'RGB uses assigned context per case. Corpus retrieval would invalidate its no-answer and noise protocol.'
}
$datasetPath = if ($Dataset -eq 'smoke') { Join-Path $PSScriptRoot 'smoke' } else { Join-Path $PSScriptRoot "full/$Dataset" }
$arguments = @('-m', 'codeplus.knowledge', 'benchmark', '--dataset', $datasetPath)
switch ($PSCmdlet.ParameterSetName) {
    'Retrieval' {
        $arguments += @('--kb-id', $KbId, '--mode', $Mode)
        if ($Mode -eq 'hybrid') { $arguments += @('--candidates', '50', '--rrf-k', '60') }
        if ($ManagedLocal) { $arguments += '--managed-local' }
    }
    'Replay' { $arguments += @('--replay', (Resolve-Path -LiteralPath $Replay).Path) }
    default {
        if ($Dataset -eq 'all') { $arguments = @(Join-Path $PSScriptRoot 'check.py') }
        else { $arguments += '--check' }
    }
}
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath @arguments
    $runExitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $runExitCode
