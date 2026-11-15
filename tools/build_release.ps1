param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path

if (-not $Python) {
    $candidates = @(
        (Join-Path $root "venv\Scripts\python.exe"),
        (Join-Path $root ".venv\Scripts\python.exe"),
        (Join-Path $root ".grounder-venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $Python = $candidate
            break
        }
    }
}

if (-not $Python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        $Python = $command.Source
    }
}

if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw "Python was not found. Use -Python to specify the interpreter path."
}

& $Python -m nuitka --version
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is not installed in the selected Python environment."
}

Push-Location $root
try {
    & $Python -m nuitka --output-dir=dist --assume-yes-for-downloads src\__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Release file created: $root\dist\Grounder.exe"
