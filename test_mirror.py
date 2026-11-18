import hashlib
import io
import json
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import mirror


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.today = date(2026, 11, 18)
        self.clock = patch.object(mirror, "date").start()
        self.addCleanup(patch.stopall)
        self.clock.today.return_value = self.today
        self.root = Path(self.directory.name) / "mirror"
        self.package = b"firmware package"
        self.firmware = {
            "id": 42,
            "card": "img/cards/42.webp",
            "pkg": "packages/42.zip",
            "sha256": hashlib.sha256(self.package).hexdigest(),
        }
        self.requests = []
        self.assets = {
            "img/cards/42.webp": b"card",
            "img/w0/42.webp": b"fallback",
            "packages/42.zip": self.package,
        }

    def next_day(self):
        self.today += timedelta(days=1)
        self.clock.today.return_value = self.today

    def open_url(self, url, timeout):
        self.requests.append(url)
        self.assertEqual(timeout, 60)
        if url.startswith(f"{mirror.BASE_URL}/catalog.json?"):
            return io.BytesIO(json.dumps({"firmwares": [self.firmware]}).encode())
        path = url.removeprefix(f"{mirror.BASE_URL}/")
        if path not in self.assets:
            raise HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(self.assets[path])

    def test_initial_download_and_unchanged_firmware(self):
        with patch.object(mirror, "urlopen", side_effect=self.open_url):
            self.assertTrue(mirror.mirror(self.root))
            self.assertEqual((self.root / "packages/42.zip").read_bytes(), self.package)
            self.assertEqual((self.root / "img/cards/42.webp").read_bytes(), b"card")
            self.assertEqual(json.loads((self.root / "catalog.json").read_text())["firmwares"], [self.firmware])
            self.assertEqual(self.requests[0], f"{mirror.BASE_URL}/catalog.json?{self.today.isoformat()}")
            self.requests.clear()
            self.assertTrue(mirror.mirror(self.root))
            self.assertEqual(self.requests, [])
            self.next_day()
            self.assertTrue(mirror.mirror(self.root))
            self.assertEqual(self.requests, [f"{mirror.BASE_URL}/catalog.json?{self.today.isoformat()}"])

    def test_changed_firmware_and_missing_card_fallback(self):
        with patch.object(mirror, "urlopen", side_effect=self.open_url):
            self.assertTrue(mirror.mirror(self.root))
            self.package = b"updated package"
            self.assets["packages/42.zip"] = self.package
            del self.assets["img/cards/42.webp"]
            self.firmware["sha256"] = hashlib.sha256(self.package).hexdigest()
            self.next_day()
            self.assertTrue(mirror.mirror(self.root))
            self.assertEqual((self.root / "packages/42.zip").read_bytes(), self.package)
            self.assertEqual((self.root / "img/w0/42.webp").read_bytes(), b"fallback")

    def test_checksum_failure_preserves_package_and_catalog_for_retry(self):
        with patch.object(mirror, "urlopen", side_effect=self.open_url):
            self.assertTrue(mirror.mirror(self.root))
            old_catalog = (self.root / "catalog.json").read_bytes()
            self.firmware["sha256"] = hashlib.sha256(b"expected").hexdigest()
            self.assets["packages/42.zip"] = b"wrong"
            self.next_day()
            with self.assertLogs(mirror.LOG, level="ERROR") as logs:
                self.assertFalse(mirror.mirror(self.root))
            self.assertIn("SHA-256 mismatch", "\n".join(logs.output))
            self.assertEqual((self.root / "catalog.json").read_bytes(), old_catalog)
            self.assertEqual((self.root / "packages/42.zip").read_bytes(), self.package)
            self.assets["packages/42.zip"] = b"expected"
            self.assertTrue(mirror.mirror(self.root))

    def test_rejects_paths_outside_mirror(self):
        for path in ("../outside", "/%2e%2e/outside", "//other.example/file", "https://other.example/file"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                mirror.asset_location(self.root, path)

    def test_missing_card_and_nested_data(self):
        self.firmware.pop("card")
        self.firmware["id"] = "zp12"
        self.assets["img/w0/zp12.webp"] = b"fallback"
        self.assertEqual(mirror.firmware_list({"data": {"firmwares": [self.firmware]}}), [self.firmware])
        with patch.object(mirror, "urlopen", side_effect=self.open_url):
            self.assertTrue(mirror.mirror(self.root))
        self.assertEqual((self.root / "img/w0/zp12.webp").read_bytes(), b"fallback")

    def test_card_server_error_does_not_use_fallback(self):
        def open_url(url, timeout):
            if url.endswith("/img/cards/42.webp"):
                raise HTTPError(url, 500, "Server Error", {}, None)
            return self.open_url(url, timeout)

        with patch.object(mirror, "urlopen", side_effect=open_url):
            with self.assertLogs(mirror.LOG, level="ERROR"):
                self.assertFalse(mirror.mirror(self.root))
        self.assertFalse((self.root / "img/w0/42.webp").exists())
        self.assertFalse((self.root / "catalog.json").exists())

    def test_invalid_catalog_is_not_saved(self):
        for catalog in ([], {"data": []}, {"firmwares": [None]}):
            with self.subTest(catalog=catalog):
                self.next_day()
                with patch.object(mirror, "urlopen", return_value=io.BytesIO(json.dumps(catalog).encode())):
                    with self.assertRaises(ValueError):
                        mirror.mirror(self.root)
                self.assertFalse((self.root / "catalog.json").exists())

    def test_failed_catalog_request_is_not_repeated_same_day(self):
        with patch.object(mirror, "urlopen", side_effect=OSError("unavailable")) as open_url:
            with self.assertRaises(OSError):
                mirror.mirror(self.root)
            with self.assertRaises(ValueError):
                mirror.mirror(self.root)
            self.assertEqual(open_url.call_count, 1)

    def test_failed_batch_does_not_replace_previously_downloaded_assets(self):
        with patch.object(mirror, "urlopen", side_effect=self.open_url):
            self.assertTrue(mirror.mirror(self.root))
            old_catalog = (self.root / "catalog.json").read_bytes()
            self.next_day()
            self.firmware["sha256"] = hashlib.sha256(b"new package").hexdigest()
            self.assets["packages/42.zip"] = b"new package"
            original_open = self.open_url

            def open_two(url, timeout):
                if url.startswith(f"{mirror.BASE_URL}/catalog.json?"):
                    bad = dict(self.firmware, id="bad", pkg="packages/bad.zip", card=None)
                    return io.BytesIO(json.dumps({"firmwares": [self.firmware, bad]}).encode())
                return original_open(url, timeout)

            with patch.object(mirror, "urlopen", side_effect=open_two):
                with self.assertLogs(mirror.LOG, level="ERROR"):
                    self.assertFalse(mirror.mirror(self.root))
            self.assertEqual((self.root / "catalog.json").read_bytes(), old_catalog)
            self.assertEqual((self.root / "packages/42.zip").read_bytes(), self.package)


if __name__ == "__main__":
    unittest.main()
