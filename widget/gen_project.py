#!/usr/bin/env python3
"""生成 LabAssistantToday.xcodeproj（不依赖 XcodeGen / brew）。

pbxproj 是 Xcode 的工程文件格式，手写易错，所以这里用一个确定性生成器产出：
每次运行都会重新分配同样的对象 ID（内容寻址），diff 友好，也能随时改完重跑。

    python3 widget/gen_project.py
    cd widget && xcodebuild -list -project LabAssistantToday.xcodeproj
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJ = ROOT / "LabAssistantToday.xcodeproj"

APP_NAME = "LabAssistantToday"
EXT_NAME = "LabAssistantTodayWidget"
APP_BUNDLE_ID = "com.labassistant.desktop.host"
EXT_BUNDLE_ID = "com.labassistant.desktop.host.widget"
DEPLOY = "14.0"

APP_SOURCES = ["Sources/HostApp/LabAssistantTodayApp.swift"]
# 快照解码器两边共用：宿主 App 的“是否读到快照”提示也要用它
APP_SOURCES.append("Sources/Widget/LabSnapshot.swift")
EXT_SOURCES = ["Sources/Widget/LabSnapshot.swift", "Sources/Widget/LabTodayWidget.swift"]

_ids: dict[str, str] = {}


def oid(key: str) -> str:
    """内容寻址的 24 位十六进制 ID：同一逻辑对象每次生成 ID 一致。"""
    if key not in _ids:
        _ids[key] = hashlib.sha1(key.encode()).hexdigest()[:24].upper()
    return _ids[key]


def rel(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")


def build_files(sources: list[str], target_key: str) -> list[str]:
    return [oid(f"bf:{target_key}:{s}") for s in sources]


def file_refs(paths: list[str]) -> str:
    out = ["/* Begin PBXFileReference section */"]
    for p in paths:
        name = Path(p).name
        out.append(
            f"\t\t{oid('fr:' + p)} /* {name} */ = {{isa = PBXFileReference; "
            f"lastKnownFileType = {ftype(p)}; name = {q(name)}; path = {q(rel(p))}; "
            f"sourceTree = \"<group>\"; }};")
    out.append("/* End PBXFileReference section */")
    return "\n".join(out)


def ftype(path: str) -> str:
    ext = Path(path).suffix
    return {".swift": "sourcecode.swift",
            ".plist": "text.plist.xml",
            ".entitlements": "text.plist.entitlements"}.get(ext, "file")


def q(value: str) -> str:
    """pbxproj 里带路径/特殊字符的值要加引号。"""
    if any(c in value for c in " -/().,\"'"):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def section(isa: str, entries: list[str]) -> str:
    if not entries:
        return ""
    body = "\n".join(entries)
    return f"/* Begin {isa} section */\n{body}\n/* End {isa} section */"


def product_ref(name: str, ftype_: str, bundle: str) -> str:
    return (f"\t\t{oid('prod:' + name)} /* {name} */ = {{isa = PBXFileReference; "
            f"explicitFileType = wrapper.{ftype_}; includeInIndex = 0; path = {q(name)}; "
            f"sourceTree = BUILT_PRODUCTS_DIR; productBundleIdentifier = {q(bundle)}; }};")


def build() -> str:
    all_sources = sorted(set(APP_SOURCES + EXT_SOURCES))
    other_files = [
        "Sources/HostApp/Info.plist",
        "Sources/HostApp/LabAssistantToday.entitlements",
        "Sources/Widget/Info.plist",
        "Sources/Widget/LabAssistantTodayWidget.entitlements",
    ]

    # ---- PBXBuildFile：每个源文件在所属 target 里一条
    bf_entries = []
    for s in all_sources:
        for t in ("app", "ext"):
            srcs = APP_SOURCES if t == "app" else EXT_SOURCES
            if s in srcs:
                bf_entries.append(
                    f"\t\t{oid(f'bf:{t}:{s}')} /* {Path(s).name} in Sources */ = "
                    f"{{isa = PBXBuildFile; fileRef = {oid('fr:' + s)} /* {Path(s).name} */; }};")
    # 把 appex 嵌入宿主 App 的 PlugIns 目录
    bf_entries.append(
        f"\t\t{oid('bf:embed')} /* {EXT_NAME}.appex in Embed App Extensions */ = "
        f"{{isa = PBXBuildFile; fileRef = {oid('prod:' + EXT_NAME + '.appex')} /* "
        f"{EXT_NAME}.appex */; settings = {{ATTRIBUTES = (RemoveHeadersOnName, ); }}; }};")

    # ---- 源文件编译阶段
    def sources_phase(target_key: str, sources: list[str]) -> str:
        files = ", ".join(f"{oid(f'bf:{target_key}:{s}')}" for s in sources)
        return (f"\t\t{oid('sources:' + target_key)} /* Sources */ = {{isa = PBXSourcesBuildPhase; "
                f"buildActionMask = 2147483647; files = ({files}); "
                f"runOnlyForDeploymentPostprocessing = 0; }};")

    # ---- 配置清单
    def cfg(tid: str, name: str, extra: dict[str, str]) -> str:
        base = {
            "SWIFT_VERSION": '"5.9"',
            "MACOSX_DEPLOYMENT_TARGET": q(DEPLOY),
            "SDKROOT": macosx,
            "ENABLE_STRICT_OBJC_MSGSEND": "YES",
            "CODE_SIGN_STYLE": Automatic,
            "DEVELOPMENT_TEAM": '""',
            "COMBINE_HIDPI_IMAGES": "YES",
            "SWIFT_OPTIMIZATION_LEVEL": ("-O" if name == "Release" else "-Onone"),
            "ONLY_ACTIVE_ARCH": ("NO" if name == "Release" else "YES"),
        }
        base.update(extra)
        lines = "\n".join(f"\t\t\t\t{k} = {v};" for k, v in sorted(base.items()))
        return (f"\t\t{oid('cfg:' + tid + ':' + name)} /* {name} */ = {{isa = XCBuildConfiguration; "
                f"buildSettings = {{\n{lines}\n\t\t\t}}; name = {name}; }};")

    macosx = "macosx"
    Automatic = "Automatic"

    app_extra = {
        "PRODUCT_BUNDLE_IDENTIFIER": q(APP_BUNDLE_ID),
        "PRODUCT_NAME": q(APP_NAME),
        "INFOPLIST_FILE": q("Sources/HostApp/Info.plist"),
        "CODE_SIGN_ENTITLEMENTS": q("Sources/HostApp/LabAssistantToday.entitlements"),
        "GENERATE_INFOPLIST_FILE": "NO",
        "LD_RUNPATH_SEARCH_PATHS": '("$(inherited)", "@executable_path/../Frameworks")',
        "MARKETING_VERSION": '"2.0.1"',
        "CURRENT_PROJECT_VERSION": "7",
    }
    ext_extra = {
        "PRODUCT_BUNDLE_IDENTIFIER": q(EXT_BUNDLE_ID),
        "PRODUCT_NAME": q(EXT_NAME),
        "INFOPLIST_FILE": q("Sources/Widget/Info.plist"),
        "CODE_SIGN_ENTITLEMENTS": q("Sources/Widget/LabAssistantTodayWidget.entitlements"),
        "GENERATE_INFOPLIST_FILE": "NO",
        "SKIP_INSTALL": "YES",
        "LD_RUNPATH_SEARCH_PATHS": '("$(inherited)", "@executable_path/../Frameworks", '
                                   '"@loader_path/../Frameworks")',
        "MARKETING_VERSION": '"2.0.1"',
        "CURRENT_PROJECT_VERSION": "7",
    }

    sections = [
        section("PBXBuildFile", bf_entries),

        section("PBXContainerItemProxy", [
            f"\t\t{oid('proxy')} /* PBXContainerItemProxy */ = {{isa = PBXContainerItemProxy; "
            f"containerPortal = {oid('project')} /* Project object */; proxyType = 1; "
            f"remoteGlobalIDString = {oid('target:ext')}; remoteInfo = {q(EXT_NAME)}; }};"]),

        section("PBXCopyFilesBuildPhase", [
            f"\t\t{oid('copy:embed')} /* Embed App Extensions */ = {{isa = PBXCopyFilesBuildPhase; "
            f"buildActionMask = 2147483647; dstPath = \"\"; dstSubfolderSpec = 13; "
            f"files = ({oid('bf:embed')} /* {EXT_NAME}.appex in Embed App Extensions */); "
            f"name = \"Embed App Extensions\"; runOnlyForDeploymentPostprocessing = 0; }};"]),

        section("PBXFileReference", [
            *file_refs(all_sources + other_files).splitlines()[1:-1],
            product_ref(APP_NAME + ".app", "application", APP_BUNDLE_ID),
            product_ref(EXT_NAME + ".appex", "app-extension", EXT_BUNDLE_ID)]),

        section("PBXFrameworksBuildPhase", [
            f"\t\t{oid('fw:app')} /* Frameworks */ = {{isa = PBXFrameworksBuildPhase; "
            f"buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};",
            f"\t\t{oid('fw:ext')} /* Frameworks */ = {{isa = PBXFrameworksBuildPhase; "
            f"buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};"]),

        section("PBXGroup", [
            # 产物只放在 Products 组里；重复挂到根组会触发 Xcode 的 malformed 警告
            f"\t\t{oid('group:root')} = {{isa = PBXGroup; children = ("
            f"{oid('group:sources')}, {oid('group:products')}); "
            f"sourceTree = \"<group>\"; }};",
            f"\t\t{oid('group:sources')} /* Sources */ = {{isa = PBXGroup; children = ("
            + ", ".join(f"{oid('fr:' + s)} /* {Path(s).name} */"
                        for s in sorted(all_sources + other_files))
            + f"); name = Sources; sourceTree = \"<group>\"; }};",
            f"\t\t{oid('group:products')} /* Products */ = {{isa = PBXGroup; children = ("
            f"{oid('prod:' + APP_NAME + '.app')} /* {APP_NAME}.app */, "
            f"{oid('prod:' + EXT_NAME + '.appex')} /* {EXT_NAME}.appex */); "
            f"name = Products; sourceTree = \"<group>\"; }};"]),

    ]

    # ---- resources / sources phases
    sections.append(section("PBXResourcesBuildPhase", [
        f"\t\t{oid('res:app')} /* Resources */ = {{isa = PBXResourcesBuildPhase; "
        f"buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};",
        f"\t\t{oid('res:ext')} /* Resources */ = {{isa = PBXResourcesBuildPhase; "
        f"buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};"]))
    sections.append(section("PBXSourcesBuildPhase", [
        sources_phase("app", APP_SOURCES), sources_phase("ext", EXT_SOURCES)]))

    # ---- targets
    sections.append(section("PBXNativeTarget", [
        f"\t\t{oid('target:app')} /* {APP_NAME} */ = {{isa = PBXNativeTarget; "
        f"buildConfigurationList = {oid('conflist:app')} /* Build configuration list for "
        f"PBXNativeTarget \"{APP_NAME}\" */; buildPhases = ({oid('sources:app')}, "
        f"{oid('fw:app')}, {oid('res:app')}, {oid('copy:embed')}); "
        f"buildRules = (); dependencies = ({oid('dep:app')} /* PBXTargetDependency */); "
        f"name = {q(APP_NAME)}; productName = {q(APP_NAME)}; "
        f"productReference = {oid('prod:' + APP_NAME + '.app')} /* {APP_NAME}.app */; "
        f"productType = \"com.apple.product-type.application\"; }};",
        f"\t\t{oid('target:ext')} /* {EXT_NAME} */ = {{isa = PBXNativeTarget; "
        f"buildConfigurationList = {oid('conflist:ext')} /* Build configuration list for "
        f"PBXNativeTarget \"{EXT_NAME}\" */; buildPhases = ({oid('sources:ext')}, "
        f"{oid('fw:ext')}, {oid('res:ext')}); buildRules = (); dependencies = (); "
        f"name = {q(EXT_NAME)}; productName = {q(EXT_NAME)}; "
        f"productReference = {oid('prod:' + EXT_NAME + '.appex')} /* {EXT_NAME}.appex */; "
        f"productType = \"com.apple.product-type.app-extension\"; }};"]))

    sections.append(section("PBXProject", [
        f"\t\t{oid('project')} /* Project object */ = {{isa = PBXProject; attributes = "
        f"{{BuildIndependentTargetsInParallel = YES; LastSwiftUpdateCheck = 1500; "
        f"LastUpgradeCheck = 1500; TargetAttributes = {{{oid('target:app')} = "
        f"{{CreatedOnToolsVersion = 15.0; }}; {oid('target:ext')} = "
        f"{{CreatedOnToolsVersion = 15.0; }}; }}; }}; "
        f"buildConfigurationList = {oid('conflist:project')}; "
        f"compatibilityVersion = \"Xcode 14.0\"; developmentRegion = zh_CN; "
        f"hasScannedForEncodings = 0; knownRegions = (zh_CN, en, Base); "
        f"mainGroup = {oid('group:root')}; productRefGroup = {oid('group:products')} "
        f"/* Products */; projectDirPath = \"\"; projectRoot = \"\"; targets = ("
        f"{oid('target:app')} /* {APP_NAME} */, {oid('target:ext')} /* {EXT_NAME} */); }};"]))

    sections.append(section("PBXTargetDependency", [
        f"\t\t{oid('dep:app')} /* PBXTargetDependency */ = {{isa = PBXTargetDependency; "
        f"target = {oid('target:ext')} /* {EXT_NAME} */; targetProxy = {oid('proxy')} "
        f"/* PBXContainerItemProxy */; }};"]))

    sections.append(section("XCBuildConfiguration", [
        cfg("project", "Debug", {}), cfg("project", "Release", {}),
        cfg("app", "Debug", app_extra), cfg("app", "Release", app_extra),
        cfg("ext", "Debug", ext_extra), cfg("ext", "Release", ext_extra)]))

    def conflist(tid: str, label: str) -> str:
        return (f"\t\t{oid('conflist:' + tid)} /* {label} */ = {{isa = XCConfigurationList; "
                f"buildConfigurations = ({oid('cfg:' + tid + ':Debug')} /* Debug */, "
                f"{oid('cfg:' + tid + ':Release')} /* Release */); defaultConfigurationIsVisible = 0; "
                f"defaultConfigurationName = Release; }};")

    sections.append(section("XCConfigurationList", [
        conflist("project", "Build configuration list for PBXProject \"" + APP_NAME + "\""),
        conflist("app", "Build configuration list for PBXNativeTarget \"" + APP_NAME + "\""),
        conflist("ext", "Build configuration list for PBXNativeTarget \"" + EXT_NAME + "\"")]))

    body = "\n\n".join(x for x in sections if x)
    return ("// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {\n\t};\n"
            "\tobjectVersion = 56;\n\tobjects = {\n\n"
            + body
            + "\n\n\t};\n rootObject = " + oid('project')
            + " /* Project object */;\n}\n")


def main() -> int:
    PROJ.mkdir(parents=True, exist_ok=True)
    out = PROJ / "project.pbxproj"
    out.write_text(build(), encoding="utf-8")
    print(f"已生成 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
