# LabAssistant · 实验室时间与日程助手

LabAssistant 是本地优先的桌面应用，用于记录实验室时间、课程、待办与网站收藏，并提供可选的跨设备同步和 macOS 今日小组件。桌面界面使用 PySide6，数据存储在本机 SQLite 中。

## 功能

- 实验室打卡、课程表、月度统计和每日待办
- 随手记识别日程、网址和账号；密码本使用 AES-256-GCM 加密
- 可选的自托管同步服务器
- macOS WidgetKit 今日小组件（需要自行使用 Apple 开发者身份构建并签名）

## 下载与运行

在 GitHub 仓库页面选择 **Code → Download ZIP**，解压后按下列步骤运行源码。需要 Python 3.10 或更新版本。当前提供的是源码下载，macOS 安装包可按下文自行构建。

```bash
cd LabAssistant-main  # 如果通过 Download ZIP 下载
python3 -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install -r requirements.txt
python src/main.py
```

Windows PowerShell 可将激活命令换为 `.venv-mac\\Scripts\\Activate.ps1`。应用会在首次启动时自行创建空数据库；仓库不提供也不需要任何用户数据库。

```bash
python -m pytest tests -q
```

macOS 可运行 `bash scripts/build_macos.sh` 构建本机 `.app` 和 `.dmg`。Windows 构建脚本在 `scripts/` 目录。小组件的构建步骤见 [widget/README.md](widget/README.md)。同步协议见 [docs/SYNC.md](docs/SYNC.md)。

## 数据与隐私

源码运行时，数据位于项目的 `data/`；macOS 打包版位于 `~/Library/Application Support/LabAssistant/`。可通过 `LABASSISTANT_DATA_DIR` 指定其他数据目录。请勿将数据库、备份、令牌、密钥、日志或小组件快照提交到公开仓库；本仓库的 `.gitignore` 已屏蔽常见路径与扩展名。

同步功能由用户自行部署和配置。密码本数据与随手记原文不参与同步。首次启动可按需加载演示数据。

## 许可证

代码以 [MIT License](LICENSE) 发布。
