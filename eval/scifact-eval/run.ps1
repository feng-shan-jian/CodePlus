[CmdletBinding(DefaultParameterSetName = 'Check')]
param(
    [Parameter(ParameterSetName = 'Check')][switch]$Check,
    [Parameter(Mandatory, ParameterSetName = 'Prepare')][switch]$Prepare,
    [Parameter(Mandatory, ParameterSetName = 'Score')][string]$Score,
    [Parameter(ParameterSetName = 'Score')][string]$RunName = 'baseline'
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Install the isolated evaluation environment: uv sync --project eval/scifact-eval --locked'
}
$arguments = @((Join-Path $PSScriptRoot 'scifact_suite.py'))
if ($Prepare) { $arguments += 'prepare' }
elseif ($Score) { $arguments += @('score', '--input', (Resolve-Path -LiteralPath $Score).Path, '--run-name', $RunName) }
else { $arguments += 'check' }
& $pythonPath -B -X utf8 @arguments
exit $LASTEXITCODE
