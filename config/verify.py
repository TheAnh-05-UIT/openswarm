"""Read-only run evidence verification in the SWOP image; no Docker API access."""
import hashlib
import json
from pathlib import Path
import struct
import urllib.request

def require(ok, message):
    if not ok:
        raise ValueError(message)

def read_json(path):
    return json.loads(Path(path).read_text())

def bounded(path, limit):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'Evidence exceeds size limit')
    return raw

def inspect_journal(events, profile, bootstrap):
    require(0 < len(events) <= 20000, 'Missing/oversize journal')
    require([e['sequence'] for e in events] == list(range(len(events))), 'Journal sequence mismatch')
    require(not any(e['event'] in ('RUN_FAILED', 'CREATE_UNCERTAIN_OBSERVED') for e in events), 'Failed or ambiguous run')
    started = [e for e in events if e['event'] == 'CONTROLLER_STARTED']
    require(len(started) == 1 and started[0]['run_id'] == profile['deployment']['run_id']
            and started[0]['job_id'] == profile['job_id'], 'Wrong run/job journal')
    completed = [e for e in events if e['event'] == 'ROUND_COMPLETED']
    require([e['round_id'] for e in completed] == [1, 2], 'Exactly two completed rounds required')
    end = [e for e in events if e['event'] == 'RUN_COMPLETED']
    require(len(end) == 1 and end[0]['rounds'] == 2 and end[0]['sequence'] > completed[-1]['sequence'], 'Run not complete')
    services = {s['id']: s for s in profile['services']}
    samples = {services[s['sl']]['peer_id']: s['params']['samples'] for s in services.values() if s['role'] == 'user_tf'}
    peers = sorted(samples)
    receipts = {}
    epoch = None
    header_size = struct.unpack('>I', bootstrap[8:12])[0]
    model = json.loads(bootstrap[12:12 + header_size])['model_identity']
    tensor = hashlib.sha256(bootstrap[12 + header_size:]).hexdigest()
    base = hashlib.sha256(bootstrap).hexdigest()
    for index, record in enumerate(completed):
        rid = index + 1
        creates = [e for e in events if e['event'] == 'ROUND_CREATED' and e['round_id'] == rid]
        intents = [e for e in events if e['event'] == 'CREATE_INTENT' and e['round_id'] == rid]
        require(len(creates) == 1 and intents and max(e['sequence'] for e in intents) < creates[0]['sequence'] < record['sequence'], 'Round creation/order mismatch')
        if index:
            require(min(e['sequence'] for e in intents) > completed[index - 1]['sequence'], 'Next round opened before completion')
        require(record['base_sha256'] == base and record['global_version'] == rid
                and record['aggregator'] == peers[index % len(peers)] and record['clients_completed'] == peers, 'Global model/aggregator mismatch')
        rows = [e for e in events if e['event'] == 'CLIENT_COMPLETED' and e['receipt']['round_id'] == rid]
        require(len(rows) == len(peers) and sorted(e['receipt']['peer_id'] for e in rows) == peers, 'Missing/duplicate receipts')
        for event in rows:
            receipt = event['receipt']; peer = receipt['peer_id']
            require(creates[0]['sequence'] < event['sequence'] < record['sequence'], 'Receipt outside round creation/completion')
            if epoch is None:
                epoch = receipt['sn_instance_id']
            require(receipt['protocol_version'] == 1 and receipt['run_id'] == profile['deployment']['run_id']
                    and receipt['job_id'] == profile['job_id'] and receipt['sn_instance_id'] == epoch
                    and receipt['model_id'] == model and receipt['samples'] == samples[peer]
                    and receipt['base_version'] == rid - 1 and receipt['global_version'] == rid
                    and receipt['base_sha256'] == base and receipt['global_sha256'] == record['global_sha256']
                    and receipt['base_tensor_sha256'] == receipt['training_start_tensor_sha256'] == tensor
                    and receipt['global_tensor_sha256'] == record['client_tensor_sha256'], 'Receipt context/application/carry-over mismatch')
            receipts[(rid, peer)] = receipt
        base = record['global_sha256']; tensor = record['client_tensor_sha256']
    return completed, receipts, epoch, model

def get(url):
    # URLs are fixed by the generated, validated profile, not untrusted redirects.
    with urllib.request.urlopen(url, timeout=5) as response:
        raw = response.read(131073)
    require(len(raw) <= 131072, 'HTTP response exceeds bound')
    return json.loads(raw)

def main():
    from swop.controller_artifact import tensor_digest
    from swop.profile import load_profile, endpoint
    bundle = Path('/deployment/bundle')
    profile = load_profile(bundle / 'profile.json').data
    ready = read_json('/deployment/READY.json')
    require(ready['run_id'] == profile['deployment']['run_id'], 'Wrong initialized run')
    raw = bounded('/controller-data/rounds.jsonl', 16 * 1024 * 1024)
    events = [json.loads(line) for line in raw.splitlines()]
    records, receipts, epoch, model = inspect_journal(events, profile, bounded(bundle / 'assets/initial.iutfw', 65536))
    for suffix in ('a', 'b'):
        for rid in (1, 2):
            raw = bounded('/checkpoint-' + suffix + '/round-' + str(rid) + '.iutfw', 65536)
            require(tensor_digest(raw, model, None) == receipts[(rid, 'sl-' + suffix)]['global_tensor_sha256'], 'Checkpoint differs from applied global tensors')
    hosts = {h['id']: h for h in profile['hosts']}
    sn = next(s for s in profile['services'] if s['role'] == 'sn')
    current = get(endpoint(sn, hosts) + '/v1/rounds/current')
    require(current['instance_id'] == epoch and current['round_id'] == 2 and current['status'] == 'synchronized', 'SN no longer matches completed run')
    for service in profile['services']:
        if service['role'] == 'sl':
            s = get(endpoint(service, hosts) + '/v1/ml/session')
            require(s['sn_instance_id'] == epoch and s['job_id'] == profile['job_id'] and s['model_id'] == model
                    and s['committed']['version'] == 2 and s['committed']['weights_sha256'] == records[-1]['global_sha256']
                    and s['round']['phase'] == 'completed' and s['round']['applied'] is True, 'SL commit mismatch')
    print(json.dumps({'status': 'PASS', 'scope': 'two rounds, four receipts, checkpoints, application/carry-over, current SN/SL',
                      'run_id': ready['run_id'], 'rounds': records, 'fedavg_arithmetic': 'Run verify-fedavg separately',
                      'native_arm64': 'Requires operator evidence of physical CPU/local Engine'}, indent=2))

if __name__ == '__main__':
    main()
