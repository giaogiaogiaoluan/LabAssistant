//
//  LabTodayWidget.swift
//  LabAssistant 的 macOS 桌面小方格：只显示今天。
//
//  布局（systemSmall）：
//      22  9月 · 周二          ← 日期 + 状态
//      ▪ 81%   6.5 / 8h        ← 今日方格进度 + 有效时长
//      08:00 示例课程 · 教学楼A ← 下一节课 / 今日安排
//      ○ 3 项待办               ← 待办计数
//

import SwiftUI
import WidgetKit

// ---------------------------------------------------------------- 配色
// LabOS Platinum colors follow the desktop app's semantic palette.
enum LabPalette {
    static func color(_ light: String, _ dark: String, _ scheme: ColorScheme) -> Color {
        rgb(scheme == .dark ? dark : light)
    }

    static func rgb(_ hex: String) -> Color {
        var s = hex
        if s.hasPrefix("#") { s.removeFirst() }
        var value: UInt64 = 0
        Scanner(string: s).scanHexInt64(&value)
        let r = Double((value >> 16) & 0xFF) / 255.0
        let g = Double((value >> 8) & 0xFF) / 255.0
        let b = Double(value & 0xFF) / 255.0
        return Color(.sRGB, red: r, green: g, blue: b, opacity: 1)
    }

    static func tint(for snap: LabSnapshot, _ scheme: ColorScheme) -> Color {
        switch snap.statusKey {
        case "done":           return color("18624E", "87D6AA", scheme)
        case "partial":        return color("875716", "F5CB82", scheme)
        case "holiday",
             "holiday_done":   return color("735A9E", "C5ABE8", scheme)
        case "weekend_done":   return color("4E639C", "A9BCEB", scheme)
        case "weekend":        return color("586168", "BBC5C6", scheme)
        default:               return color("586168", "BBC5C6", scheme)
        }
    }

    static let accent = rgb("145267")
}

// ---------------------------------------------------------------- Timeline
struct LabEntry: TimelineEntry {
    let date: Date
    let snapshot: LabSnapshot?

    /// 下一次刷新：App 写好的 5 分钟检查点，兜底 5 分钟后
    var nextDate: Date {
        if let r = snapshot?.refreshDate, r > date { return r }
        let cal = Calendar.current
        let soon = cal.date(byAdding: .minute, value: 5, to: date) ?? date.addingTimeInterval(300)
        let midnight = cal.startOfDay(for: date.addingTimeInterval(24 * 3600))
        return min(soon, midnight)
    }
}

struct LabProvider: TimelineProvider {
    private func entry(at date: Date = Date()) -> LabEntry {
        LabEntry(date: date, snapshot: LabSnapshotLoader.load())
    }

    func placeholder(in context: Context) -> LabEntry { entry() }

    func getSnapshot(in context: Context, completion: @escaping (LabEntry) -> Void) {
        completion(entry())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<LabEntry>) -> Void) {
        let first = entry()
        // 跨天时先补一个“今天”的条目，保证零点后显示的是新的一天
        var entries: [LabEntry] = [first]
        let cal = Calendar.current
        // 即使 LabAssistant 关闭，课程结束时也让 Widget 重新计算“下一节课”。
        if let snap = first.snapshot {
            for course in snap.courses {
                let parts = course.end.split(separator: ":").compactMap { Int($0) }
                guard parts.count == 2,
                      let boundary = cal.date(bySettingHour: parts[0], minute: parts[1], second: 1,
                                              of: first.date), boundary > first.date else { continue }
                entries.append(LabEntry(date: boundary, snapshot: snap))
            }
        }
        let midnight = cal.startOfDay(for: first.date.addingTimeInterval(24 * 3600))
        if midnight.timeIntervalSince(first.date) < 25 * 3600 {
            entries.append(LabEntry(date: midnight, snapshot: nil))
        }
        completion(Timeline(entries: entries, policy: .after(first.nextDate)))
    }
}

// ---------------------------------------------------------------- 视图
struct LabTodayView: View {
    @Environment(\.colorScheme) private var scheme
    var entry: LabEntry

    var body: some View {
        Group {
            if let snap = entry.snapshot {
                content(snap)
            } else {
                empty
            }
        }
        .containerBackground(for: .widget) {
            LabPalette.color("F7F5EE", "252C31", scheme)
        }
    }

