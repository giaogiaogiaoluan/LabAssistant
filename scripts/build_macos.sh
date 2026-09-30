#!/usr/bin/env bash
# 在 macOS 上一键构建 LabAssistant.app 与 LabAssistant-<版本>-macOS.dmg
# 用法：bash scripts/build_macos.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
APP_NAME="LabAssistant"
APP_VERSION="$("$ROOT/.venv-mac/bin/python" -c "import sys; sys.path.insert(0,'src'); from labassistant import constants as C; print(C.VERSION)" 2>/dev/null || echo "2.0.0")"
BUNDLE_ID="com.labassistant.desktop"

echo "[0/5] 工具链预检（先确认能打包，再动 dist/，避免把上次的好产物删了）"
# lipo 遇到未接受的 Xcode 许可时会打印错误但**仍然返回 0**，所以只能看输出文本
LIPO_PROBE="$(lipo -info "$(command -v python3)" 2>&1 || true)"
if printf '%s' "$LIPO_PROBE" | grep -qi "license"; then
  echo "❌ 工具链被 Xcode 许可协议挡住了，PyInstaller 无法处理二进制。二选一："
  echo "     sudo xcodebuild -license accept        # 接受许可（继续用 Xcode）"
  echo "   或"
  echo "     sudo xcode-select -s /Library/Developer/CommandLineTools"
  echo "   （后者把默认工具链切回 CLT，装 Xcode 期间也能照常打包 Python 应用）"
  echo "   本次已停止，dist/ 与已安装的应用都没有被动过。"
  exit 1
fi
echo "  lipo 可用（$(xcode-select -p)）"

echo "[1/5] 准备虚拟环境（若不存在）"
if [ ! -d ".venv-mac" ]; then
  python3 -m venv .venv-mac
fi
source .venv-mac/bin/activate
if [ "${LABASSISTANT_SKIP_PIP:-0}" != "1" ]; then
  python -m pip install --upgrade pip -q
  python -m pip install -r requirements.txt -q
else
  echo "  使用现有虚拟环境，跳过网络依赖安装"
fi

echo "[2/5] 生成图标（assets/icon.icns / icon.png / icon.ico）"
export QT_QPA_PLATFORM=offscreen
if [ -f "assets/icon.png" ] && [ -f "assets/icon.icns" ]; then
  echo "  已存在，跳过（需重做请删除 assets/ 后重跑）"
else
  python scripts/make_icons.py "$ROOT/assets/icon.png"
fi

echo "[2.5/5] 打包前自检：语法编译 + 主窗口离屏构造（防止把坏代码打进去）"
export QT_QPA_PLATFORM=offscreen
python -m compileall -q src || { echo "❌ 语法错误，终止打包"; exit 1; }
python - <<'PYCHK'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
app = QApplication([]); app.setStyle("Fusion"); app.setFont(QFont("PingFang SC", 10))
os.environ.setdefault("LABASSISTANT_DATA_DIR", "/tmp/labassistant_buildcheck")
from labassistant.db import Database
from labassistant.ui import theme as T
from labassistant.ui.main_window import MainWindow
app.setStyleSheet(T.QSS)
win = MainWindow(Database())        # 六个页面在这里全部构造，缺符号会立刻暴露
win.show(); app.processEvents()
print("  主窗口与全部页面构造通过")
PYCHK
[ $? -eq 0 ] || { echo "❌ 主窗口构造失败，终止打包"; exit 1; }

echo "[3/5] PyInstaller 构建 .app（macOS windowed）"
# 先把上一次的可用产物挪去备份：这次失败也还能装回来
if [ -d "dist/${APP_NAME}.app" ]; then
  rm -rf "dist/.${APP_NAME}.app.lastgood"
  mv "dist/${APP_NAME}.app" "dist/.${APP_NAME}.app.lastgood"
  echo "  已备份上一次的 dist/${APP_NAME}.app"
