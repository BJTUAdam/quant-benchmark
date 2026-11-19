from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from urllib.parse import urlsplit

from deployment.build_engine_reference import (
    BLOB_BASE, COMMIT, DEST, SOURCES, resolve_link, rewrite_links, validate,
)


class EngineReferenceTests(unittest.TestCase):
    def test_saved_and_unsaved_relative_links_have_distinct_destinations(self):
        source = "docs/zh/start/quickstart.md"
        self.assertEqual(resolve_link(source, "../guide/strategy.md#signals"), "../guide/strategy.md#signals")
        self.assertEqual(resolve_link(source, "../guide/quant_basics.md"), BLOB_BASE + "docs/zh/guide/quant_basics.md")

    def test_images_become_passive_fixed_version_source_links(self):
        text = "![example](../../images/example.png)\n<img src=\"../../images/other.png\" alt=\"other\">\n"
        out = rewrite_links("docs/zh/start/quickstart.md", text)
        self.assertNotIn("![", out)
        self.assertNotIn("<img", out)
        self.assertIn(COMMIT + "/docs/images/example.png", out)
        self.assertIn(COMMIT + "/docs/images/other.png", out)

    def test_code_fences_and_external_links_are_preserved(self):
        text = "```python\ns = '[label](../x.md)'\n```\n[Docs](https://example.org/reference)\n"
        self.assertEqual(rewrite_links(SOURCES[0], text), text)

    def test_snapshot_is_complete_and_verifies_fully_offline(self):
        result = validate()
        self.assertEqual(result["official_documents"], 13)
        self.assertEqual(result["mode"], "offline_hash_validation")
        manifest = json.loads((DEST / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_commit"], COMMIT)
        self.assertEqual({x["source_path"] for x in manifest["documents"]}, set(SOURCES))

    def test_snapshot_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "reference"
            shutil.copytree(DEST, target)
            file = target / "official/start/quickstart.md"
            file.write_bytes(file.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate(target)

    def test_downloaded_relative_document_links_resolve_locally(self):
        for file in (DEST / "official").rglob("*.md"):
            text = file.read_text(encoding="utf-8")
            for match in re.finditer(r"\[[^\]\n]+\]\(([^\s)]+)\)", text):
                link = urlsplit(match[1])
                if not link.scheme and not link.netloc and link.path:
                    self.assertTrue((file.parent / link.path).is_file(), f"{file.name}: {link.path}")


if __name__ == "__main__":
    unittest.main()
