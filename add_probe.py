"""Add the experimental Safari-only endpoint to the already isolated checkout."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
def replace(relative, old, new):
    path = root / relative
    text = path.read_text(encoding='utf-8')
    assert text.count(old) == 1, f'Unexpected source anchor: {relative}'
    path.write_text(text.replace(old, new), encoding='utf-8', newline='\n')

(root / 'iCTRLUITests/ProbeNativeJob.swift').write_text((Path(__file__).parent / 'ProbeNativeJob.swift').read_text(encoding='utf-8'), encoding='utf-8')
(root / 'iCTRLUITests/ProbePanel.swift').write_text((Path(__file__).parent / 'ProbePanel.swift').read_text(encoding='utf-8'), encoding='utf-8')
replace('iCTRLUITests/ActionHandler.swift', '    func dispatch(method: String, params: [String: JSONValue]?) -> ([String: JSONValue]?, JSONRPCError?) {', '''    private var nativeJobRefs: [String: String] = [:]
    private var nativeJobRefTypes: [String: String] = [:]
    private lazy var nativePanel = ProbePanel { [weak self] method, params in
        guard let self = self else { return (nil, .internalError) }
        return self.dispatch(method: method, params: params)
    }
    func showNativePanel(activate: Bool = false) {
        nativePanel.show()
        if activate {
            _ = ObjCException.catching {
                _ = self.dispatch(method: "open_url", params: ["url": .string("hc-ictrl-probe://")])
            }
        }
    }
    private lazy var probeNativeJob = ProbeNativeJob(showPanel: { [weak self] in
        self?.showNativePanel(activate: true)
    }) { [weak self] method, params in
        guard let self = self else { return (nil, .internalError) }
        return self.nativeJobCall(method, params)
    }

    func dispatch(method: String, params: [String: JSONValue]?, fromNativeJob: Bool = false) -> ([String: JSONValue]?, JSONRPCError?) {
        if ["native_job_start", "native_job_status", "native_job_cancel", "native_app_install", "native_app_page"].contains(method) {
            if method == "native_job_start" && !probeNativeJob.active { nativeJobRefs.removeAll(); nativeJobRefTypes.removeAll() }
            let result = probeNativeJob.handle(method, params ?? [:])
            if method == "native_app_install" && result.1 == nil { nativePanel.reload() }
            return result
        }
        if ProbeNativeJob.isBusy && !fromNativeJob {
            return (nil, .serverError("Native test is active; cancel it before other UI operations"))
        }''')
replace('iCTRLUITests/iCTRLUITests.swift', '        backgroundSelfNow()\n',
        '        DispatchQueue.main.async { [weak self] in self?.actionHandler?.showNativePanel() }\n')
native_method = (Path(__file__).parent / 'native-job-method.swift.txt').read_text(encoding='utf-8')
replace('iCTRLUITests/ActionHandler.swift', '    private func tap(_ cmd: ActionCommand)', native_method + '    private func tap(_ cmd: ActionCommand)')

# Propagate traversal exceptions instead of accepting a partial tree. This only
# changes the independent probe app, never the installed production runner.
replace('iCTRLUITests/ForegroundApp.m',
        '    } @catch (__unused NSException *e) {}\n    return NO;\n}\n\n// XCUIElementType',
        '    } @catch (__unused NSException *e) { *truncated = YES; }\n    return NO;\n}\n\n// XCUIElementType')
replace('iCTRLUITests/ForegroundApp.m', '    NSInteger visited;\n    CGRect viewport;',
        '    NSInteger visited;\n    BOOL failed;\n    CGRect viewport;')
replace('iCTRLUITests/ForegroundApp.m',
        '    } @catch (__unused NSException *e) {}\n}\n\n+ (nullable NSDictionary *)locateInBoundedSnapshot:',
        '    } @catch (__unused NSException *e) { ctx->failed = YES; }\n}\n\n+ (nullable NSDictionary *)locateInBoundedSnapshot:')
replace('iCTRLUITests/ForegroundApp.m',
        '                 cands, bars, scrollables, fp);\n',
        '                 cands, bars, scrollables, fp);\n    if (ctx.failed || ctx.visited >= ctx.maxVisit) { return nil; }\n')

replace('iCTRLUITests/ActionCommand.swift', '    let name: String\n', '    let name: String\n    var probeDeadlineMs: Double?\n')
replace('iCTRLUITests/ActionCommand.swift', '        self.name = method\n', '        self.name = method\n        self.probeDeadlineMs = params?["expires_at_ms"]?.doubleValue\n')
replace('iCTRLUITests/ActionHandler.swift', '        case "tap":\n', '        case "probe_guarded_tap":\n            return probeGuardedTap(cmd)\n        case "probe_guarded_run":\n            return probeGuardedRun(cmd)\n        case "tap":\n')
for anchor in ['private static let uiActionMethods: Set<String> = [', 'private static let knownMethods: [String] = [']:
    replace('iCTRLUITests/ActionHandler.swift', anchor, anchor + '\n        "probe_guarded_tap", "probe_guarded_run", "native_job_start", "native_job_status", "native_job_cancel",')
method = (Path(__file__).parent / 'probe-method.swift.txt').read_text(encoding='utf-8')
replace('iCTRLUITests/ActionHandler.swift', '    private func tap(_ cmd: ActionCommand)', method + '    private func tap(_ cmd: ActionCommand)')
replace('iCTRLUITests/iCTRLUITests.swift', 'private static let mainThreadMethods: Set<String> = [', 'private static let mainThreadMethods: Set<String> = [\n        "probe_guarded_tap", "probe_guarded_run", "native_job_start", "native_job_status", "native_job_cancel",')
replace('iCTRLKit/JSONRPCServer.swift', 'private static let uiMutatingMethods: Set<String> = [', 'private static let uiMutatingMethods: Set<String> = [\n        "probe_guarded_tap", "probe_guarded_run", "native_job_start", "native_job_cancel",')
schema = '''
        RPCMethodDef(name: "native_job_start", description: "Experimental trusted local generator task. Confirm-only, no submission/payment targets. Scripts are supplied locally, not bundled. One task, default 240-second monotonic runtime cap; explicit runtime_seconds supports 60..86400. OS background survival is not guaranteed. Never retry a timed-out start; query status with the same run id. In-flight OS actions cannot be cancelled.", params: [RPCParamDef(name: "run_id", type: "string", required: true, description: "Unique request identity; do not reuse"), RPCParamDef(name: "source", type: "string", required: false, description: "Trusted local generator source; omitted uses installed local app"), RPCParamDef(name: "config", type: "object", required: true, description: "Isolated confirm-only config"), RPCParamDef(name: "bundle_id", type: "string", required: true, description: "Only app this task can inspect or control"), RPCParamDef(name: "open_url", type: "string", required: false, description: "Only exact deep link permitted"), RPCParamDef(name: "runtime_seconds", type: "number", required: false, description: "Monotonic task budget in seconds, 60..86400; default 240"), RPCParamDef(name: "return_to_panel", type: "boolean", required: false, description: "Return to the localhost panel after task finishes")], returns: "{run_id,phase,active,report,steps}"),
        RPCMethodDef(name: "native_job_status", description: "Read native test status; restart interrupts without replay.", params: [], returns: "{run_id,phase,active,report,steps}"),
        RPCMethodDef(name: "native_job_cancel", description: "Stop scheduling new steps. An already dispatched OS action cannot be undone.", params: [RPCParamDef(name: "run_id", type: "string", required: true, description: "Identity of task to cancel")], returns: "{run_id,phase,active}"),
        RPCMethodDef(name: "probe_guarded_run",
            description: "Safari isolated experiment only: native guarded loop, at most six touches and three seconds from handler start to initiate touches; queue time excluded. New full snapshot before each touch. Stops on any non-tapped result. No production apps or remote cancellation. Do not resend after timeout. In-flight OS calls cannot be cancelled; return time may exceed three seconds.",
            params: [RPCParamDef(name: "text", type: "string", required: true, description: "16-character nonce displayed by the current isolated page")],
            returns: "{state,reason,taps,steps,elapsed_ms}"),
        RPCMethodDef(name: "probe_guarded_tap",
            description: "Experimental single guarded tap, Safari isolated probe only. No batches or retry on timeout. Never use with production apps.",
            params: [RPCParamDef(name: "text", type: "string", required: true, description: "16-character alphanumeric nonce displayed by the isolated probe page"),
                     RPCParamDef(name: "expires_at_ms", type: "number", required: true, description: "Unix milliseconds deadline, at most five seconds ahead")],
            returns: "{state:tapped|stopped|uncertain,reason?,check_ms?,touch_ms?,elapsed_ms}"),
'''
replace('iCTRLKit/RPCMethod.swift', 'static let methods: [RPCMethodDef] = [\n', 'static let methods: [RPCMethodDef] = [\n' + schema)

# Public cloud code serves only locally installed assets. Authentication remains the
# existing paired/encrypted RPC protocol; GET never includes config or credentials.
for relative, anchor in [('iCTRLUITests/ActionHandler.swift', 'private static let knownMethods: [String] = ['),
                         ('iCTRLUITests/iCTRLUITests.swift', 'private static let mainThreadMethods: Set<String> = [')]:
    replace(relative, anchor, anchor + '\n        "native_app_install", "native_app_page",')
replace('iCTRLKit/JSONRPCServer.swift', 'private static let uiMutatingMethods: Set<String> = [',
        'private static let uiMutatingMethods: Set<String> = [\n        "native_app_install",')
replace('iCTRLKit/JSONRPCServer.swift', '        case ("GET", "/schema"):', '''        case ("GET", "/local-app"):
            guard isLoopback(connection), headers["host"] == "127.0.0.1:18201" else {
                return sendHTTPResponse(connection: connection, status: 403, body: "Phone localhost only", cors: false)
            }
            guard let handler = onRPCRequest else {
                return sendHTTPResponse(connection: connection, status: 500, body: "Runner not ready", cors: false)
            }
            handler("native_app_page", [:]) { [weak self] result, error in
                guard let self = self else { return }
                self.queue.async {
                    self.sendHTTPResponse(connection: connection, status: error == nil ? 200 : 404,
                        headers: ["Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                  "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"],
                        contentType: "text/html; charset=utf-8", body: result?["html"]?.stringValue ?? "Local app not installed", cors: false)
                }
            }

        case ("GET", "/schema"):''')
replace('iCTRLKit/RPCMethod.swift', 'static let methods: [RPCMethodDef] = [\n', '''static let methods: [RPCMethodDef] = [
        RPCMethodDef(name: "native_app_install", description: "Install trusted local HTML and generator source into the isolated Probe container. No secrets in HTML. Requires existing pairing; task must be idle.", params: [RPCParamDef(name: "html", type: "string", required: true, description: "Local UI document, under 250 KB"), RPCParamDef(name: "source", type: "string", required: true, description: "Trusted generator, under 200 KB")], returns: "{installed}"),
''')
print('Experimental probe_guarded_tap/run added; no production automation enabled.')
