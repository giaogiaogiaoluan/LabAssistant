#!/usr/bin/env bash
# 不装 Xcode 也能做的校验：用 Command Line Tools 的 SDK 对两个 target 做类型检查。
# 注意：这只能证明 Swift 代码本身合法，真正的 .appex 打包/签名注册仍需 Xcode。
set -uo pipefail
cd "$(dirname "$0")"
SDK="$(xcrun --show-sdk-path)"
TARGET="arm64-apple-macosx14.0"
fail=0
run() {
  local name="$1"; shift
  if out="$(swiftc -typecheck -sdk "$SDK" -target "$TARGET" "$@" 2>&1)"; then
    printf "✅ %-12s 类型检查通过\n" "$name"
  else
    printf "❌ %-12s 类型检查失败\n" "$name"; echo "$out" | head -20; fail=1
  fi
}
run "小组件" Sources/Widget/LabSnapshot.swift Sources/Widget/LabTodayWidget.swift
run "宿主App" Sources/HostApp/LabAssistantTodayApp.swift Sources/Widget/LabSnapshot.swift
exit $fail
