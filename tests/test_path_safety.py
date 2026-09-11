import tempfile
import unittest
from pathlib import Path

from tools.path_safety import external_path


class ExternalPathTests(unittest.TestCase):
    def test_existing_temporary_path_is_canonicalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = external_path(temporary, require_exists=True)
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_dir())

    def test_caller_created_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                external_path(link, require_exists=True)


if __name__ == "__main__":
    unittest.main()
