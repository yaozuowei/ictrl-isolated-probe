"""Inspect build artifacts before any installation is allowed."""
import pathlib
import plistlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
expected = {
    'iCTRL.ipa': 'org.houchang.ictrlprobe.ictrl',
    'iCTRLUITests-Runner.ipa': 'org.houchang.ictrlprobe.ictrl.uitests.xctrunner',
}
for filename, bundle_id in expected.items():
    with zipfile.ZipFile(root / filename) as archive:
        entries = archive.namelist()
        mains = [p for p in entries if p.startswith('Payload/') and p.endswith('.app/Info.plist') and p.count('/') == 2]
        assert len(mains) == 1, 'Missing or ambiguous main app'
        info = plistlib.loads(archive.read(mains[0]))
        assert info['CFBundleIdentifier'] == bundle_id, 'Would conflict with another install'
        schemes = [s for item in info.get('CFBundleURLTypes', []) for s in item.get('CFBundleURLSchemes', [])]
        assert not {'ictrl', 'ictrl-app'}.intersection(schemes), 'Original launch scheme conflict'
        assert ('hc-ictrl-probe' if 'Runner' in filename else 'hc-ictrl-probe-app') in schemes
        services = info.get('NSBonjourServices', [])
        assert '_hcprobe._tcp' in services and '_ictrl._tcp' not in services, 'Bonjour service conflict'
        assert not any(p.endswith('embedded.mobileprovision') for p in entries), 'Cloud artifact must not include profiles'
        assert not any(pathlib.PurePosixPath(p).name in {'ictrl', 'ictrl.sh', 'ictrl_rpc.py', 'SKILL.md'} for p in entries), 'Original client resource must not ship'
        if 'Runner' in filename:
            plugins = [p for p in entries if p.endswith('.xctest/Info.plist')]
            assert len(plugins) == 1
            assert plistlib.loads(archive.read(plugins[0]))['CFBundleIdentifier'] == bundle_id
    print(filename, 'isolated identity verified')
