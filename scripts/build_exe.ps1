# 一键打包 LabAssistant.exe（在项目根目录执行：powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1）
# 依赖：Python 环境已安装 PySide6 + pyinstaller（可用 .pylibs 目录或 .venv）

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1) 生成图标（需要 PySide6 可用）
Write-Host "[1/3] 生成图标..."
$env:QT_QPA_PLATFORM = "offscreen"
# 图标已随仓库提供（assets/icon.png / icon.icns / icon.ico）。
# 需重做时：python scripts\make_icons.py "assets\icon_master.png"
if (-not (Test-Path assets\icon.png)) { throw "缺少 assets/icon.png，请先运行 scripts\make_icons.py" }
if ($LASTEXITCODE -ne 0) { throw "make_icon 失败" }

# 2) 让当前 Python 能 import 到 src 与依赖目录
$env:PYTHONPATH = "$root\src"

# 3) PyInstaller 打包（onefile + 无控制台窗口）
Write-Host "[2/3] PyInstaller 打包 LabAssistant.exe（可能需要几分钟）..."
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "LabAssistant" `
    --icon "assets\icon.ico" `
    --add-data "assets;assets" `
    --paths "$root\src" `
    src\main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# 便携版：把 exe 同时复制到项目根目录（数据默认放在 exe 同级的 data\ 里）
Write-Host "[3/3] 复制便携版到项目根目录 ..."
Copy-Item -Force "dist\LabAssistant.exe" "$root\LabAssistant.exe"

Write-Host "完成 -> dist\LabAssistant.exe  与  根目录 LabAssistant.exe（推荐运行这个：数据保存在 exe 旁 data\）"
