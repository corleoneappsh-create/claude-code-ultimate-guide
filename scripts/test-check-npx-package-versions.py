#!/usr/bin/env python3
"""Offline contract tests for checking version-pinned npm install instructions."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name('check-npx-packages.py')
SPEC = importlib.util.spec_from_file_location('npm_package_checker', SOURCE)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class ExactVersionChecks(unittest.TestCase):
    def test_default_extractor_keeps_existing_name_only_behavior(self):
        found = checker.extract('npx -y claude-code-ultimate-guide-mcp@1.3.8')
        self.assertEqual(found, {('claude-code-ultimate-guide-mcp', 1)})

    def test_optional_extraction_preserves_only_exact_semver_pins(self):
        source = (
            'npx -y claude-code-ultimate-guide-mcp@1.3.8\n'
            'npm install @teamacme/tool@2.1.0-beta.3\n'
            'npm install metadata-package@1.2.3+build.7\n'
            'npx other-package@latest\n'
            'npx another-package@^2.1.0\n'
            '"command": "npx", "args": ["-y", "@teamacme/json-tool@4.5.6"]'
        )
        found = checker.extract(source, preserve_versions=True)
        self.assertIn(('claude-code-ultimate-guide-mcp@1.3.8', 1), found)
        self.assertIn(('@teamacme/tool@2.1.0-beta.3', 2), found)
        self.assertIn(('metadata-package@1.2.3+build.7', 3), found)
        self.assertIn(('other-package', 4), found)
        self.assertIn(('another-package', 5), found)
        self.assertIn(('@teamacme/json-tool@4.5.6', 6), found)

    def test_missing_exact_version_is_not_treated_as_published(self):
        seen = []
        def fake_fetch(url, read_body=False):
            seen.append(url)
            if url.endswith('/1.3.8'):
                return 404, b'Not found'
            return 200, b''
        with patch.object(checker, 'fetch', side_effect=fake_fetch):
            status = checker.registry_status('claude-code-ultimate-guide-mcp@1.3.8')
        self.assertIn('1.3.8', status)
        self.assertNotEqual(status, 'published')
        self.assertEqual(len(seen), 2)

    def test_valid_exact_version_and_scoped_npm_endpoint(self):
        seen = []
        def fake_fetch(url, read_body=False):
            seen.append(url)
            return 200, b''
        with patch.object(checker, 'fetch', side_effect=fake_fetch):
            status = checker.registry_status('@scope/tool@2.1.0')
        self.assertEqual(status, 'published')
        self.assertEqual(seen[-1], 'https://registry.npmjs.org/@scope%2Ftool/2.1.0')

    def test_default_name_check_uses_only_one_registry_request(self):
        with patch.object(checker, 'fetch', return_value=(200, b'')) as fetch:
            status = checker.registry_status('valid-package')
        self.assertEqual(status, 'published')
        fetch.assert_called_once()

    def test_build_metadata_queries_the_publishable_base_version(self):
        seen = []
        def fake_fetch(url, read_body=False):
            seen.append(url)
            if url.endswith('/1.2.3'):
                return 200, b''
            if url.endswith('/1.2.3%2Bbuild.7'):
                return 404, b''
            return 200, b''
        with patch.object(checker, 'fetch', side_effect=fake_fetch):
            status = checker.registry_status('metadata-package@1.2.3+build.7')
        self.assertEqual(status, 'published')
        self.assertEqual(seen[-1], 'https://registry.npmjs.org/metadata-package/1.2.3')

    def test_rejects_invalid_exact_semver_suffixes_without_looking_them_up(self):
        for invalid in ('1.0.0-01', '1.0.0-..', '1.0.0-a..b', '١.2.3'):
            self.assertEqual(checker.exact_version_spec('valid-package@' + invalid), 'valid-package')

    def test_version_mode_requires_explicit_files_to_avoid_illustrative_pins(self):
        import sys
        with patch.object(sys, 'argv', ['check-npx-packages.py', '--check-versions']):
            with patch.object(checker, 'scoped_files', return_value=[]):
                with self.assertRaises(SystemExit) as exc:
                    checker.main()
        self.assertEqual(exc.exception.code, 2)

    def test_version_registry_failure_is_not_reported_as_published(self):
        with patch.object(checker, 'fetch', side_effect=[
            (200, b''), (503, b'')
        ]):
            status = checker.registry_status('valid-package@1.2.3')
        self.assertIn('503', status)
        self.assertNotEqual(status, 'published')

    def test_own_mcp_readme_is_scanned(self):
        files = {str(x.relative_to(checker.ROOT)) for x in checker.scoped_files()}
        self.assertIn('mcp-server/README.md', files)


if __name__ == '__main__':
    unittest.main()
