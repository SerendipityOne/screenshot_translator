import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from app import (
    ApiConfig,
    ConfigError,
    ConfigStore,
    TranslationError,
    check_tesseract_languages,
    image_to_png_bytes,
    parse_chat_completion,
    request_translation,
    run_ocr,
)


VALID_CONFIG = ApiConfig(
    api_url="https://example.com/v1/chat/completions",
    model="test-model",
    api_key="test-key",
)


class ApiConfigTests(unittest.TestCase):
    def test_validation_accepts_https_and_local_http(self) -> None:
        self.assertEqual(VALID_CONFIG.validated(), VALID_CONFIG)
        local = ApiConfig(
            "http://127.0.0.1:8080/v1/chat/completions", "model", "key"
        )
        self.assertEqual(local.validated(), local)

    def test_validation_rejects_remote_http_and_missing_secret(self) -> None:
        with self.assertRaises(ConfigError):
            ApiConfig("http://example.com/v1/chat/completions", "model", "key").validated()
        with self.assertRaises(ConfigError):
            ApiConfig("https://example.com/v1/chat/completions", "model", "").validated()

    def test_config_store_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "nested" / "config.json"
            store = ConfigStore(path)
            store.save(VALID_CONFIG)
            self.assertEqual(store.load(), VALID_CONFIG)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class TranslationContractTests(unittest.TestCase):
    def test_parse_rejects_invalid_contract(self) -> None:
        with self.assertRaises(TranslationError):
            parse_chat_completion({"choices": []})

    def test_request_uses_chat_completions_contract(self) -> None:
        received: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                received["authorization"] = self.headers["Authorization"]
                received["body"] = json.loads(self.rfile.read(length))
                response = json.dumps(
                    {"choices": [{"message": {"content": "你好，世界"}}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = ApiConfig(
                api_url=f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                model="mock-model",
                api_key="mock-key",
            )
            self.assertEqual(request_translation(config, "Hello, world"), "你好，世界")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(received["authorization"], "Bearer mock-key")
        body = received["body"]
        self.assertEqual(body["model"], "mock-model")
        self.assertIn("Hello, world", body["messages"][1]["content"])


class TesseractSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_required_languages_and_simple_ocr(self) -> None:
        check_tesseract_languages()
        image = QImage(1000, 260, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setPen(QColor("black"))
        painter.setFont(QFont("DejaVu Sans", 72, QFont.Weight.Bold))
        painter.drawText(QRect(30, 30, 940, 190), 0, "TEST 123")
        painter.end()

        text = run_ocr(image_to_png_bytes(image))
        self.assertIn("TEST", text.upper())


if __name__ == "__main__":
    unittest.main()
