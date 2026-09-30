# 一键打包 Windows 客户端 LabAssistant.exe（在项目根目录执行）
# 依赖：当前 Python 可 import PySide6 + pyinstaller（可通过 .\.pylibs 提供）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/3] 打包 LabAssistant.exe ..."
$env:PYTHONPATH = "$root\src"
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "LabAssistant" --icon "assets\icon.ico" `
    --add-data "assets;assets" --paths "$root\src" `
    src\main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

New-Item -ItemType Directory -Force -Path "release\windows" | Out-Null
Copy-Item -Force "dist\LabAssistant.exe" "release\windows\LabAssistant.exe"
Copy-Item -Force "dist\LabAssistant.exe" "LabAssistant.exe"
Write-Host "[2/3] 完成 -> release\windows\LabAssistant.exe"
