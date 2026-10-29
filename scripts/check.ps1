$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Command failed with exit code ${LASTEXITCODE}: " +
            "$Executable $($CommandArguments -join ' ')"
        )
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repositoryRoot "backend\.venv\Scripts\python.exe"
$backendPython = if ($env:BACKEND_PYTHON) {
    $env:BACKEND_PYTHON
}
elseif (Test-Path -LiteralPath $venvPython) {
    $venvPython
}
else {
    throw (
        "Missing backend\.venv. Create it with Python 3.12 and install " +
        "backend\requirements.lock, or set BACKEND_PYTHON explicitly."
    )
}
Invoke-Checked -Executable $backendPython -CommandArguments @(
    "-c", "import sys; raise SystemExit(sys.version_info[:2] != (3, 12))"
)
$npmCommand = (Get-Command "npm.cmd" -ErrorAction Stop).Source
$env:APP_ENV = "test"
$env:LLM_PROVIDER = "mock"
$env:SEARCH_PROVIDER = "mock"
$env:EMBEDDING_PROVIDER = "mock"
$env:EVAL_PROVIDER_MODE = "mock"
$env:DATABASE_URL = if ($env:DATABASE_URL) {
    $env:DATABASE_URL
}
else {
    "postgresql+asyncpg://career_buddy:career_buddy_local@localhost:5432/career_buddy_test"
}
$env:JWT_SECRET = if ($env:JWT_SECRET) {
    $env:JWT_SECRET
}
else {
    "local-test-secret-with-at-least-32-characters"
}

Push-Location (Join-Path $repositoryRoot "backend")
try {
    Invoke-Checked -Executable $backendPython -CommandArguments @("-m", "ruff", "check", ".")
    Invoke-Checked -Executable $backendPython -CommandArguments @("-m", "mypy", "app")
    Invoke-Checked -Executable $backendPython -CommandArguments @(
        "-m", "scripts.audit_public_claims"
    )
    Invoke-Checked -Executable $backendPython -CommandArguments @(
        "-m", "alembic", "upgrade", "head"
    )
    Invoke-Checked -Executable $backendPython -CommandArguments @(
        "-m", "alembic", "check"
    )
    Invoke-Checked -Executable $backendPython -CommandArguments @("-m", "pytest")
    Invoke-Checked -Executable $backendPython -CommandArguments @(
        "-m", "scripts.run_eval", "--no-persist"
    )
    Invoke-Checked -Executable $backendPython -CommandArguments @(
        "-m", "evals.v2", "run", "--dataset", "stage5",
        "--provider-mode", "mock",
        "--trial-count", "1", "--require-all-hard-gates"
    )
}
finally {
    Pop-Location
}

Push-Location (Join-Path $repositoryRoot "frontend")
try {
    Invoke-Checked -Executable $npmCommand -CommandArguments @("test")
    Invoke-Checked -Executable $npmCommand -CommandArguments @("run", "build")
}
finally {
    Pop-Location
}
