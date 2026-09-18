[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $projectRoot "dist"
$packageName = "QwenPDF-Layout-Translator-Windows-x64-Portable"
$stagingRoot = Join-Path $env:TEMP ("qwen-pdf-package-" + [guid]::NewGuid().ToString("N"))
$packageRoot = Join-Path $stagingRoot $packageName
$zipPath = Join-Path $distDir ($packageName + ".zip")
$resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$resolvedStage = [IO.Path]::GetFullPath($stagingRoot)

if (-not $resolvedStage.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Split-Path -Leaf $resolvedStage).StartsWith("qwen-pdf-package-")) {
    throw "Unsafe staging directory: $resolvedStage"
}

$required = @(
    "backend",
    "gui",
    "runtime",
    "tools",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "Start.cmd"
)

foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $item))) {
        throw "Missing required package item: $item"
    }
}

New-Item -ItemType Directory -Path $distDir, $packageRoot -Force | Out-Null

try {
    foreach ($item in $required) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $item) -Destination $packageRoot -Recurse -Force
    }
    # Bytecode caches are development artifacts and are not needed at runtime.
    $resolvedPackageRoot = [IO.Path]::GetFullPath($packageRoot).TrimEnd('\') + '\'
    foreach ($cacheDir in Get-ChildItem -LiteralPath $packageRoot -Directory -Filter "__pycache__" -Recurse) {
        $resolvedCache = [IO.Path]::GetFullPath($cacheDir.FullName)
        if (-not $resolvedCache.StartsWith($resolvedPackageRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe cache directory: $resolvedCache"
        }
        Remove-Item -LiteralPath $resolvedCache -Recurse -Force
    }

    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }

    Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath -CompressionLevel Optimal
    $hash = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
    Write-Host "Created: $zipPath"
    Write-Host "Size: $([math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)) MB"
    Write-Host "SHA256: $($hash.Hash)"
}
finally {
    if ($resolvedStage.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedStage).StartsWith("qwen-pdf-package-")) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
