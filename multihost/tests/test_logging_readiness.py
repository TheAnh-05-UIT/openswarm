"""Regression logs/preflight với IO giả; không cần SWOP hoặc Docker daemon."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operations
import preflight
import runtime_contract as contract


def passing(host='rpi5'):
    names = ['bundle_host_identity', 'host_architecture', 'local_host_engine_compose',
             'resource_ownership', 'private_interface', 'peer_route']
    names.extend('port_' + r for r in contract.ROLES[host] if not r.startswith('client-'))
    return {'checks': [{'check': n, 'status': 'PASS'} for n in names], 'deployment_authorized': False}


class ReadinessTests(unittest.TestCase):
    def test_both_hosts_pass(self):
        for host in contract.ROLES:
            report = passing(host)
            self.assertEqual(preflight.readiness(report, host), 0)
            self.assertEqual(report['readiness']['status'], 'PASS')
            self.assertFalse(report['deployment_authorized'])

    def test_mixed_mandatory_fail(self):
        for name in ('local_host_engine_compose', 'resource_ownership', 'peer_route', 'port_sl-a'):
            report = passing()
            next(c for c in report['checks'] if c['check'] == name)['status'] = 'FAIL'
            self.assertEqual(preflight.readiness(report, 'rpi5'), 2)

    def test_unknown_not_tested_missing(self):
        for value in ('UNKNOWN', 'NOT TESTED', 'REQUIRES PHYSICAL TESTING', 'unexpected'):
            report = passing(); report['checks'][0]['status'] = value
            self.assertEqual(preflight.readiness(report, 'rpi5'), 3)
        self.assertEqual(preflight.readiness({'checks': []}, 'rpi5'), 3)

    def test_fail_precedes_incomplete(self):
        report = passing(); report['checks'][0]['status'] = 'UNKNOWN'; report['checks'][1]['status'] = 'FAIL'
        self.assertEqual(preflight.readiness(report, 'rpi5'), 2)

    def test_observation_not_a_physical_pass(self):
        report = passing()
        report['checks'].extend([{'check': 'native_multihost_training', 'status': 'REQUIRES PHYSICAL TESTING'},
                                {'check': 'container_network_paths', 'status': 'NOT TESTED'},
                                {'check': 'host_endpoint_round-controller', 'status': 'FAIL'}])
        self.assertEqual(preflight.readiness(report, 'rpi5'), 0)
        self.assertIn('native_multihost_training', report['readiness']['observation_checks'])

    def test_requested_http_probes_are_mandatory(self):
        report = passing()
        self.assertEqual(preflight.readiness(report, 'rpi5', True), 3)
        for role in ('sn', 'sl-a', 'sl-b'):
            report['checks'].append({'check': 'host_endpoint_' + role, 'status': 'PASS'})
        self.assertEqual(preflight.readiness(report, 'rpi5', True), 0)
        report['checks'][-1]['status'] = 'FAIL'
        self.assertEqual(preflight.readiness(report, 'rpi5', True), 2)

    def test_cli_exit_json_contract(self):
        for status, expected in [('PASS', 0), ('FAIL', 2), ('UNKNOWN', 3), ('NOT TESTED', 3)]:
            report = passing(); report['checks'][0]['status'] = status; out = io.StringIO()
            with patch.object(contract, 'load_context', return_value=SimpleNamespace(host='rpi5')), \
                 patch.object(preflight, 'observations', return_value=report), contextlib.redirect_stdout(out):
                code = operations.main(['preflight', '--host', 'rpi5', '--env', 'MOCK.env'])
            self.assertEqual(code, expected)
            parsed = json.loads(out.getvalue())
            self.assertEqual(parsed['readiness']['exit_code'], expected)
            self.assertFalse(parsed['deployment_authorized'])


class LogSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(host='rpi5', project='MOCK', env={}, env_path=Path('MOCK.env'),
                                   compose=contract.ROOT / 'compose.host-a.yaml')
        self.capture = preflight.Capture('2026-11-05T10:00:00Z MOCK\n', '', 0, 26, 26, 1024)
        self.runner = SimpleNamespace(capture=Mock(side_effect=lambda *a, **k: copy.copy(self.capture)))

    def cli(self, *options, existing=None, ownership_error=False):
        out = io.StringIO()
        own = patch.object(preflight, 'owned', side_effect=ValueError('Foreign ownership')) if ownership_error else \
            patch.object(preflight, 'owned', return_value=({'sn': {}} if existing is None else existing, False))
        with patch.object(contract, 'load_context', return_value=self.ctx), \
             patch.object(preflight, 'Runner', return_value=self.runner), \
             patch.object(preflight, 'local_engine'), own, contextlib.redirect_stdout(out):
            code = operations.main(['logs', '--host', 'rpi5', '--env', 'MOCK.env', *options])
        return code, json.loads(out.getvalue())

    def test_timestamps_owned_services_only(self):
        code, result = self.cli(existing={'sl-a': {}, 'sn': {}})
        self.assertEqual(code, 0)
        command = self.runner.capture.call_args.args[0]
        self.assertIn('--timestamps', command); self.assertNotIn('--follow', command)
        self.assertEqual(command[-2:], ['sl-a', 'sn'])
        self.assertEqual(command[command.index('--tail') + 1], '100')
        self.assertTrue(result['capture']['complete'])

    def test_since_exact_boundary(self):
        since = '2026-11-05T10:00:00.123456789Z'
        code, result = self.cli('--since', since)
        self.assertEqual(code, 0)
        command = self.runner.capture.call_args.args[0]
        self.assertEqual(command[command.index('--since') + 1], since)
        self.assertTrue(result['boundary_overlap_possible'])

    def test_invalid_since_before_context_or_engine(self):
        for value in ('1m', '2026-02-30T00:00:00Z', '2026-11-05T25:00:00Z',
                      '2026-11-05T10:00:00+07:00', '$(touch X)', '2026-11-05T10:00:00Z --follow'):
            with patch.object(contract, 'load_context') as context:
                code, result = self.cli('--since', value)
                self.assertEqual(code, 2); context.assert_not_called()
                self.assertEqual(result['status'], 'FAIL')
        self.runner.capture.assert_not_called()

    def test_invalid_tail(self):
        for value in ('0', '1001'):
            code, _ = self.cli('--tail', value); self.assertEqual(code, 2)
        self.runner.capture.assert_not_called()

    def test_repeated_snapshot_allows_boundary_overlap(self):
        code, first = self.cli('--since', '2026-11-05T10:00:00Z')
        next_code, second = self.cli('--since', '2026-11-05T10:00:00Z')
        self.assertEqual((code, next_code), (0, 0))
        self.assertEqual(first['logs'], second['logs'])
        self.assertTrue(second['boundary_overlap_possible'])

    def test_missing_services_no_unrelated_logs(self):
        code, result = self.cli(existing={})
        self.assertEqual((code, result['logs']), (0, ''))
        self.runner.capture.assert_not_called()

    def test_foreign_ownership_blocks_logs(self):
        code, result = self.cli(ownership_error=True)
        self.assertEqual((code, result['status']), (2, 'FAIL'))
        self.runner.capture.assert_not_called()

    def test_partial_capture_returns_prefix_and_nonzero(self):
        for flag in ('truncated', 'timed_out', 'decode_replaced', 'read_error'):
            self.capture = preflight.Capture('MOCK prefix', '', -9, 11, 12, 1024)
            setattr(self.capture, flag, True)
            code, result = self.cli()
            self.assertEqual((code, result['logs']), (3, 'MOCK prefix'))
            self.assertFalse(result['capture']['complete'])

    def test_nonzero_preserves_stderr(self):
        self.capture = preflight.Capture('', 'MOCK diagnostic', 7, 15, 15, 1024)
        code, result = self.cli()
        self.assertEqual((code, result['stderr']), (2, 'MOCK diagnostic'))


if __name__ == '__main__': unittest.main(verbosity=2)
