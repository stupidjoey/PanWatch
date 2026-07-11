import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.spa import (
    UnsafeStaticPath,
    register_spa_routes,
    resolve_static_candidate,
)


class SpaStaticSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.static_root = self.base / "static"
        self.static_root.mkdir()
        (self.static_root / "index.html").write_text("SPA INDEX", encoding="utf-8")
        (self.static_root / "assets").mkdir()
        (self.static_root / "assets" / "app.js").write_text(
            "console.log('ok')", encoding="utf-8"
        )
        (self.base / "secret.txt").write_text("DO NOT SERVE", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _client(self) -> TestClient:
        app = FastAPI()
        register_spa_routes(app, self.static_root)
        return TestClient(app)

    def test_serves_static_asset(self) -> None:
        response = self._client().get("/assets/app.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "console.log('ok')")

    def test_unknown_frontend_route_falls_back_to_index(self) -> None:
        response = self._client().get("/portfolio")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "SPA INDEX")

    def test_rejects_encoded_parent_directory_traversal(self) -> None:
        client = self._client()
        variants = (
            "/%2e%2e/secret.txt",
            "/..%2fsecret.txt",
            "/%2e%2e%2fsecret.txt",
        )

        for path in variants:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("DO NOT SERVE", response.text)

    def test_rejects_decoded_and_backslash_traversal(self) -> None:
        variants = (
            "../secret.txt",
            "nested/../../secret.txt",
            "..\\secret.txt",
            unquote("%2e%2e%2fsecret.txt"),
        )

        for path in variants:
            with self.subTest(path=path):
                with self.assertRaises(UnsafeStaticPath):
                    resolve_static_candidate(self.static_root, path)

    def test_rejects_absolute_path(self) -> None:
        with self.assertRaises(UnsafeStaticPath):
            resolve_static_candidate(self.static_root, str(self.base / "secret.txt"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_rejects_symlink_that_points_outside_static_root(self) -> None:
        link = self.static_root / "leak.txt"
        link.symlink_to(self.base / "secret.txt")

        response = self._client().get("/leak.txt")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("DO NOT SERVE", response.text)


if __name__ == "__main__":
    unittest.main()
