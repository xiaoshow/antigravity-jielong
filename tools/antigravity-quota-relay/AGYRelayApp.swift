import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var child: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.applicationIconImage = Self.makeIcon()
        launchHUD()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showHUD()
        return true
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        showHUD()
    }

    func applicationWillTerminate(_ notification: Notification) {
        child?.terminate()
    }

    private func launchHUD() {
        terminateExisting()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/opt/homebrew/bin/python3")
        process.arguments = ["/Users/weinliu/tools/antigravity-quota-relay/multi_hud.py"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle(forWritingAtPath: "/Users/weinliu/tools/antigravity-quota-relay/hud.log") ?? FileHandle.nullDevice
        do {
            try process.run()
            child = process
        } catch {
            let alert = NSAlert()
            alert.messageText = "AGY 接龙启动失败"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }

    private func terminateExisting() {
        let finder = Process()
        finder.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        finder.arguments = ["-f", "^/opt/homebrew/.*/Python /Users/weinliu/tools/antigravity-quota-relay/multi_hud.py$"]
        let pipe = Pipe()
        finder.standardOutput = pipe
        finder.standardError = FileHandle.nullDevice
        try? finder.run()
        finder.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        guard let output = String(data: data, encoding: .utf8) else { return }
        for line in output.split(whereSeparator: \.isNewline) {
            if let pid = Int32(line.trimmingCharacters(in: .whitespaces)) {
                kill(pid, SIGTERM)
            }
        }
    }

    private func showHUD() {
        guard let child, child.isRunning,
              let running = NSRunningApplication(processIdentifier: child.processIdentifier) else { return }
        running.activate(options: [.activateAllWindows])
    }

    static func makeIcon() -> NSImage {
        let size = NSSize(width: 512, height: 512)
        return NSImage(size: size, flipped: false) { rect in
            let r = rect.insetBy(dx: 30, dy: 30)
            let path = NSBezierPath(roundedRect: r, xRadius: 105, yRadius: 105)
            let grad = NSGradient(colors: [
                NSColor(red: 0.12, green: 0.14, blue: 0.22, alpha: 1.0),
                NSColor(red: 0.18, green: 0.22, blue: 0.35, alpha: 1.0)
            ])
            grad?.draw(in: path, angle: -45)
            
            // 绘制双向切换与闪电符号 ⚡ ⇄
            let text = "⚡"
            let font = NSFont.systemFont(ofSize: 200, weight: .bold)
            let str = NSAttributedString(string: text, attributes: [
                .font: font,
                .foregroundColor: NSColor(red: 0.22, green: 0.74, blue: 0.95, alpha: 1.0)
            ])
            str.draw(at: NSPoint(x: rect.midX - 85, y: rect.midY - 65))
            
            // 底部 1 2 3 号指示点
            let colors: [NSColor] = [.systemGreen, .systemCyan, .systemOrange]
            for (i, c) in colors.enumerated() {
                let dotRect = NSRect(x: rect.midX - 60 + CGFloat(i) * 50, y: r.minY + 45, width: 22, height: 22)
                c.setFill()
                NSBezierPath(ovalIn: dotRect).fill()
            }
            return true
        }
    }
}

if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--export-icon" {
    let img = AppDelegate.makeIcon()
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1024, pixelsHigh: 1024, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    img.draw(in: NSRect(x: 0, y: 0, width: 1024, height: 1024))
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