fi
rm -rf build "build/${APP_NAME}.app"
python -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name "$APP_NAME" --icon "assets/icon.icns" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --add-data "assets:assets" --paths "$ROOT/src" \
  src/main.py

if [ ! -d "dist/${APP_NAME}.app" ] && [ -d "dist/.${APP_NAME}.app.lastgood" ]; then
  echo "❌ 本次构建没有产出 dist/${APP_NAME}.app，恢复上一次的备份"
  mv "dist/.${APP_NAME}.app.lastgood" "dist/${APP_NAME}.app"
  exit 1
fi
rm -rf "dist/.${APP_NAME}.app.lastgood"
ls "dist/${APP_NAME}.app" >/dev/null

echo "[3.5/5] 润色 Info.plist（版本号 / 显示名 / 分类）"
PLIST="dist/${APP_NAME}.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${APP_VERSION}" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string ${APP_VERSION}" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${APP_VERSION}" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${APP_VERSION}" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName ${APP_NAME}" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string ${APP_NAME}" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.productivity" "$PLIST" 2>/dev/null || true
# 让桌面小组件可以点击唤起主程序（labassistant://today）
/usr/libexec/PlistBuddy -c "Delete :CFBundleURLTypes" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0 dict" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0:CFBundleURLName string com.labassistant.desktop" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string labassistant" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :NSHighResolutionCapable true" "$PLIST" 2>/dev/null || true
touch "dist/${APP_NAME}.app"
SIGN_IDENTITY="${LABASSISTANT_SIGN_IDENTITY:-}"
if [ -z "$SIGN_IDENTITY" ] && [ -d "/Applications/LabAssistantToday.app" ]; then
  SIGN_IDENTITY="$(codesign -dv --verbose=2 \
    /Applications/LabAssistantToday.app/Contents/PlugIns/LabAssistantTodayWidget.appex \
    2>&1 | sed -n 's/^Authority=\(Apple Development:.*\)$/\1/p' | head -n 1)"
  if [ -z "$SIGN_IDENTITY" ]; then
    echo "❌ 已安装小组件，但无法读取它的开发者签名；请设置 LABASSISTANT_SIGN_IDENTITY 后重试"
    exit 1
  fi
fi
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
if ! codesign --force --deep --sign "$SIGN_IDENTITY" "dist/${APP_NAME}.app" >/dev/null 2>&1; then
  echo "❌ 无法使用 $SIGN_IDENTITY 签名主应用；保留旧安装版，避免再次出现其他 App 数据访问提示"
  exit 1
fi
codesign --verify --deep --strict "dist/${APP_NAME}.app"
echo "  已签名：$SIGN_IDENTITY"
echo "  -> dist/${APP_NAME}.app"

echo "[4/5] 组装 DMG 源目录（Applications 快捷方式）"
mkdir -p release/macos
DMG_DIR="build/dmg_src"
rm -rf "$DMG_DIR" && mkdir -p "$DMG_DIR"
cp -R "dist/${APP_NAME}.app" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"

echo "[5/5] hdiutil 生成 DMG"
OUT="release/macos/${APP_NAME}-${APP_VERSION}-macOS.dmg"
if [ "${LABASSISTANT_SKIP_DMG:-0}" = "1" ]; then
  echo "  已按要求跳过 DMG，.app 可直接安装"
else
  rm -f "$OUT"
  hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_DIR" -ov -format UDZO "$OUT" >/dev/null
  echo "完成 -> $OUT"
fi
echo
echo "安装到 /Applications：  cp -R \"dist/${APP_NAME}.app\" /Applications/"
echo "首次打开若被 Gatekeeper 拦截：右键应用 → 打开；或 xattr -dr com.apple.quarantine \"<路径>\""
if [ "$SIGN_IDENTITY" = "-" ]; then
  echo "本机构建使用临时签名；未做开发者公证。"
else
  echo "本机构建使用 Apple Development 签名；未做开发者公证。"
fi
