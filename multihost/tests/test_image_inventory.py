"""Kiểm allowlist SL mới và bundle ghim digest; không mạng hoặc Docker lifecycle."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_tooling import tool, fixture, HAS_SWOP, WORK, ROOT
import runtime_contract as contract


@unittest.skipUnless(HAS_SWOP, 'Existing SWOP v2 package required')
class ImageInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=WORK)
        self.root = Path(self.tmp.name)
        (self.root / 'assets').mkdir()
        (self.root / 'assets/initial.iutfw').write_bytes((ROOT / 'assets/initial.iutfw').read_bytes())
        self.profile = self.root / 'profile.json'
        self.data = fixture()
    def tearDown(self):
        self.tmp.cleanup()
    def save(self):
        self.profile.write_bytes(tool.encoded(self.data))
    def sl(self, ref):
        for service in self.data['services']:
            if service['role'] == 'sl': service['image'] = ref
        self.save()
    def test_old_version_still_valid(self):
        self.save(); tool.validate_profile(self.profile)
    def test_new_tag_valid(self):
        self.sl(tool.SL_V11); tool.validate_profile(self.profile)
    def test_reviewed_pin_valid(self):
        self.sl(tool.SL_V11_PINNED); tool.validate_profile(self.profile)
    def test_other_tag_rejected(self):
        self.sl('24520115/independent-sl:v0.1.2-arm64')
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)
    def test_other_digest_rejected(self):
        self.sl(tool.SL_V11 + '@sha256:' + '0' * 64)
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)
    def test_other_namespace_rejected(self):
        self.sl('someone/independent-sl:v0.1.1-arm64')
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)
    def test_mixed_peer_versions_rejected(self):
        self.sl(tool.SL_V11)
        next(s for s in self.data['services'] if s['id'] == 'sl-b')['image'] = tool.IMAGES['sl']
        self.save()
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)
    def test_other_role_cannot_upgrade(self):
        self.sl(tool.SL_V11_PINNED)
        next(s for s in self.data['services'] if s['role'] == 'sn')['image'] = '24520115/independent-sn:v0.1.1-arm64'
        self.save()
        with self.assertRaises(ValueError): tool.validate_profile(self.profile)
    def test_digest_bundle_import_and_env_round_trip(self):
        self.sl(tool.SL_V11_PINNED)
        seal = tool.prepare(self.profile, self.root / 'bundle')
        verified = tool.verify(self.root / 'bundle', seal['bundle_id'])
        seal2 = tool.prepare(self.profile, self.root / 'second')
        self.assertEqual(seal['bundle_id'], seal2['bundle_id'])
        for host in tool.HOSTS:
            output = self.root / host
            tool.import_host(self.root / 'bundle', host, output, seal['bundle_id'], 'defer')
            env_path = self.root / (host + '.env')
            contract.configure(output, host, seal['bundle_id'], env_path)
            ctx = contract.load_context(host, env_path)
            self.assertEqual(ctx.env['SL_IMAGE'], tool.SL_V11_PINNED)
            service = json.loads(verified.files[host + '/docker-compose.yaml'])['services']['sl-a' if host == 'rpi5' else 'sl-b']
            self.assertEqual(service['entrypoint'], ['python', '/swop-runtime/sl_entry.py'])
            # SWOP dùng biến env trong Compose; bản ghim phải nằm đúng env của host.
            variable = 'SWOP_SL_A_IMAGE' if host == 'rpi5' else 'SWOP_SL_B_IMAGE'
            self.assertIn('${' + variable, service['image'])
            self.assertIn(variable + '=' + tool.SL_V11_PINNED,
                          verified.files[host + '/.env.example'].decode('utf-8'))
    def test_canonical_env_rejects_image_override(self):
        self.sl(tool.SL_V11_PINNED)
        seal = tool.prepare(self.profile, self.root / 'bundle')
        tool.import_host(self.root / 'bundle', 'rpi5', self.root / 'host', seal['bundle_id'], 'defer')
        env = self.root / 'host.env'
        contract.configure(self.root / 'host', 'rpi5', seal['bundle_id'], env)
        env.write_text(env.read_text(encoding='utf-8').replace(tool.SL_V11_INDEX, 'sha256:' + '0' * 64), encoding='utf-8')
        with self.assertRaises(ValueError): contract.load_context('rpi5', env)


if __name__ == '__main__': unittest.main()
