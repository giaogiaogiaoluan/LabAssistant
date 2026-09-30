# macOS 今日实验室小组件

本目录包含 WidgetKit 宿主应用和扩展的源码。小组件显示当天的进度、有效时长、课程与待办；主程序负责生成汇总快照。

## 自行构建

需要 macOS 14 或更新版本、Xcode，以及可用于本机签名的 Apple Development 身份。

```bash
python3 widget/gen_project.py
cd widget
xcodebuild -project LabAssistantToday.xcodeproj -scheme LabAssistantToday \
  -configuration Release -allowProvisioningUpdates \
  DEVELOPMENT_TEAM=<你的 Apple Team ID> \
  CODE_SIGN_IDENTITY="Apple Development" build
```

在 Xcode 的构建产物中找到 `LabAssistantToday.app` 并安装到 `/Applications`。主程序和小组件应使用同一个 Team 签名。安装后启动 LabAssistant 一次，随后在桌面“编辑小组件”中搜索“今日实验室”。

快照保存在当前用户的小组件沙盒容器中。仓库不包含任何真实快照或用户数据库。个人免费 Team 的签名可能会过期，需要自行重新构建。
