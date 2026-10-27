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
replace('iCTRLUITests/ActionHandler.swift', '    func dispatch(method: String, params: [String: JSONValue]?) -> ([String: JSONValue]?, JSONRPCError?) {', '''    private var nativeJobRefs: [String: String] = [:]
    private var nativeJobRefTypes: [String: String] = [:]
    private lazy var probeNativeJob = ProbeNativeJob { [weak self] method, params in
        guard let self = self else { return (nil, .internalError) }
        return self.nativeJobCall(method, params)
    }

    func dispatch(method: String, params: [String: JSONValue]?, fromNativeJob: Bool = false) -> ([String: JSONValue]?, JSONRPCError?) {
        if ["native_job_start", "native_job_status", "native_job_cancel"].contains(method) {
            if method == "native_job_start" && !probeNativeJob.active { nativeJobRefs.removeAll(); nativeJobRefTypes.removeAll() }
            return probeNativeJob.handle(method, params ?? [:])
        }
        if ProbeNativeJob.isBusy && !fromNativeJob {
            return (nil, .serverError("Native test is active; cancel it before other UI operations"))
        }''')
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
        RPCMethodDef(name: "native_job_start", description: "Experimental trusted local generator task. Confirm-only, no submission/payment targets. Scripts are supplied locally, not bundled. One task, 240-second runtime cap. Never retry a timed-out start; query status with the same run id. In-flight OS actions cannot be cancelled.", params: [RPCParamDef(name: "run_id", type: "string", required: true, description: "Unique request identity; do not reuse"), RPCParamDef(name: "source", type: "string", required: true, description: "Trusted local generator source"), RPCParamDef(name: "config", type: "object", required: true, description: "Isolated confirm-only config"), RPCParamDef(name: "bundle_id", type: "string", required: true, description: "Only app this task can inspect or control"), RPCParamDef(name: "open_url", type: "string", required: false, description: "Only exact deep link permitted")], returns: "{run_id,phase,active,report,steps}"),
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
print('Experimental probe_guarded_tap/run added; no production automation enabled.')
