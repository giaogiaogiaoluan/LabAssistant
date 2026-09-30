//
//  LabSnapshot.swift
//  读取 LabAssistant 导出的今日快照。
//
//  为什么不在小组件里直接读 SQLite / 重算统计：
//  统计算法（实验室时段 ∪ 计入打卡的课程时段，同日合并去重，再加手动时长）只应有一份实现。
//  两边各写一遍迟早对不上，桌面方格进度就会和软件里的数字互相矛盾。
//  所以由 App 侧算好写成 JSON，小组件只负责渲染。
//

import Foundation

enum LabGroup {
    static let fileName = "widget_snapshot.json"
}

struct LabSnapshot: Codable {
    let schema: Int
    let generatedAt: String
    let date: String
    let dayNum: Int
    let monthCn: String
    let weekdayCn: String
    let kind: String
    let holidayName: String?
    let statusKey: String
    let statusLabel: String
    let requiredMin: Int
    let effectiveMin: Int
    let ratio: Double
    let labMin: Int
    let courseMin: Int
    let manualMin: Int
    let overlapMin: Int
    let courses: [Course]
    let labBlocks: [LabBlock]
    let nextCourse: NextCourse?
    let todoOpen: Int
    let todoDone: Int
    let todoTitles: [String]?
    let nextTodo: String?
    let importantEvent: String?
    let nextRefresh: String?
    let appVersion: String?

    struct Course: Codable {
        let name: String
        let start: String
        let end: String
        let location: String?
        let state: String
        let counts: Bool
    }

    struct LabBlock: Codable {
        let start: String
        let end: String
    }

    struct NextCourse: Codable {
        let name: String
        let start: String
        let location: String?
    }

    enum CodingKeys: String, CodingKey {
        case schema
        case generatedAt = "generated_at"
        case date
        case dayNum = "day_num"
        case monthCn = "month_cn"
        case weekdayCn = "weekday_cn"
        case kind
        case holidayName = "holiday_name"
        case statusKey = "status_key"
        case statusLabel = "status_label"
        case requiredMin = "required_min"
        case effectiveMin = "effective_min"
        case ratio
        case labMin = "lab_min"
        case courseMin = "course_min"
        case manualMin = "manual_min"
        case overlapMin = "overlap_min"
        case courses
        case labBlocks = "lab_blocks"
        case nextCourse = "next_course"
        case todoOpen = "todo_open"
        case todoDone = "todo_done"
        case todoTitles = "todo_titles"
        case nextTodo = "next_todo"
        case importantEvent = "important_event"
        case nextRefresh = "next_refresh"
        case appVersion = "app_version"
    }

    /// schema 不匹配时按“不可用”处理，避免字段含义变了还硬画
    var isCompatible: Bool { schema == 1 }

    var generatedDate: Date? { LabDate.parse(generatedAt) }

    var refreshDate: Date? { LabDate.parse(nextRefresh ?? "") }

    /// 快照超过这个时长就认为 App 没在更新（比如被卸载了），界面上给提示
    var isStale: Bool {
        guard let g = generatedDate else { return true }
        return Date().timeIntervalSince(g) > 26 * 3600
    }

    /// 完成小时数文案：整数不带小数，和软件里一致
    var effectiveHoursText: String { LabFormat.hours(effectiveMin) }
    var requiredHoursText: String { LabFormat.hours(requiredMin) }
    var percentText: String { "\(Int((min(max(ratio, 0), 9.99) * 100).rounded()))%" }
}

enum LabDate {
    /// Python 写的是带时区偏移的 ISO8601；这里两种形态都容错
    static func parse(_ text: String) -> Date? {
        if text.isEmpty { return nil }
        let withTZ = ISO8601DateFormatter()
        withTZ.formatOptions = [.withInternetDateTime]
        if let d = withTZ.date(from: text) { return d }
        let noTZ = ISO8601DateFormatter()
        noTZ.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = noTZ.date(from: text) { return d }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return local.date(from: String(text.prefix(19)))
    }
}

enum LabFormat {
    static func hours(_ minutes: Int) -> String {
        let h = Double(minutes) / 60.0
        if abs(h - h.rounded()) < 0.000_001 { return String(format: "%.0f", h.rounded()) }
        return String(format: "%.1f", h)
    }
}

enum LabSnapshotLoader {
    static func candidateURLs() -> [URL] {
        [FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/LabAssistant/\(LabGroup.fileName)")]
    }

    /// 依次尝试候选路径；读不到返回 nil（界面上会提示去打开 App）
    static func load() -> LabSnapshot? {
        for url in candidateURLs() {
            guard let data = try? Data(contentsOf: url) else { continue }
            if let snap = try? JSONDecoder().decode(LabSnapshot.self, from: data),
               snap.isCompatible {
                return snap
            }
        }
        return nil
    }
}
