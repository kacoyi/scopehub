"""Compare live renderer behavior and verify the specific retained native workload."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

from diagnostic_archive import resolve_diagnostic

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'contracts/reviews/2026-11-23-renderer-comparison.json'
FIXTURES = ROOT / 'tests/fixtures/workbench'


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def verified(path, expected):
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected, f'Digest mismatch: {path.name}')
    return data


def png_size(data):
    require(data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR', 'Invalid native PNG')
    return struct.unpack('>II', data[16:24])


def verify_ubuntu_visibility(folder, review):
    surface = review['public_surface']
    ref = surface['ubuntu_native']
    files = json.loads(verified(folder / 'visibility-index.json', ref['index_sha256']))['files']

    def original(name):
        return verified(folder / name, files[name])

    guest = verified(ROOT / surface['guest'], surface['sha256'])
    report = json.loads(original('visibility/ubuntu.json'))
    require(report['candidate'] == review['candidate'] and report['fingerprint'] == review['fingerprint']
            and original('visibility/visibility.js') == guest, 'Ubuntu visibility runtime or guest differs')
    for state in ['05-accepted', '07-timeout', '09-restored']:
        name = 'ef0a1e1-visibility-01/' + state
        width, height = png_size(original(name + '.png'))
        pixels = report['actual_pixels'][state]
        left, top, right, bottom = pixels['crop']
        require(0 <= left < right <= width and 0 <= top < bottom <= height
                and (right - left, bottom - top) == (861, 323)
                and pixels['raw_rgb_sha256'] == ref['plot_rgb_sha256'], 'Ubuntu plot observation differs')
        nodes = json.loads(original(name + '.json'))
        if state == '07-timeout':
            require(any(n.get('text') == 'The drawing exceeded its time limit.' for n in nodes),
                    'Ubuntu timeout notification missing')
        else:
            require(any(n['role'] == 'entry' and n.get('text') == guest.decode().strip() for n in nodes),
                    'Ubuntu accepted or restored source differs')


def verify_ubuntu_binding(root, review):
    reference = review['ubuntu_binding']
    folder = root / reference['directory']
    verify_ubuntu_visibility(folder, review)
    index = json.loads(verified(folder / 'ubuntu-index.json', reference['index_sha256']))
    require(index['files'][index['report']] == reference['report_sha256'], 'Ubuntu report identity differs')

    def original(name):
        return verified(folder / name, index['files'][name])

    report = json.loads(original(index['report']))
    require(report['candidate'] == review['candidate'] and report['fingerprint'] == review['fingerprint'],
            'Ubuntu accepted-series runtime differs')
    for name in ['accepted-series-dots.js', 'accepted-series-strips.js']:
        require(original('sources/' + name) == (FIXTURES / name).read_bytes(), 'Ubuntu binding guest differs')
    prefix = 'ef0a1e1-05/'
    for state in ['dots-selected', 'strips-selected', 'studio-table', 'live-revoked']:
        png_size(original(prefix + state + '.png'))
    table = original(prefix + 'studio-table.json').decode()
    revoked = original(prefix + 'live-revoked.json').decode()
    identity = report['profile_identity']
    require(identity['Output'] in table and identity['Accepted version'] in table
            and identity['Output unit'] == 'Unknown' and identity['Source volume unit'] == 'shares',
            'Ubuntu table source identity or units differ')
    require('Accepted series unavailable.' in revoked and 'Blank canvas' in revoked and
            all(label not in revoked for label in [identity['Output'], 'Undo accepted edit', 'Program geometry']),
            'Revoked Ubuntu view still presents the old output')
    return identity['Output']


def verify_evidence(root, review):
    # The pinned private manifests list synthetic files. Only open named inputs.
    native_ref = review['native_index']
    native_dir = root / native_ref['directory']
    native = json.loads(verified(native_dir / 'native-index.json', native_ref['sha256']))
    hosted = json.loads(verified(native_dir / 'hosted-index.json', native_ref['hosted_sha256']))

    def original(name):
        return verified(native_dir / name, native['retained'][name]['sha256'])

    for file, guest in [('initial.js', 'volume-pennants.js'), ('revised.js', 'volume-pennants-revised.js')]:
        require(original('guest-02/' + file) == (FIXTURES / guest).read_bytes(), 'Native guest bytes differ')
    ubuntu = json.loads(original('fresh-native-01/observations.json'))
    case = json.loads(original('v2-report.json'))
    require(ubuntu['source'] == hosted['candidate'] == review['pennant_runtime'], 'Native versions differ')
    require(case['fingerprint'] == hosted['runtime_sha256'] == review['pennant_fingerprint'], 'Native runtime differs')
    measurements = json.loads(original('fresh-pixels.json'))['native']
    require({m['image'] for m in measurements} == {f'{frame}-{width}' for frame in ['fit', 'wide', 'refitted']
            for width in [980, 1280]}, 'Missing Ubuntu viewport or window')
    for measurement in measurements:
        width, height = png_size(original('fresh-native-01/' + measurement['image'] + '-plot.png'))
        require((width, height) == (measurement['width'], measurement['height']) and height > 100,
                'Wrong Ubuntu capture dimensions')
        points = measurement['points']
        require(len(points) == 2 and all(abs(p['measured_length_px'] - expected * width / 640) <= 2
                for p, expected in zip(points, [24, 16])), 'Ubuntu poles differ from frozen volumes')
    revised = original('fresh-native-01/revised-plot.png')
    require(revised == original('fresh-native-01/restored-plot.png') == original('fresh-native-01/retained-plot.png')
            and revised != original('fresh-native-01/undone-plot.png'), 'Ubuntu fallback or undo differs')

    windows = hosted['platforms']['windows']
    diagnostic = resolve_diagnostic({'run_id': windows['run_id'], 'source_commit': hosted['head'],
                                     'report_sha256': windows['report_sha256']}, root)
    require(diagnostic['selected_check_statuses']['workbench-drawing-runtime'] == 'passed', 'Windows native check failed')
    frames = {f['name']: f for f in hosted['native_windows']['frames']}
    for name in ['fit.png', 'expanded.png', 'revised.png', 'restored.png']:
        frame = frames[name]
        data = verified(Path(diagnostic['report_path']).parent / 'webview2-drawing' / name, frame['sha256'])
        require(png_size(data) == (frame['width'], frame['height']) and frame['height'] > 100,
                'Wrong Windows capture dimensions')
    require(frames['fit.png']['width'] > frames['expanded.png']['width']
            and frames['fit.png']['sha256'] != frames['revised.png']['sha256'] == frames['restored.png']['sha256'],
            'Windows resizing, scoped revision or recovery differs')

    ref = review['independent_binding']
    directory = root / ref['directory']
    manifest = json.loads(verified(directory / 'manifest.json', ref['sha256']))
    path = manifest['observation']
    observed = json.loads(verified(directory / path, manifest['files_sha256'][path]))
    require(observed['candidate'] == review['candidate'] and observed['fingerprint'] == review['fingerprint'],
            'Accepted-series observation belongs to a different runtime')
    accepted = observed['accepted']
    series = next(s for s in accepted['document']['artifact']['result']['series'] if s['id'] == 'mean')
    require([(p['time_ms'], p['value']) for p in series['points']] ==
            [(1770076800000 + i * 86400000, value) for i, value in enumerate([None, 115, 125, 135])],
            'Changed accepted series points')
    require((observed['dots'], observed['strips'], observed['missingMarks']) == (3, 3, 1)
            and observed['denied'] == 'PERMISSION_DENIED' and observed['delayedPreRevocationOutput'] == 'released'
            and observed['listing']['layers'] == [{'layer_id': accepted['layer_id'], 'version_id': accepted['version_id'],
                                                  'available': False}], 'Missing rendering or revocation evidence')
    ubuntu_hash = verify_ubuntu_binding(root, review)
    return {'ubuntu_native_binding_result_hash': ubuntu_hash, 'paired_native_pennants': hosted['candidate'], 'ubuntu_host': ubuntu['native_host'],
            'windows_webview2': hosted['native_windows']['webview2'], 'browser_binding_version': accepted['version_id'],
            'browser_binding_result_hash': accepted['document']['artifact']['result_hash']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root', type=Path, default=os.getenv('SCOPEHUB_ARCHIVE_ROOT'))
    parser.add_argument('--retained-only', action='store_true', help='Inspect archived observations without browser replay.')
    args = parser.parse_args()
    review = json.loads(REVIEW.read_text(encoding='utf-8'))
    if not args.retained_only:
        pnpm = shutil.which('pnpm')
        if not pnpm:
            print('The shared live workload requires pnpm.')
            return 77
        workload = subprocess.run([pnpm, 'exec', 'playwright', 'test', '--config',
                                   'tests/ui/renderer-comparison.config.mjs'], cwd=ROOT, check=False)
        if workload.returncode:
            return 1
    if not args.archive_root:
        print('Native comparison unavailable. Set SCOPEHUB_ARCHIVE_ROOT to the retained development archive.')
        return 77
    try:
        observations = verify_evidence(args.archive_root, review)
    except FileNotFoundError as error:
        print(f'Retained comparison input unavailable: {error.filename}')
        return 77
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print(f'Retained comparison rejected: {error}')
        return 1
    runtime_paths = ['apps/desktop/ui', 'apps/desktop/src-tauri/tauri.conf.json',
                     'apps/desktop/src-tauri/capabilities', 'pnpm-lock.yaml']
    listing = subprocess.check_output(['git', 'ls-tree', '-r', 'HEAD', '--', *runtime_paths], cwd=ROOT)
    changes = subprocess.check_output(['git', 'status', '--porcelain', '--', *runtime_paths], cwd=ROOT)
    if changes or hashlib.sha256(listing).hexdigest() != review['fingerprint']:
        print('Historical artifacts verified. The measured browser runtime differs from this checkout.')
        return 77
    print(json.dumps({'live_shared_workload': 'omitted' if args.retained_only else 'passed',
                      'verified': observations, 'selection': review['recommendation'], 'open': review['open']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
