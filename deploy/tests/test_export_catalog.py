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

    def test_private_checkout_exports_committed_files_without_credentials_or_hooks(self):
        original = self.root / 'private-source'
        original.mkdir()
        real_run = subprocess.run

        def git(*args):
            return real_run(['git', '-C', str(original), *args], check=True, capture_output=True, text=True).stdout

        git('init', '--quiet', '--template=')
        git('config', 'user.name', 'Catalog test')
        git('config', 'user.email', 'test@example.invalid')
        git('remote', 'add', 'origin', 'https://operator:private-token@github.com/OREUM-IT/jeju-tour.git')
        tracked = original / 'tracked.txt'
        tracked.write_text('pinned content')
        git('add', 'tracked.txt')
        git('commit', '--quiet', '-m', 'Pinned source')
        commit = git('rev-parse', 'HEAD').strip()
        tracked.write_text('local edits must remain')
        (original / '.env').write_text('PRIVATE=never-mount-this')
        (original / '.git/hooks').mkdir()
        hook = original / '.git/hooks/post-checkout'
        hook.write_text('#!/bin/sh\nexit 99\n')
        hook.chmod(0o755)
        before_config = (original / '.git/config').read_bytes()
        # Exercise the sudo case without mutating global git trust configuration.
        if os.getuid() == 0:
            os.chown(original, 65534, 65534)
            os.chown(original / '.git', 65534, 65534)
        observed = []

        def run(command, **kwargs):
            if command[:2] == ['docker', 'run']:
                mounts = [command[i + 1] for i, part in enumerate(command) if part == '--mount']
                self.assertEqual(len(mounts), 2)
                source_mount = mounts[1]
                self.assertTrue(source_mount.endswith(',dst=/source,readonly'))
                source = Path(source_mount.split('src=', 1)[1].split(',dst=', 1)[0])
                output = Path(mounts[0].split('src=', 1)[1].split(',dst=', 1)[0])
                self.assertEqual(source.parent, output.parent)
                self.assertNotEqual(source, output)
                self.assertNotIn(str(original), '\n'.join(mounts))
                self.assertEqual((source / 'tracked.txt').read_text(), 'pinned content')
                self.assertFalse((source / '.env').exists())
                self.assertFalse((source / '.git/hooks').exists())
                self.assertNotIn('private-token', (source / '.git/config').read_text())
                self.assertIn(exporter.SOURCE_REMOTE, (source / '.git/config').read_text())
                observed.append(True)
                return self.generate_in_mount(command)
            if command[:2] == ['docker', 'build']:
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, **kwargs)

        output = io.StringIO()
        with patch.object(exporter, 'APP_COMMIT', commit), patch.object(exporter.subprocess, 'run', side_effect=run):
            with contextlib.redirect_stdout(output):
                exporter.export_catalog(self.root, original)
                exporter.export_catalog(self.root, self.root / 'missing-checkout')
        self.assertEqual(observed, [True], 'Existing catalog reuse needs neither source checkout nor Docker.')
        self.assertEqual(tracked.read_text(), 'local edits must remain')
        self.assertEqual((original / '.git/config').read_bytes(), before_config)
        self.assertEqual(hook.read_text(), '#!/bin/sh\nexit 99\n')
        self.assertNotIn('private-token', output.getvalue())
        self.assertNotIn('never-mount-this', output.getvalue())

    def test_source_origin_identity_and_errors_do_not_expose_credentials(self):
        for remote in ['git@github.com:OREUM-IT/jeju-tour.git',
                       'ssh://git@github.com/OREUM-IT/jeju-tour.git',
                       'https://user:private-token@github.com/OREUM-IT/jeju-tour.git']:
            self.assertEqual(exporter.repository_identity(remote), 'oreum-it/jeju-tour')
        self.assertIsNone(exporter.repository_identity('https://github.com.evil.invalid/OREUM-IT/jeju-tour.git'))
        secret_error = subprocess.CalledProcessError(128, ['git'], stderr=b'https://private-token@github.com')
        with patch.object(exporter.subprocess, 'run', side_effect=secret_error):
            with self.assertRaises(ValueError) as failure:
                exporter.export_catalog(self.root, self.root / 'absent')
        self.assertNotIn('private-token', str(failure.exception))
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
