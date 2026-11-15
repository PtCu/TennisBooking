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
    throw "找不到 Python。请使用 -Python 指定解释器路径。"
}

& $Python -m nuitka --version
if ($LASTEXITCODE -ne 0) {
    throw "当前 Python 环境没有 Nuitka，请先安装 requirements.txt 和 Nuitka。"
}

Push-Location $root
try {
    & $Python -m nuitka --output-dir=dist --assume-yes-for-downloads src\__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka 打包失败。"
    }
}
finally {
    Pop-Location
}

Write-Host "发布文件已生成：$root\dist\Grounder.exe"
