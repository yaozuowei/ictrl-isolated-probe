"""Add the experimental Safari-only endpoint to the already isolated checkout."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
def replace(relative, old, new):
    path = root / relative
    text = path.read_text(encoding='utf-8')
    assert text.count(old) == 1, f'Unexpected source anchor: {relative}'
    path.write_text(text.replace(old, new), encoding='utf-8', newline='\n')

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
    replace('iCTRLUITests/ActionHandler.swift', anchor, anchor + '\n        "probe_guarded_tap", "probe_guarded_run",')
method = (Path(__file__).parent / 'probe-method.swift.txt').read_text(encoding='utf-8')
replace('iCTRLUITests/ActionHandler.swift', '    private func tap(_ cmd: ActionCommand)', method + '    private func tap(_ cmd: ActionCommand)')
replace('iCTRLUITests/iCTRLUITests.swift', 'private static let mainThreadMethods: Set<String> = [', 'private static let mainThreadMethods: Set<String> = [\n        "probe_guarded_tap", "probe_guarded_run",')
replace('iCTRLKit/JSONRPCServer.swift', 'private static let uiMutatingMethods: Set<String> = [', 'private static let uiMutatingMethods: Set<String> = [\n        "probe_guarded_tap", "probe_guarded_run",')
schema = '''
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
