"""A disposable local HTTP server used only by browser tests."""

from contextlib import contextmanager
from pathlib import Path
import importlib.util
import os
import secrets
import tempfile
import threading
from werkzeug.serving import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@contextmanager
def live_server():
    with tempfile.TemporaryDirectory(prefix="tourism-browser-") as directory:
        overrides = {
            "INSTANCE_DIR": directory,
            "APP_ENV": "development",
            "SECRET_KEY": secrets.token_hex(32),
            "COOKIE_SECURE": "0",
        }
        previous = {key: os.environ.get(key) for key in overrides}
        os.environ.update(overrides)
        try:
            spec = importlib.util.spec_from_file_location(
                "browser_app", ROOT / "app.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        server = make_server("127.0.0.1", 0, module.app, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        password = (
            (Path(directory) / "admin-access.txt")
            .read_text()
            .split("Пароль: ")[1]
            .splitlines()[0]
        )
        try:
            yield f"http://{server.server_address[0]}:{server.server_port}", password
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
