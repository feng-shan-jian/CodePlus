# Sole maintained RAG benchmark: official MultiHop-RAG, full corpus in every tier.
[CmdletBinding(DefaultParameterSetName = 'Check')]
param(
    [ValidateSet('all', 'multihop')]
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
    [string]$Replay,
    [ValidateSet('lite', 'medium', 'full')]
    [string]$Tier = 'full',
    [Parameter(Mandatory, ParameterSetName = 'Answers')]
    [string]$Answers
)
$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ParameterSetName -eq 'Retrieval') {
    throw '旧检索引擎已移除，新引擎宿主/评测接入尚未完成。'
}
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Install the project Windows environment: uv sync --locked'
}
$benchmarkEntry = @(Join-Path $PSScriptRoot 'replay.py')
# Resolve caller-relative paths before changing the working directory.
if ($Replay) { $Replay = (Resolve-Path -LiteralPath $Replay).Path }
if ($Answers) { $Answers = (Resolve-Path -LiteralPath $Answers).Path }
Push-Location -LiteralPath $projectRoot
try {
    $checked = & $pythonPath (Join-Path $PSScriptRoot 'check.py') --tier $Tier
    if ($LASTEXITCODE -ne 0) { throw 'Official dataset integrity check failed.' }
    if ($PSCmdlet.ParameterSetName -eq 'Check') {
        $checked
        exit 0
    }
    if ($PSCmdlet.ParameterSetName -eq 'Answers') {
        & $pythonPath (Join-Path $PSScriptRoot 'score.py') --task answers --input $Answers --tier $Tier
        exit $LASTEXITCODE
    }
    $arguments = @($benchmarkEntry) + @('--dataset', $PSScriptRoot)
    if ($Tier -ne 'full') {
        $tiers = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'tiers.json') -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        $arguments += @('--question-ids') + @($tiers.tiers[$Tier].question_ids)
    }
    $arguments += @('--replay', $Replay)
    $runOutput = & $pythonPath @arguments
    $runExitCode = $LASTEXITCODE
    $result = ($runOutput -join "`n") | ConvertFrom-Json -AsHashtable
    if (-not $result.report) { $runOutput; exit $runExitCode }
    $scorePath = Join-Path ([IO.Path]::GetDirectoryName($result.report)) 'upstream-retrieval.json'
    $scoreOutput = & $pythonPath (Join-Path $PSScriptRoot 'score.py') --task retrieval --input $result.report --tier $Tier --output $scorePath
    if ($LASTEXITCODE -ne 0) { throw 'Upstream scoring failed; retain the native report for diagnosis.' }
    $result.upstream_score = $scorePath
    $result.upstream_summary = ($scoreOutput -join "`n") | ConvertFrom-Json -AsHashtable
    $result | ConvertTo-Json -Depth 20
    exit $runExitCode
}
finally { Pop-Location }
