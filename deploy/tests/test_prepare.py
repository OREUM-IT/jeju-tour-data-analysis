import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare', Path(__file__).parents[1] / 'prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrivateSettingsTest(unittest.TestCase):
    def test_credentials_private_distinct_and_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '.local'
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                prepare.prepare(root)
                first = {p.name: p.read_text() for p in root.iterdir()}
                prepare.prepare(root)
            self.assertEqual(first, {p.name: p.read_text() for p in root.iterdir()})
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            tokens = []
            for p in root.iterdir():
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)
                token = p.read_text().strip().split('=', 1)[1]
                self.assertRegex(token, r'^[a-f0-9]{64}$')
                self.assertNotIn(token, output.getvalue())
                tokens.append(token)
            self.assertEqual(len(set(tokens)), 2)

    def test_symlink_directory_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'target').mkdir()
            (root / '.local').symlink_to(root / 'target', target_is_directory=True)
            with self.assertRaises(ValueError):
                prepare.prepare(root / '.local')
            self.assertEqual(list((root / 'target').iterdir()), [])

    def test_symlink_file_refused_without_modifying_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'original'
            target.write_text('keep')
            target.chmod(0o644)
            private = root / '.local'
            private.mkdir()
            (private / 'mcp.env').symlink_to(target)
            with self.assertRaises(ValueError):
                prepare.prepare(private)
            self.assertEqual(target.read_text(), 'keep')
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
