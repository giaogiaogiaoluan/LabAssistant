# 一键打包 Windows 同步服务器 LabAssistantServer.exe（在项目根目录执行）
# 依赖：当前 Python 可 import PySide6 + fastapi + uvicorn + pyinstaller

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/3] 打包 LabAssistantServer.exe ..."
$env:PYTHONPATH = "$root\src"
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "LabAssistantServer" --icon "assets\icon.ico" `
    --add-data "assets;assets" --paths "$root\src" `
    src\server_main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

New-Item -ItemType Directory -Force -Path "release\windows" | Out-Null
Copy-Item -Force "dist\LabAssistantServer.exe" "release\windows\LabAssistantServer.exe"
Write-Host "[2/3] 完成 -> release\windows\LabAssistantServer.exe"
