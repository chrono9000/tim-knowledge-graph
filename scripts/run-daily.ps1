param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repositoryRoot
try {
    & $Python -m agent run-daily
    $workflowExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $workflowExitCode