    private func content(_ snap: LabSnapshot) -> some View {
        let tint = LabPalette.tint(for: snap, scheme)
        return VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text("\(snap.dayNum)")
                    .font(.system(size: 26, weight: .bold, design: .monospaced))
                    .foregroundStyle(scheme == .dark ? .white : LabPalette.rgb("23292B"))
                VStack(alignment: .leading, spacing: 0) {
                    Text("\(snap.monthCn) · \(snap.weekdayCn)")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.secondary)
                    Text(snap.holidayName?.isEmpty == false ? snap.holidayName! : snap.statusLabel)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(tint)
                        .lineLimit(1)
                        .minimumScaleFactor(0.7)
                }
                Spacer(minLength: 0)
                HStack(spacing: 2) {
                    ForEach(["B84F44", "B87923", "27866A", "4E639C"], id: \.self) { hex in
                        Rectangle().fill(LabPalette.rgb(hex)).frame(width: 4, height: 4)
                    }
                }
            }

            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(snap.percentText)
                        .font(.system(size: 11, weight: .bold, design: .monospaced))
                        .monospacedDigit()
                        .foregroundStyle(tint)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                    ForEach(0..<2, id: \.self) { row in
                        HStack(spacing: 1) {
                            ForEach(0..<10, id: \.self) { col in
                                Rectangle()
                                    .fill(Double(row * 10 + col) < min(max(snap.ratio, 0), 1) * 20
                                          ? tint : tint.opacity(0.16))
                                    .frame(width: 4, height: 6)
                            }
                        }
                    }
                }
                .frame(width: 49, height: 42, alignment: .leading)

                VStack(alignment: .leading, spacing: 0) {
                    Text("\(snap.effectiveHoursText)h")
                        .font(.system(size: 18, weight: .bold, design: .monospaced))
                        .monospacedDigit()
                        .foregroundStyle(scheme == .dark ? .white : LabPalette.rgb("23292B"))
                    if snap.requiredMin > 0 {
                        Text("目标 \(snap.requiredHoursText)h")
                            .font(.system(size: 9.5))
                            .foregroundStyle(.secondary)
                    } else {
                        Text(snap.kind == "weekend" ? "周末不设目标" : "节假日不设目标")
                            .font(.system(size: 9.5))
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
            }

            Spacer(minLength: 2)

            VStack(alignment: .leading, spacing: 2) {
                Text(footerLine(snap))
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(footerColor(snap, tint))
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                Text(secondLine(snap))
                    .font(.system(size: 9.5))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
            }

            if snap.isStale {
                Text("快照未更新 · 打开 LabAssistant")
                    .font(.system(size: 8.5, weight: .medium))
                    .foregroundStyle(LabPalette.rgb("B26A00"))
            }
        }
    }

    /// 没课的时候用次要色，有安排才用状态色
    private func footerColor(_ snap: LabSnapshot, _ tint: Color) -> Color {
        (snap.nextCourse == nil && snap.courses.isEmpty) ? Color.secondary : tint
    }

    private func footerLine(_ snap: LabSnapshot) -> String {
        if let next = liveNextCourse(snap) {
            let loc = (next.location ?? "").isEmpty ? "" : " · \(next.location!)"
            return "\(next.start) \(next.name)\(loc)"
        }
        if !snap.courses.isEmpty { return "今日 \(snap.courses.count) 节课已结束" }
        if snap.kind == "holiday" { return "节假日 · 不产生目标" }
        if snap.kind == "weekend" { return "周末 · 不产生目标" }
        return "今天没课"
    }

    private func secondLine(_ snap: LabSnapshot) -> String {
        if let event = snap.importantEvent, !event.isEmpty {
            return "⚠︎ \(event)"
        }
        var parts: [String] = []
        if snap.labMin > 0 { parts.append("实验室 \(LabFormat.hours(snap.labMin))h") }
        if snap.manualMin > 0 { parts.append("手动 \(LabFormat.hours(snap.manualMin))h") }
        if snap.courseMin > 0 { parts.append("课程 \(LabFormat.hours(snap.courseMin))h") }
        let todo = snap.todoOpen > 0 ? "\(snap.todoOpen) 项待办" : "待办已清空"
        parts.append(todo)
        return parts.joined(separator: " · ")
    }

    private func liveNextCourse(_ snap: LabSnapshot) -> LabSnapshot.NextCourse? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        guard formatter.string(from: entry.date) == snap.date else { return nil }
        let cal = Calendar.current
        let nowMin = cal.component(.hour, from: entry.date) * 60 + cal.component(.minute, from: entry.date)
        let upcoming = snap.courses.filter { course in
            let parts = course.end.split(separator: ":").compactMap { Int($0) }
            return parts.count == 2 && parts[0] * 60 + parts[1] > nowMin
        }.sorted { $0.start < $1.start }
        guard let course = upcoming.first else { return nil }
        return .init(name: course.name, start: course.start, location: course.location)
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("LabAssistant")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(LabPalette.accent)
            Spacer()
            Text("打开 LabAssistant 同步今日数据")
                .font(.system(size: 10.5))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// ---------------------------------------------------------------- Widget
struct LabTodayWidget: Widget {
    let kind = "LabTodayWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: LabProvider()) { entry in
            LabTodayView(entry: entry)
                .widgetURL(URL(string: "labassistant-widget://today"))
        }
        .configurationDisplayName("今日实验室")
        .description("只显示今天：方格进度、时长、下一节课、待办数。")
        .supportedFamilies([.systemSmall])
    }
}

@main
struct LabAssistantTodayBundle: WidgetBundle {
    var body: some Widget {
        LabTodayWidget()
    }
}
