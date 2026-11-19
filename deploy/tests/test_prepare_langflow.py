import base64
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('prepare_langflow', Path(__file__).parents[1] / 'prepare-langflow.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class LangflowSettingsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / '.local'
        self.root.mkdir(mode=0o700)
        self.mcp = self.root / 'mcp.env'
        self.mcp.write_text('MCP_SERVICE_TOKEN=' + 'a' * 64 + '\n')
        self.mcp.chmod(0o600)

    def prepare_quietly(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            setup.prepare(self.root)
        return output.getvalue()

    def test_distinct_roles_fernet_key_privacy_and_byte_exact_reuse(self):
        bootstrap = self.root / 'db.env'
        bootstrap.write_text('POSTGRES_PASSWORD=preserve-existing-bootstrap\n')
        bootstrap.chmod(0o600)
        catalog = self.root / 'catalog.json'
        catalog.write_text('{"preserve":true}\n')
        original = {path: (path.read_bytes(), path.stat().st_mode) for path in [self.mcp, bootstrap, catalog]}
        output = self.prepare_quietly()
        app = setup.read_env(self.root / setup.NAMES[0])
        database = setup.read_env(self.root / setup.NAMES[1])
        key = base64.urlsafe_b64decode(app['LANGFLOW_SECRET_KEY'])
        self.assertEqual(len(key), 32)
        passwords = [app['LANGFLOW_SUPERUSER_PASSWORD'], database['POSTGRES_PASSWORD'], database['LANGFLOW_DB_PASSWORD']]
        self.assertEqual(len(set(passwords + ['a' * 64])), 4)
        self.assertIn('langflow_app:', app['LANGFLOW_DATABASE_URL'])
        self.assertEqual(app['LANGFLOW_DATABASE_URL'], setup.database_url(database['LANGFLOW_DB_PASSWORD']))
        before = {name: ((self.root / name).read_bytes(), (self.root / name).stat().st_ino) for name in setup.NAMES}
        with patch.object(setup, 'new_pair', side_effect=AssertionError('must not rotate')):
            output += self.prepare_quietly()
        for name in setup.NAMES:
            path = self.root / name
            self.assertEqual(before[name], (path.read_bytes(), path.stat().st_ino))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        for path, expected in original.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mode), expected)
        for value in passwords + [app['LANGFLOW_SECRET_KEY'], 'a' * 64]:
            self.assertNotIn(value, output)

    def test_partial_pair_requires_recovery_without_changes(self):
        target = self.root / 'langflow.env'
        target.write_text('preserve incomplete state')
        with self.assertRaisesRegex(ValueError, 'Partial Langflow settings pair'):
            setup.prepare(self.root)
        self.assertEqual(target.read_text(), 'preserve incomplete state')
        self.assertFalse((self.root / 'langflow-db.env').exists())

    def test_symlink_root_or_existing_credentials_never_followed(self):
        link = self.directory / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            setup.prepare(link)
        self.prepare_quietly()
        target = self.root / 'langflow.env'
        original = self.directory / 'original'
        target.rename(original)
        original.chmod(0o644)
        target.symlink_to(original)
        before = original.read_bytes()
        with self.assertRaises(ValueError):
            setup.prepare(self.root)
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(original.stat().st_mode & 0o777, 0o644)

    def test_mcp_file_missing_linked_or_malformed_stops_before_creation(self):
        self.mcp.unlink()
        for kind in ['missing', 'symlink', 'hardlink', 'malformed']:
            with self.subTest(kind=kind):
                source = self.directory / 'source'
                source.write_text('MCP_SERVICE_TOKEN=' + 'a' * 64 + '\n')
                if kind == 'symlink':
                    self.mcp.symlink_to(source)
                elif kind == 'hardlink':
                    os.link(source, self.mcp)
                elif kind == 'malformed':
                    self.mcp.write_text('MCP_SERVICE_TOKEN=short\n')
                with self.assertRaises(ValueError):
                    setup.prepare(self.root)
                self.assertFalse((self.root / 'langflow.env').exists())
                self.mcp.unlink(missing_ok=True)
                source.unlink()

    def test_existing_mismatched_pair_is_preserved(self):
        self.prepare_quietly()
        path = self.root / 'langflow-db.env'
        path.write_text(path.read_text().replace('LANGFLOW_DB_PASSWORD=', 'LANGFLOW_DB_PASSWORD=0'))
        before = {name: (self.root / name).read_bytes() for name in setup.NAMES}
        with self.assertRaises(ValueError):
            setup.prepare(self.root)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in setup.NAMES})

    def test_failed_publication_rolls_back_only_this_calls_file(self):
        original_link = os.link
        def fail_second_link(source, destination, **kwargs):
            if Path(destination).name == 'langflow-db.env':
                raise PermissionError('simulated publication failure')
            return original_link(source, destination, **kwargs)
        with patch.object(setup.os, 'link', side_effect=fail_second_link):
            with self.assertRaises(OSError):
                setup.prepare(self.root)
        self.assertFalse(any((self.root / name).exists() for name in setup.NAMES))
        self.assertEqual(list(self.root.glob('.langflow-prepare-*')), [])
        self.prepare_quietly()


if __name__ == '__main__':
    unittest.main()
