"""Exercise CI's inline commands with isolated manifests and Cargo fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / 'workflows/ci.yml').read_text())


def step(job, name):
    return next(s['run'] for s in WORKFLOW['jobs'][job]['steps'] if s.get('name') == name)


class CiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            **os.environ,
            'GITHUB_OUTPUT': str(self.root / 'output'),
            'CARGO_TARGET_DIR': str(self.root / 'target'),
            'CARGO_NET_OFFLINE': 'true',
            'ALL_FEATURES': 'false',
            'CARGO_LOG': str(self.root / 'commands'),
        }

    def run_script(self, script, *, env=None, success=True):
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=self.root, env={**self.env, **(env or {})}, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def mock_cargo(self):
        directory = self.root / 'tools'
        directory.mkdir()
        cargo = directory / 'cargo'
        cargo.write_text('#!/usr/bin/env python3\nimport json, os, sys\nwith open(os.environ["CARGO_LOG"], "a") as log:\n print(json.dumps(sys.argv[1:]), file=log)\nif sys.argv[1] == os.environ.get("FAIL_COMMAND"):\n sys.exit(7)\n')
        cargo.chmod(0o755)
        self.env['PATH'] = str(directory) + os.pathsep + os.environ['PATH']

    def test_msrv_uses_declared_version_and_normalizes_minor_versions(self):
        for declared, expected in [('1.88', '1.88.0'), ('1.90.0', '1.90.0'), ('1.90.1', '1.90.1')]:
            with self.subTest(declared=declared):
                (self.root / 'Cargo.toml').write_text(f'[package]\nname = "fixture"\nrust-version = "{declared}"\n')
                self.run_script(step('msrv', 'Read declared minimum Rust version'))
                self.assertEqual((self.root / 'output').read_text().splitlines()[-1], 'toolchain=' + expected)

    def test_msrv_rejects_missing_inherited_or_invalid_versions(self):
        for value in ['', 'rust-version = { workspace = true }', 'rust-version = 190', 'rust-version = "stable"', 'rust-version = "1.090"', 'rust-version = "1.90.0\\ninjected=value"']:
            with self.subTest(value=value):
                (self.root / 'Cargo.toml').write_text('[package]\nname = "fixture"\n' + value + '\n')
                result = self.run_script(step('msrv', 'Read declared minimum Rust version'), success=False)
                self.assertIn('explicit rust-version is required', result.stderr)

    def test_default_and_optional_feature_flags_reach_all_compiler_checks(self):
        self.mock_cargo()
        for enabled in ['false', 'true']:
            with self.subTest(all_features=enabled):
                (self.root / 'commands').write_text('')
                self.run_script(step('check', 'Clippy and tests'), env={'ALL_FEATURES': enabled})
                self.run_script(step('msrv', 'Check all targets on minimum Rust'), env={'ALL_FEATURES': enabled})
                calls = [json.loads(line) for line in (self.root / 'commands').read_text().splitlines()]
                self.assertEqual([args[0] for args in calls], ['clippy', 'test', 'check'])
                for args in calls:
                    self.assertIn('--locked', args)
                    self.assertEqual('--all-features' in args, enabled == 'true')
                self.assertIn('--all-targets', calls[0])
                self.assertNotIn('--all-targets', calls[1])
                self.assertIn('--all-targets', calls[2])
                self.assertEqual(calls[0][-3:], ['--', '-D', 'warnings'])

    def test_failed_clippy_stops_before_tests(self):
        self.mock_cargo()
        self.run_script(step('check', 'Clippy and tests'), env={'FAIL_COMMAND': 'clippy'}, success=False)
        calls = [json.loads(line) for line in (self.root / 'commands').read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'clippy')

    def test_native_platforms_run_tests_with_requested_features(self):
        self.assertEqual(WORKFLOW['jobs']['platform']['strategy']['matrix']['os'],
                         ['windows-latest', 'macos-latest'])
        self.mock_cargo()
        for enabled in ['false', 'true']:
            (self.root / 'commands').write_text('')
            self.run_script(step('platform', 'Native platform tests'), env={'ALL_FEATURES': enabled})
            calls = [json.loads(line) for line in (self.root / 'commands').read_text().splitlines()]
            self.assertEqual(calls, [['test', '--locked'] + (['--all-features'] if enabled == 'true' else [])])

    def test_registry_package_failure_is_a_ci_failure(self):
        self.mock_cargo()
        self.run_script(step('package', 'Verify registry package'), env={'FAIL_COMMAND': 'package'}, success=False)
        calls = [json.loads(line) for line in (self.root / 'commands').read_text().splitlines()]
        self.assertEqual(calls, [['package', '--locked']])

    def test_package_ci_checks_default_and_optional_features(self):
        self.mock_cargo()
        self.run_script(step('package', 'Verify registry package'), env={'ALL_FEATURES': 'true'})
        calls = [json.loads(line) for line in (self.root / 'commands').read_text().splitlines()]
        self.assertEqual(calls, [['package', '--locked'], ['package', '--locked', '--all-features']])

    def test_package_policy_checks_extracted_archive_and_propagates_failure(self):
        self.cargo_fixture('pub fn fixture() {}\n')
        self.run_script('cargo package --offline --locked')
        (self.root / 'deny.toml').write_text('[advisories]\n')
        self.mock_cargo()
        cargo = self.root / 'tools/cargo'
        cargo.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1] == 'metadata':
    root = Path(os.environ['FIXTURE_ROOT'])
    print(json.dumps({'packages': [{'name': 'ci-fixture', 'version': '0.1.0', 'manifest_path': str(root / 'Cargo.toml')}],
                      'target_directory': str(root / 'target')}))
else:
    Path(os.environ['CARGO_LOG']).write_text(json.dumps({'args': sys.argv[1:], 'cwd': str(Path.cwd()), 'policy': Path('deny.toml').read_text()}))
    sys.exit(int(os.environ.get('DENY_EXIT', '0')))
''')
        env = {'RUNNER_TEMP': str(self.root), 'FIXTURE_ROOT': str(self.root)}
        self.run_script(step('package', 'Verify packaged dependency policy'), env=env)
        call = json.loads((self.root / 'commands').read_text())
        self.assertNotEqual(Path(call['cwd']), self.root)
        self.assertEqual(call['args'], ['deny', '--locked', '--all-features', 'check'])
        self.assertEqual(call['policy'], (self.root / 'deny.toml').read_text())
        parsed = subprocess.run(['cargo', *call['args'], '--help'], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.run_script(step('package', 'Verify packaged dependency policy'), env={**env, 'DENY_EXIT': '7'}, success=False)

    def test_failed_tests_and_msrv_checks_are_not_ignored(self):
        self.mock_cargo()
        self.run_script(step('check', 'Clippy and tests'), env={'FAIL_COMMAND': 'test'}, success=False)
        self.run_script(step('msrv', 'Check all targets on minimum Rust'), env={'FAIL_COMMAND': 'check'}, success=False)

    def cargo_fixture(self, source):
        (self.root / 'Cargo.toml').write_text('[package]\nname = "ci-fixture"\nversion = "0.1.0"\nedition = "2021"\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/lib.rs').write_text(source)
        self.run_script('cargo generate-lockfile --offline')

    def test_failing_documentation_example_fails_stable_checks(self):
        self.cargo_fixture('/// ```\n/// assert!(false);\n/// ```\npub fn fixture() {}\n')
        result = self.run_script(step('check', 'Clippy and tests'), success=False)
        self.assertIn('doctest failed', result.stderr)

    def test_invalid_example_fails_msrv_all_targets_check(self):
        self.cargo_fixture('pub fn fixture() {}\n')
        (self.root / 'examples').mkdir()
        (self.root / 'examples/invalid.rs').write_text('fn main() { missing_function(); }\n')
        result = self.run_script(step('msrv', 'Check all targets on minimum Rust'), success=False)
        self.assertIn('missing_function', result.stderr)


if __name__ == '__main__':
    unittest.main()
