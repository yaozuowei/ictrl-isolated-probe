"""Prepare only a pinned, clean upstream checkout for a separate test install."""
import pathlib
import re
import subprocess
import sys

BASE = 'c1e4268859ba858d1579f7b846a56cb40641b7f6'
PREFIX = 'org.houchang.ictrlprobe'
root = pathlib.Path(sys.argv[1]).resolve()
head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
assert head == BASE, 'Unexpected upstream revision'
assert not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip(), 'Checkout must be clean'

# Only upstream build/runtime sources are modified; no user project is copied.
files = [root / 'Makefile', root / 'project.yml']
for directory in ['Config', 'Scripts', 'iCTRLApp', 'iCTRLKit', 'iCTRLUITests']:
    files.extend(p for p in (root / directory).rglob('*') if p.suffix in {'.swift', '.m', '.h', '.plist', '.sh', '.xcconfig'})
for path in files:
    before = path.read_text(encoding='utf-8')
    after = before.replace('com.openminis', PREFIX)
    after = after.replace('ictrl-app', 'hc-ictrl-probe-app').replace('ictrl://', 'hc-ictrl-probe://')
    after = after.replace('"ictrl"', '"hc-ictrl-probe"').replace('<string>ictrl</string>', '<string>hc-ictrl-probe</string>')
    after = after.replace('string ictrl"', 'string hc-ictrl-probe"')
    after = after.replace('_ictrl._tcp', '_hcprobe._tcp')
    after = re.sub(r'\b8200\b', '18201', after)
    if path.name == 'Makefile':
        anchor = '\tSDK="$$(xcrun --sdk iphoneos --show-sdk-path)"; \\\n'
        assert after.count(anchor) == 1
        # Xcode 16.4's runner does not import this newer-Xcode symbol.
        # Inspect successfully before skipping; tool failures must fail the build.
        guard = ('\tUNDEFINED="$$(nm -u "$$APP/$(RUNNER_BIN)")"; \\\n'
                 '\tif ! printf "%s\\n" "$$UNDEFINED" | grep -Fq \'_OBJC_CLASS_$$_XCTCommandLineToolHelper\'; then \\\n'
                 '\t\techo "Runner does not import compat symbol; shim not needed"; \\\n'
                 '\t\texit 0; \\\n'
                 '\tfi; \\\n')
        after = after.replace(anchor, guard + anchor)
    if path.name == 'project.yml':
        after = after.replace('GENERATE_INFOPLIST_FILE: YES', 'GENERATE_INFOPLIST_FILE: YES\n        INFOPLIST_KEY_CFBundleDisplayName: "iCTRL Probe"')
        # The upstream clients persist state in ~/.ictrl and default to the live
        # service. Do not distribute them from this baseline experiment.
        after, removed = re.subn(r'      - path: (?:SKILL\.md|Scripts/ictrl_rpc\.py|cli/ictrl(?:\.sh)?)\n        buildPhase: resources\n', '', after)
        assert removed == 4, 'Unexpected bundled client resource layout'
    if before != after:
        path.write_text(after, encoding='utf-8', newline='\n')

assert f'ICTRL_BUNDLE_PREFIX = {PREFIX}' in (root / 'Config/Signing.xcconfig').read_text(encoding='utf-8')
assert 'string hc-ictrl-probe"' in (root / 'Makefile').read_text(encoding='utf-8')
assert '"hc-ictrl-probe"' in (root / 'iCTRLUITests/EarlyURLHook.m').read_text(encoding='utf-8')
assert f'{PREFIX}.ictrl.auth' in (root / 'iCTRLKit/KeychainStore.swift').read_text(encoding='utf-8')
print('Isolated baseline prepared; no guarded_tap implementation yet.')
