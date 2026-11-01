"""Read-only Docker Registry v2 verification; never pulls layers or stores tokens."""
import concurrent.futures
import datetime
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request

EXPECTED = {
    'sn': '2c426c892cf5da693f1b96ab2f77400c9970b08a6effde15dd8d8ae9dcaa5984',
    'sl': 'ca0c22b20dbf220868ae9188a9c30c3f13db25f0dbffaf2eef00559813aa6eb9',
    'user-tf': '7e2edf5051b0bc03259de61c59d1d3775bd196c3f4a82cdf4a1b4f5c0a9fc16b',
    'swop': 'd2810148d8142ac7f679e4f263534643a36b8020560dde66309de7e5128284e5',
}
ACCEPT = ', '.join(['application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json'])

def probe(name):
    repo = '24520115/independent-' + name
    result = {'image': repo + ':v0.1.0-arm64', 'status': 'NOT VERIFIED',
              'expected_config': 'sha256:' + EXPECTED[name]}
    try:
        with urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:' + repo + ':pull', timeout=30) as r:
            token = json.load(r)['token']
        def fetch(kind, ref):
            request = urllib.request.Request('https://registry-1.docker.io/v2/' + repo + '/' + kind + '/' + ref,
                headers={'Authorization': 'Bearer ' + token, 'Accept': ACCEPT})
            with urllib.request.urlopen(request, timeout=30) as r:
                raw = r.read(); claimed = r.headers.get('Docker-Content-Digest')
            digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
            if claimed and claimed != digest:
                raise ValueError('Registry content digest mismatch')
            if ref.startswith('sha256:') and ref != digest:
                raise ValueError('Requested digest mismatch')
            return json.loads(raw), digest
        index, digest = fetch('manifests', 'v0.1.0-arm64')
        result['tag_digest'] = digest
        if 'manifests' in index:
            choices = [m for m in index['manifests'] if m.get('platform', {}).get('os') == 'linux'
                       and m.get('platform', {}).get('architecture') == 'arm64']
            if len(choices) != 1:
                raise ValueError('Expected exactly one linux/arm64 platform manifest')
            manifest, digest = fetch('manifests', choices[0]['digest'])
        else:
            manifest = index
        result['platform_manifest_digest'] = digest
        config, config_digest = fetch('blobs', manifest['config']['digest'])
        result.update(config_digest=config_digest, os=config.get('os'), architecture=config.get('architecture'),
                      entrypoint=config.get('config', {}).get('Entrypoint'),
                      cmd=config.get('config', {}).get('Cmd'), user=config.get('config', {}).get('User'))
        result['accepted_image_match'] = config_digest == result['expected_config']
        result['status'] = 'PASS' if result['accepted_image_match'] and result['os'] == 'linux' and result['architecture'] == 'arm64' else 'BLOCKED'
        result['reason'] = 'Registry metadata/config identity only; no pull or runtime test' if result['status'] == 'PASS' else 'Architecture or accepted config identity differs'
    except urllib.error.HTTPError as exc:
        result.update(status='BLOCKED' if exc.code == 404 else 'NOT VERIFIED', http_status=exc.code,
                      reason='Tag not found' if exc.code == 404 else 'Registry inaccessible; existence not established')
    except Exception as exc:
        result['reason'] = type(exc).__name__ + ': ' + str(exc)
    return result

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        images = list(pool.map(probe, EXPECTED))
    report = {'checked_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'method': 'Read-only Registry v2 token/manifest/config HTTP; no Docker pull', 'images': images}
    output = Path(__file__).parent / 'evidence' / 'registry-status.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for item in images:
        print(item['image'], item['status'], item.get('reason', ''))
