"""Chụp ID/trạng thái Docker ở chế độ chỉ đọc; không thu env hay credentials."""
import datetime
import json
from pathlib import Path
import subprocess
import sys

def read(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE).splitlines()

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode not in ('before', 'after'):
        raise SystemExit('before or after only')
    result = {'checked_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'Read-only IDs/states; not a continuous event audit',
              'context': read('context', 'show'), 'engine': read('info', '--format', '{{.OSType}}/{{.Architecture}}'),
              'containers': sorted(read('container', 'ls', '--all', '--no-trunc', '--format', '{{.ID}} {{.State}}')),
              'images': sorted(set(read('image', 'ls', '--quiet', '--no-trunc'))),
              'networks': sorted(read('network', 'ls', '--no-trunc', '--format', '{{.ID}}')),
              'volumes': sorted(read('volume', 'ls', '--quiet'))}
    folder = Path(__file__).parent / 'evidence'
    folder.mkdir(exist_ok=True)
    (folder / ('docker-' + mode + '.json')).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print({key: len(result[key]) for key in ['containers', 'images', 'networks', 'volumes']})
    if mode == 'after':
        before = json.loads((folder / 'docker-before.json').read_text(encoding='utf-8'))
        changes = [key for key in ['context', 'engine', 'containers', 'images', 'networks', 'volumes'] if before[key] != result[key]]
        audit = {'status': 'PASS' if not changes else 'ENVIRONMENT EXCEPTION', 'changed_sections': changes,
                 'scope': 'IDs/container states between two read-only snapshots only; no causal attribution'}
        (folder / 'docker-isolation.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')
        print(audit)
