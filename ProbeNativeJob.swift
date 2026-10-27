import Foundation
import JavaScriptCore
import UIKit

/// Experimental local generator host. Scripts/config stay on-device, not in the cloud build.
/// Only trusted local scripts are supported; this is not an untrusted-JavaScript sandbox.
final class ProbeNativeJob {
    private static let busyLock = NSLock()
    private static var busy = false
    static var isBusy: Bool {
        busyLock.lock(); defer { busyLock.unlock() }
        return busy
    }
    typealias Call = (String, [String: JSONValue]) -> ([String: JSONValue]?, JSONRPCError?)
    private let call: Call
    private var context: JSContext?
    private var input = "{}"
    private var began = 0.0
    private var generation = 0
    private var inFlight = false { didSet { refreshBusy() } }
    private var rgba: [UInt8] = []
    private let file: URL
    private(set) var active = false { didSet { refreshBusy() } }
    var canAct: Bool { active && ProcessInfo.processInfo.systemUptime - began < 240 }
    private(set) var bundle = ""
    private(set) var openURL = ""
    private var state: [String: JSONValue] = [:]
    private var usedIDs: [String] = []

    private func refreshBusy() {
        Self.busyLock.lock(); defer { Self.busyLock.unlock() }
        Self.busy = active || inFlight
    }

    deinit {
        Self.busyLock.lock(); defer { Self.busyLock.unlock() }
        Self.busy = false
    }

    init(call: @escaping Call) {
        self.call = call
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("NativeProbe", isDirectory: true)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        file = base.appendingPathComponent("status.json")
        if let data = try? Data(contentsOf: file),
           let saved = try? JSONDecoder().decode([String: JSONValue].self, from: data) {
            state = saved
            if case .array(let ids)? = saved["used_ids"] { usedIDs = ids.compactMap { $0.stringValue } }
            if saved["active"]?.boolValue == true {
                state["phase"] = .string("interrupted")
                state["message"] = .string("Runner restarted; task was not replayed")
                state["active"] = .bool(false)
                persist()
            }
        }
    }

    @discardableResult private func persist() -> Bool {
        state["active"] = .bool(active)
        state["used_ids"] = .array(usedIDs.map { .string($0) })
        do {
            let data = try JSONEncoder().encode(state)
            try data.write(to: file, options: .atomic)
            return true
        } catch { return false }
    }

    private func finish(_ phase: String, _ message: String) {
        active = false
        generation += 1
        state["phase"] = .string(phase)
        state["message"] = .string(message)
        state["elapsed_ms"] = .int(Int((ProcessInfo.processInfo.systemUptime - began) * 1000))
        context = nil
        rgba = []
        persist()
    }

    func handle(_ method: String, _ params: [String: JSONValue]) -> ([String: JSONValue]?, JSONRPCError?) {
        precondition(Thread.isMainThread)
        if method == "native_job_status" { return (state, nil) }
        if method == "native_job_cancel" {
            guard params["run_id"] == state["run_id"] else { return (nil, .invalidParams) }
            if active { finish("stopped", "Stopped by user; no more actions scheduled") }
            return (state, nil)
        }
        guard !active && !inFlight else { return (nil, .serverError("A native task or action is still active")) }
        guard let id = params["run_id"]?.stringValue, id.count >= 16, id.count <= 100,
              !usedIDs.contains(id), let source = params["source"]?.stringValue, source.utf8.count < 200_000,
              let bid = params["bundle_id"]?.stringValue, !bid.isEmpty,
              case .object(let config)? = params["config"], config["stopAt"]?.stringValue == "confirm",
              let js = JSContext() else { return (nil, .invalidParams) }
        bundle = bid
        openURL = params["open_url"]?.stringValue ?? ""
        began = ProcessInfo.processInfo.systemUptime
        generation += 1
        usedIDs.append(id); usedIDs = Array(usedIDs.suffix(40))
        state = ["run_id": .string(id), "phase": .string("starting"), "steps": .int(0)]
        active = true
        guard persist() else { finish("failed", "Cannot persist task identity; no action started"); return (state, nil) }
        context = js
        let ownGeneration = generation
        let report: @convention(block) (String) -> Void = { [weak self] text in
            guard let self = self, self.generation == ownGeneration, self.active, let data = text.data(using: .utf8),
                  let value = try? JSONDecoder().decode([String: JSONValue].self, from: data) else { return }
            self.state["report"] = .object(value)
            self.state["phase"] = value["phase"] ?? .string("running")
            self.state["message"] = value["message"] ?? .string("")
            if !self.persist() {
                self.active = false
                self.state["phase"] = .string("failed")
                self.state["message"] = .string("Could not persist progress; no further actions")
            }
        }
        let stopped: @convention(block) () -> Bool = { [weak self] in
            guard let self = self else { return true }
            return !self.active || self.generation != ownGeneration
        }
        let parseURL: @convention(block) (String) -> String = { text in
            guard let u = URLComponents(string: text), let scheme = u.scheme, let host = u.host else { return "{\"error\":\"Invalid URL\"}" }
            let value: [String: Any] = ["protocol": scheme + ":", "hostname": host, "pathname": u.path,
                "username": u.user ?? "", "password": u.password ?? "", "port": u.port.map(String.init) ?? "",
                "query": (u.queryItems ?? []).map { [$0.name, $0.value ?? ""] }]
            return String(data: (try? JSONSerialization.data(withJSONObject: value)) ?? Data(), encoding: .utf8) ?? "{}"
        }
        let decode: @convention(block) (String) -> String = { [weak self] text in self?.decodeImage(text) ?? "{}" }
        let pixel: @convention(block) (Double) -> Int = { [weak self] index in
            guard let self = self, index.isFinite, index >= 0, index < Double(self.rgba.count) else { return 0 }
            return Int(self.rgba[Int(index)])
        }
        js.setObject(report, forKeyedSubscript: "__report" as NSString)
        js.setObject(stopped, forKeyedSubscript: "__stopped" as NSString)
        js.setObject(parseURL, forKeyedSubscript: "__parseURL" as NSString)
        js.setObject(decode, forKeyedSubscript: "__decodeImage" as NSString)
        js.setObject(pixel, forKeyedSubscript: "__pixel" as NSString)
        js.evaluateScript(source)
        guard js.exception == nil else { finish("failed", "Script failed to initialize"); return (state, nil) }
        let data = (try? JSONEncoder().encode(config)) ?? Data()
        js.setObject(String(data: data, encoding: .utf8) ?? "{}", forKeyedSubscript: "__configJSON" as NSString)
        js.evaluateScript("var __jobIterator = createJob(JSON.parse(__configJSON));")
        guard js.exception == nil else { finish("failed", "Task creation failed"); return (state, nil) }
        input = "{}"
        schedule(0)
        return (state, nil)
    }

