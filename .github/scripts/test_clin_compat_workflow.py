"""Check immutable consumer selection and read-only downstream execution."""
import os
from pathlib import Path
import subprocess
import unittest

import yaml


WORKFLOW = yaml.safe_load((Path(__file__).parents[1] / 'workflows/clin-compat.yml').read_text())
STEPS = WORKFLOW['jobs']['adapter']['steps']


class ClinCompatibilityTests(unittest.TestCase):
    def test_consumer_revision_requires_full_sha_and_never_evaluates_input(self):
        script = next(step['run'] for step in STEPS if step.get('name') == 'Validate Clin revision')
        for revision, succeeds in [('a' * 40, True), ('main', False), ('abc123', False),
                                    ('a' * 40 + '\ninjected', False), ('$(exit 0)', False)]:
            with self.subTest(revision=revision):
                result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script],
                                        env={**os.environ, 'CLIN_REF': revision}, capture_output=True)
                self.assertEqual(result.returncode == 0, succeeds)

    def test_candidate_and_consumer_checkouts_do_not_retain_credentials(self):
        self.assertEqual(WORKFLOW['permissions'], {'contents': 'read'})
        checkouts = [step['with'] for step in STEPS if step.get('uses', '').startswith('actions/checkout@')]
        self.assertEqual(checkouts[0]['path'], 'library')
        self.assertNotIn('ref', checkouts[0])
        self.assertEqual(checkouts[1]['repository'], 'reekta92/clin-rs')
        self.assertEqual(checkouts[1]['ref'], '${{ inputs.clin_ref }}')
        self.assertTrue(all(checkout['persist-credentials'] is False for checkout in checkouts))


if __name__ == '__main__':
    unittest.main()
