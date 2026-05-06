$ErrorActionPreference = "Stop"

$rootDir = Split-Path -Parent $PSScriptRoot
Set-Location $rootDir
$browserStagingDir = Join-Path $rootDir "build/playwright-browsers"

uv sync --extra dev
if (Test-Path $browserStagingDir) {
    Remove-Item -Path $browserStagingDir -Recurse -Force
}
$env:PLAYWRIGHT_BROWSERS_PATH = $browserStagingDir
uv run playwright install chromium
uv run python packaging/playwright_browsers.py clear-package-local
uv run pyinstaller --noconfirm --clean packaging/160grab.spec
$env:PLAYWRIGHT_BROWSERS_PATH = ""
uv run python packaging/playwright_browsers.py sync-into-bundle --source $browserStagingDir --bundle-root "$rootDir/dist/160Grab/_internal"
uv run python packaging/build_release.py --platform windows --arch x64