    private func schedule(_ milliseconds: Double) {
        let expected = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + milliseconds / 1000) { [weak self] in
            guard let self = self, self.active, self.generation == expected else { return }
            self.tick()
        }
    }

    private func tick() {
        guard active, let js = context else { return }
        let ownGeneration = generation
        guard canAct else { finish("stopped", "Native test runtime limit"); return }
        js.setObject(input, forKeyedSubscript: "__inputJSON" as NSString)
        guard let text = js.evaluateScript("JSON.stringify(__jobIterator.next(JSON.parse(__inputJSON)))")?.toString(),
              js.exception == nil, let data = text.data(using: .utf8),
              let step = try? JSONDecoder().decode([String: JSONValue].self, from: data) else {
            finish("failed", "Script step failed; no retry"); return
        }
        guard ownGeneration == generation else { return }
        guard active else { context = nil; rgba = []; return }
        if step["done"]?.boolValue == true {
            if case .object(let result)? = step["value"] {
                state["report"] = .object(result)
                finish(result["phase"]?.stringValue ?? "failed", result["message"]?.stringValue ?? "Finished")
            } else { finish("failed", "Missing final task result") }
            return
        }
        guard case .object(let command)? = step["value"] else { finish("failed", "Invalid yielded command"); return }
        if command["op"]?.stringValue == "sleep" {
            guard let ms = command["ms"]?.doubleValue, ms.isFinite, ms >= 0, ms <= 3000 else { finish("failed", "Invalid wait"); return }
            input = "{}"; schedule(ms); return
        }
        guard command["op"]?.stringValue == "call", let method = command["method"]?.stringValue,
              case .object(let params)? = command["params"] else { finish("failed", "Invalid native call"); return }
        guard canAct else { finish("stopped", "Native test runtime limit"); return }
        var result: ([String: JSONValue]?, JSONRPCError?) = (nil, nil)
        inFlight = true
        let exception = ObjCException.catching { result = self.call(method, params) }
        inFlight = false
        guard ownGeneration == generation, active else { return }
        if exception != nil { finish("failed", "Native action raised an exception; do not retry"); return }
        let (value, error) = result
        state["steps"] = .int((state["steps"]?.intValue ?? 0) + 1)
        guard error == nil, let value = value, let data = try? JSONEncoder().encode(value),
              let next = String(data: data, encoding: .utf8) else {
            finish("failed", error?.message ?? "Native action uncertain; do not retry"); return
        }
        input = next
        schedule(0)
    }

    private func decodeImage(_ base64: String) -> String {
        guard let data = Data(base64Encoded: base64), let image = UIImage(data: data)?.cgImage,
              image.width * image.height <= 4_000_000 else { return "{\"error\":\"Invalid image\"}" }
        let width = image.width, height = image.height
        rgba = [UInt8](repeating: 0, count: width * height * 4)
        let ok = rgba.withUnsafeMutableBytes { bytes -> Bool in
            guard let ctx = CGContext(data: bytes.baseAddress, width: width, height: height,
                bitsPerComponent: 8, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        return ok ? "{\"width\":\(width),\"height\":\(height)}" : "{\"error\":\"Decode failed\"}"
    }
}
