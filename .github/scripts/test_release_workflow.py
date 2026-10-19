"""Exercise the actual inline release scripts without credentials or publishing."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / 'workflows/rust-release.yml').read_text())
# PyYAML's YAML 1.1 loader treats the workflow key "on" as True.
INPUTS = WORKFLOW.get('on', WORKFLOW.get(True))['workflow_call']['inputs']
DEFAULTS = {name: value.get('default') for name, value in INPUTS.items()}


def step(job, name):
    return next(s['run'] for s in WORKFLOW['jobs'][job]['steps'] if s.get('name') == name)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.PIPE).strip()


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        git(self.source, 'init', '--initial-branch=main')
        git(self.source, 'config', 'user.name', 'Release Test')
        git(self.source, 'config', 'user.email', 'release@example.invalid')
        self.manifest('1.2.3')
        git(self.source, 'add', 'Cargo.toml')
        git(self.source, 'commit', '-m', 'Initial source')
        self.base = git(self.source, 'rev-parse', 'HEAD')
        self.env = {**os.environ, 'GITHUB_OUTPUT': str(self.root / 'output'), 'GITHUB_STEP_SUMMARY': str(self.root / 'summary'), 'RUNNER_TEMP': str(self.root)}

    def manifest(self, version):
        (self.source / 'Cargo.toml').write_text(f'[package]\nname = "fixture"\nversion = "{version}"\n')

    def run_script(self, script, *, env=None, cwd=None, success=True):
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=cwd or self.source, env={**self.env, **(env or {})}, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calculate(self, version, **inputs):
        self.manifest(version)
        self.run_script(step('prepare', 'Validate inputs and calculate version'), env={'RELEASE_INPUTS': json.dumps({**DEFAULTS, **inputs})})
        return dict(line.split('=', 1) for line in (self.root / 'output').read_text().splitlines())

    def candidate(self):
        self.remote = self.root / 'remote.git'
        git(self.source, 'init', '--bare', str(self.remote))
        git(self.source, 'remote', 'add', 'origin', str(self.remote))
        git(self.source, 'push', 'origin', 'main')
        self.manifest('1.2.4')
        git(self.source, 'commit', '-am', 'Release candidate')
        self.commit = git(self.source, 'rev-parse', 'HEAD')
        self.env.update(BASE_COMMIT=self.base, RELEASE_COMMIT=self.commit, BRANCH='main', TAG='v1.2.4')

    def test_version_progression(self):
        cases = [
            ('1.2.3', {}, '1.2.4'),
            ('1.2.3', {'bump': 'minor'}, '1.3.0'),
            ('1.2.3', {'bump': 'major'}, '2.0.0'),
            ('1.2.3', {'prerelease': True}, '1.2.4-rc.0'),
            ('1.2.4-rc.9', {'prerelease': True}, '1.2.4-rc.10'),
            ('1.2.4-beta.2', {'prerelease': True}, '1.2.4-rc.0'),
            ('1.2.4-rc.9', {}, '1.2.4'),
            ('1.2.3+build.7', {}, '1.2.4'),
            ('1.2.3', {'override_version': '2.0.0-beta.0+build.1'}, '2.0.0-beta.0+build.1'),
        ]
        for current, inputs, expected in cases:
            with self.subTest(current=current, inputs=inputs):
                outputs = self.calculate(current, **inputs)
                self.assertEqual(outputs['new_version'], expected)
                self.assertEqual(outputs['prerelease'], str('-' in expected.split('+')[0]).lower())

    def test_invalid_or_nonadvancing_versions_fail(self):
        for inputs in [
            {'bump': 'invalid'},
            {'override_version': '01.2.4'},
            {'override_version': '1.2.3'},
            {'override_version': '1.2.3+different'},
            {'override_version': '1.2.2'},
            {'override_version': '1.2.4-rc.01'},
            {'override_version': '1.2.4\nmalicious=value'},
            {'override_version': '$(touch injected)'},
            {'prerelease': True, 'prerelease_identifier': '01'},
            {'target_branch': 'main\nmalicious'},
        ]:
            with self.subTest(inputs=inputs):
                self.run_script(step('prepare', 'Validate inputs and calculate version'), env={'RELEASE_INPUTS': json.dumps({**DEFAULTS, **inputs})}, success=False)
        self.assertFalse((self.source / 'injected').exists())

    def test_existing_tags_cannot_be_replaced(self):
        git(self.source, 'tag', 'v1.2.4')
        self.run_script(step('prepare', 'Validate inputs and calculate version'), env={'RELEASE_INPUTS': json.dumps(DEFAULTS)}, success=False)

    def test_crate_configuration_rejects_application_steps(self):
        for key in ['build_packages', 'build_appimage', 'build_dmg', 'update_docs', 'create_discussion']:
            with self.subTest(key=key):
                self.run_script(step('prepare', 'Validate inputs and calculate version'), env={'RELEASE_INPUTS': json.dumps({**DEFAULTS, key: True})}, success=False)
        self.assertEqual(WORKFLOW['jobs']['build']['if'], "${{ inputs.release_kind == 'application' }}")
        self.assertIn('!inputs.dry_run', WORKFLOW['jobs']['publish']['if'])
        self.assertNotIn('overwrite_release', INPUTS)
        self.assertNotIn('publish_aur', INPUTS)

    def test_application_matrix_and_required_assets(self):
        config = dict(release_kind='application', BIN_NAME='fixture', PKG_PREFIX='fixture', build_macos_intel=True)
        self.run_script(step('prepare', 'Validate inputs and calculate version'), env={'RELEASE_INPUTS': json.dumps({**DEFAULTS, **config})}, success=False)
        for name in ['LICENSE', 'README.md']:
            (self.source / name).write_text('Fixture')
        matrix = json.loads(self.calculate('1.2.3', **config)['matrix'])['include']
        self.assertEqual(len(matrix), 5)
        self.assertIn('x86_64-apple-darwin', [row['target'] for row in matrix])
        linux = json.loads(self.calculate('1.2.3', **config, build_targets='linux-only')['matrix'])['include']
        self.assertEqual(len(linux), 2)
        self.run_script(step('prepare', 'Validate inputs and calculate version'), env={'RELEASE_INPUTS': json.dumps({**DEFAULTS, **config, 'build_appimage': True})}, success=False)

    def test_bundle_restores_exact_candidate_for_builds_and_dry_runs(self):
        self.candidate()
        self.run_script(step('prepare', 'Bundle the validated release commit'))
        restored = self.root / 'restored'
        git(self.source, 'clone', '--depth', '1', '--branch', 'main', self.remote.as_uri(), str(restored))
        bundle_dir = self.root / 'release-source'
        bundle_dir.mkdir()
        (self.root / 'release.bundle').rename(bundle_dir / 'release.bundle')
        build_restore = step('build', 'Restore validated release commit')
        self.assertEqual(build_restore, step('publish', 'Restore validated release commit'))
        self.run_script(build_restore, cwd=restored)
        self.assertEqual(git(restored, 'rev-parse', 'HEAD'), self.commit)
        self.assertIn('1.2.4', (restored / 'Cargo.toml').read_text())
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/main'), self.base)
        self.assertEqual(git(self.remote, 'tag'), '')

    def test_atomic_push_and_retry_preserve_release_commit(self):
        self.candidate()
        publish = step('publish', 'Push release commit and immutable tag atomically')
        self.run_script(publish)
        self.run_script(publish)
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/main'), self.commit)
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/tags/v1.2.4'), self.commit)

    def test_moved_branch_aborts_without_tagging(self):
        self.candidate()
        git(self.source, 'checkout', '--detach', self.base)
        (self.source / 'concurrent.txt').write_text('Concurrent change')
        git(self.source, 'add', 'concurrent.txt')
        git(self.source, 'commit', '-m', 'Concurrent change')
        moved = git(self.source, 'rev-parse', 'HEAD')
        git(self.source, 'push', 'origin', 'HEAD:main')
        git(self.source, 'checkout', '--detach', self.commit)
        self.run_script(step('publish', 'Push release commit and immutable tag atomically'), success=False)
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/main'), moved)
        self.assertEqual(git(self.remote, 'tag'), '')

    def test_rejected_tag_leaves_branch_untouched(self):
        self.candidate()
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nwhile read -r old new ref; do\n  case "$ref" in refs/tags/*) exit 1;; esac\ndone\n')
        hook.chmod(0o755)
        self.run_script(step('publish', 'Push release commit and immutable tag atomically'), success=False)
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/main'), self.base)
        self.assertEqual(git(self.remote, 'tag'), '')

    def test_branch_reset_during_push_is_not_overwritten(self):
        ancestor = self.base
        (self.source / 'baseline.txt').write_text('New baseline')
        git(self.source, 'add', 'baseline.txt')
        git(self.source, 'commit', '-m', 'New baseline')
        self.base = git(self.source, 'rev-parse', 'HEAD')
        self.candidate()
        hook = self.source / '.git/hooks/pre-push'
        hook.write_text(f'#!/bin/sh\ngit --git-dir="{self.remote}" update-ref refs/heads/main {ancestor}\n')
        hook.chmod(0o755)
        self.run_script(step('publish', 'Push release commit and immutable tag atomically'), success=False)
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/main'), ancestor)
        self.assertEqual(git(self.remote, 'tag'), '')

    def test_dependency_lock_verification_rejects_drift(self):
        script = step('prepare', 'Set version without updating dependencies')
        verify = script[script.index("python3 - <<'PY'"):]
        original = 'version = 4\n\n[[package]]\nname = "fixture"\nversion = "1.2.3"\n\n[[package]]\nname = "dependency"\nversion = "1.0.0"\nsource = "registry+https://example.invalid"\n'
        (self.root / 'original-Cargo.lock').write_text(original)
        (self.source / 'Cargo.lock').write_text(original.replace('1.2.3', '1.2.4'))
        self.run_script(verify, env={'VERSION': '1.2.4', 'CRATE': 'fixture'})
        (self.source / 'Cargo.lock').write_text(original.replace('1.2.3', '1.2.4').replace('1.0.0', '1.0.1'))
        self.run_script(verify, env={'VERSION': '1.2.4', 'CRATE': 'fixture'}, success=False)

    def mock_command(self, name, source):
        directory = self.root / 'tools'
        directory.mkdir(exist_ok=True)
        command = directory / name
        command.write_text('#!/usr/bin/env python3\n' + source)
        command.chmod(0o755)
        self.env['PATH'] = str(directory) + os.pathsep + os.environ['PATH']

    def test_registry_retries_require_identical_package_contents(self):
        archive = self.source / 'target/package/fixture-1.2.4.crate'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'validated package')
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.mock_command('cargo', '')
        self.mock_command('curl', "import json, os, pathlib, sys\npathlib.Path(sys.argv[sys.argv.index('--output') + 1]).write_text(json.dumps({'version': {'checksum': os.environ['CHECKSUM']}}))\nprint(os.environ['HTTP_STATUS'], end='')\n")
        script = step('publish', 'Check crates.io publication state')
        for status, expected, succeeds in [('404', '', True), ('200', checksum, True), ('200', 'different', False), ('403', '', False), ('500', '', False)]:
            with self.subTest(status=status, checksum=expected):
                self.run_script(script, env={'CRATE': 'fixture', 'VERSION': '1.2.4', 'HTTP_STATUS': status, 'CHECKSUM': expected}, success=succeeds)

    def test_missing_credentials_fail_before_mutation(self):
        env = dict(PUBLISH_CRATES='true', CREATE_DISCUSSION='false', CARGO_REGISTRY_TOKEN='', BOT_TOKEN='')
        script = step('publish', 'Require publishing credentials')
        self.run_script(script, env=env, success=False)
        self.run_script(script, env={**env, 'CARGO_REGISTRY_TOKEN': 'fixture-token'})
        self.assertEqual(git(self.source, 'rev-parse', 'HEAD'), self.base)

    def test_application_pkgbuild_tracks_published_tarball(self):
        assets = ['fixture_1.2.4_amd64.deb', 'fixture-1.2.4-1.x86_64.rpm', 'fixture-x86_64-unknown-linux-gnu.tar.gz', 'fixture-x86_64-unknown-linux-gnu.tar.xz', 'fixture-1.2.4-x86_64.AppImage']
        self.mock_command('gh', "import json, pathlib, sys\nif 'download' in sys.argv:\n pathlib.Path('tarball.tar.xz').write_bytes(b'published tarball')\nelse:\n print(json.dumps({'assets': [{'name': name} for name in " + repr(assets) + "]}))\n")
        (self.source / 'README.md').write_text('fixture_1.2.3_amd64.deb fixture-1.2.3-1.x86_64.rpm fixture-1.2.3-x86_64.AppImage\n')
        pkgbuild = self.source / 'PKGBUILD'
        pkgbuild.write_text('# Maintainer: example\npkgver=1.2.3\npkgrel=2\nsource=("old")\nsha256sums=("old")\n')
        self.run_script(step('publish', 'Update artifact references'), env={'PKG_PREFIX': 'fixture', 'VERSION': '1.2.4-beta.0', 'TAG': 'v1.2.4-beta.0', 'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_REPOSITORY': 'owner/fixture'})
        actual = pkgbuild.read_text()
        self.assertIn('# Maintainer: example', actual)
        self.assertIn('pkgver=1.2.4_beta.0', actual)
        self.assertIn('pkgrel=1', actual)
        self.assertIn(hashlib.sha256(b'published tarball').hexdigest(), actual)
        self.assertIn('https://github.com/owner/fixture/releases/download/v1.2.4-beta.0/fixture-x86_64-unknown-linux-gnu.tar.xz', actual)
        self.assertIn('fixture-1.2.4-x86_64.AppImage', (self.source / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
