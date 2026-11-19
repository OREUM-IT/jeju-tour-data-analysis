import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('export_catalog', Path(__file__).parents[1] / 'export-catalog.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def catalog_bytes():
    return json.dumps({
        'schemaVersion': '2.0', 'dataMode': 'app-export',
        'source': {'repository': 'OREUM-IT/jeju-tour', 'commit': exporter.APP_COMMIT},
        'places': [{'id': f'place-{i}', 'name': 'Not printed'} for i in range(981)],
    }).encode()


class CatalogExportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'deploy').mkdir()
        self.private = self.root / 'deploy/.local'
        self.target = self.private / 'catalog' / exporter.CATALOG_NAME

    def generate_in_mount(self, command, **kwargs):
        if command[:2] == ['docker', 'run']:
            mount = command[command.index('--mount') + 1]
            directory = Path(mount.split('src=', 1)[1].split(',dst=', 1)[0])
            (directory / exporter.CATALOG_NAME).write_bytes(catalog_bytes())
        return subprocess.CompletedProcess(command, 0)

    def test_export_is_readable_for_file_bind_private_on_host_and_reused(self):
        self.private.mkdir()
        secret = self.private / 'mcp.env'
        secret.write_text('MCP_SERVICE_TOKEN=do-not-touch')
        secret.chmod(0o600)
        output = io.StringIO()
        with patch.object(exporter.subprocess, 'run', side_effect=self.generate_in_mount) as run:
            with contextlib.redirect_stdout(output):
                exporter.export_catalog(self.root)
                before = (self.target.read_bytes(), self.target.stat().st_ino, self.target.stat().st_uid)
                exporter.export_catalog(self.root)
        self.assertEqual(run.call_count, 2, 'Rerun must not build or call a container.')
        self.assertEqual(before, (self.target.read_bytes(), self.target.stat().st_ino, self.target.stat().st_uid))
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.target.stat().st_nlink, 1)
        for directory in [self.private, self.target.parent]:
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(secret.read_text(), 'MCP_SERVICE_TOKEN=do-not-touch')
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('Not printed', output.getvalue())
        self.assertNotIn('do-not-touch', output.getvalue())
        self.assertIn('preserved-existing', output.getvalue())

    def test_failed_container_never_publishes_partial_catalog(self):
        def failed_run(command, **kwargs):
            if command[:2] == ['docker', 'run']:
                self.generate_in_mount(command)
                raise subprocess.CalledProcessError(1, command)
        with patch.object(exporter.subprocess, 'run', side_effect=failed_run):
            with self.assertRaises(subprocess.CalledProcessError):
                exporter.export_catalog(self.root)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_symlink_directories_and_catalogs_refused_without_following(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        self.private.symlink_to(elsewhere, target_is_directory=True)
        with patch.object(exporter.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                exporter.export_catalog(self.root)
            run.assert_not_called()
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.private.unlink()
        self.target.parent.mkdir(parents=True)
        original = elsewhere / 'original.json'
        original.write_bytes(catalog_bytes())
        original.chmod(0o600)
        self.target.symlink_to(original)
        with patch.object(exporter.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                exporter.export_catalog(self.root)
            run.assert_not_called()
        self.assertEqual(original.stat().st_mode & 0o777, 0o600)

    def test_invalid_existing_and_hardlinked_catalogs_preserved_and_rejected(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text('{"unexpected":"preserve"}')
        with patch.object(exporter.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                exporter.export_catalog(self.root)
            run.assert_not_called()
        self.assertEqual(self.target.read_text(), '{"unexpected":"preserve"}')
        self.target.write_bytes(catalog_bytes())
        os.link(self.target, self.root / 'second-name')
        with self.assertRaises(ValueError):
            exporter.read_catalog(self.target)

    def test_wrong_source_or_duplicate_place_ids_cannot_be_published(self):
        for mutation in ['source', 'ids']:
            with self.subTest(mutation=mutation):
                def invalid_run(command, **kwargs):
                    self.generate_in_mount(command)
                    if command[:2] == ['docker', 'run']:
                        mount = command[command.index('--mount') + 1]
                        directory = Path(mount.split('src=', 1)[1].split(',dst=', 1)[0])
                        value = json.loads(catalog_bytes())
                        if mutation == 'source':
                            value['source']['commit'] = '0' * 40
                        else:
                            value['places'][1]['id'] = value['places'][0]['id']
                        (directory / exporter.CATALOG_NAME).write_text(json.dumps(value))
                with patch.object(exporter.subprocess, 'run', side_effect=invalid_run):
                    with self.assertRaises(ValueError):
                        exporter.export_catalog(self.root)
                self.assertFalse(self.target.exists())

    def test_concurrent_export_cannot_overwrite_existing_target(self):
        def another_export_finished(command, **kwargs):
            self.generate_in_mount(command)
            if command[:2] == ['docker', 'run']:
                self.target.write_bytes(b'preserve concurrent result')
        with patch.object(exporter.subprocess, 'run', side_effect=another_export_finished):
            with self.assertRaises(FileExistsError):
                exporter.export_catalog(self.root)
        self.assertEqual(self.target.read_bytes(), b'preserve concurrent result')
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])


if __name__ == '__main__':
    unittest.main()
