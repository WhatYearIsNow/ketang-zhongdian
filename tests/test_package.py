"""Check that the distributed skill remains navigable without private files."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "package_link_checker", ROOT / "scripts" / "check_links.py"
)
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


class PackageTests(unittest.TestCase):
    def test_distributed_document_links_are_self_contained(self):
        documents = [path for path in ROOT.rglob("*.md") if ".test-tmp" not in path.parts]
        for document in documents:
            with self.subTest(document=document.relative_to(ROOT)):
                report = links.check_note(document, ROOT)
                self.assertTrue(report["ok"], report["issues"])


if __name__ == "__main__":
    unittest.main()
