"""Replay số học có giới hạn trong User-TF, sau khi hai client đã kết thúc.

Huấn luyện lại từ cấu hình và global checkpoint đã lưu, rồi tính FedAvg độc lập
bằng NumPy float64, có trọng số theo số mẫu. Không gọi hoặc sửa SN/SL, không
upload mô hình, mở round hay ghi lại checkpoint. Replay vẫn có tính toán ML.
"""
import json
from pathlib import Path

def average(updates):
    import numpy as np
    total = sum(samples for _, samples in updates)
    return tuple((sum(w[i].astype(np.float64) * n for w, n in updates) / total).astype(np.float32)
                 for i in range(len(updates[0][0])))

def main():
    import numpy as np
    from user_tf.model import LocalTrainer, synthetic_data
    from user_tf.spec import ModelSpec
    from user_tf.weights import deserialize
    root = Path('/deployment/bundle')
    profile = json.loads((root / 'profile.json').read_text(encoding='utf-8'))
    spec = ModelSpec(**profile['bootstrap']['model'])
    bootstrap = deserialize((root / 'assets/initial.iutfw').read_bytes(), spec).weights
    clients = sorted((s for s in profile['services'] if s['role'] == 'user_tf'), key=lambda s: s['id'])
    records = []
    for rid in (1, 2):
        updates = []
        for suffix, service in zip(('a', 'b'), clients):
            cfg = json.loads((root / service['host'] / 'config' / (service['id'] + '.json')).read_text(encoding='utf-8'))
            p = cfg['params']
            base = bootstrap if rid == 1 else deserialize(Path('/checkpoint-' + suffix + '/round-1.iutfw').read_bytes(), spec).weights
            trainer = LocalTrainer(spec=spec, seed=p['model_seed'], learning_rate=p['learning_rate'])
            trainer.set_weights(base, 0)
            x, y = synthetic_data(p['samples'], spec.features, seed=p['data_seed'])
            before = trainer.evaluate(x, y)['mse']
            trainer.train(x, y, epochs=p['epochs'], batch_size=p['batch_size'])
            updates.append((trainer.get_weights(), p['samples']))
            records.append({'round_id': rid, 'client': service['id'], 'samples': p['samples'],
                            'local_mse_before': before, 'local_mse_after': trainer.evaluate(x, y)['mse']})
        expected = average(updates)
        for suffix in ('a', 'b'):
            actual = deserialize(Path('/checkpoint-' + suffix + '/round-' + str(rid) + '.iutfw').read_bytes(), spec).weights
            error = max(float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64)))) for a, b in zip(actual, expected))
            if not np.isfinite(error) or error > 1e-6:
                raise ValueError('Numeric replay differs: investigate inputs/kernels/version; max error=' + str(error))
            print(json.dumps({'round_id': rid, 'client': 'client-' + suffix, 'max_abs_fedavg_error': error, 'tolerance': 1e-6}))
    print(json.dumps({'status': 'PASS', 'scope': 'read-only independent numeric replay against BOTH global checkpoints in two rounds', 'training': records}))

if __name__ == '__main__':
    main()
