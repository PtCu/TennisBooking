param(
    [string]$Python = "",
    [string]$Node = "",
    [string]$NodeLicense = ""
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

if (-not $Node) {
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCommand) { $Node = $nodeCommand.Source }
}
if (-not $Node -or -not (Test-Path -LiteralPath $Node)) {
    throw "Node.js was not found. Use -Node to specify the executable."
}
if (-not $NodeLicense) {
    $installedLicense = Join-Path (Split-Path $Node) "LICENSE"
    if (Test-Path -LiteralPath $installedLicense) {
        $NodeLicense = $installedLicense
    }
    elseif ((& $Node --version) -eq "v24.14.0") {
        $NodeLicense = Join-Path $root "tools\licenses\node-24.14.0.txt"
    }
}
if (-not $NodeLicense -or -not (Test-Path -LiteralPath $NodeLicense)) {
    throw "Supply the matching Node.js license with -NodeLicense."
}

& $Python -m nuitka --version
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is not installed in the selected Python environment."
}

Push-Location $root
try {
    & $Python -m nuitka --output-dir=dist --assume-yes-for-downloads "--include-data-files=$Node=runtime/node.exe" "--include-data-files=$NodeLicense=runtime/Node-LICENSE.txt" src\__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Release file created: $root\dist\Grounder.exe"
