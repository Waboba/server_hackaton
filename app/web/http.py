"""
http.py — Micro-framework HTTP sobre la librería estándar.

El proyecto no tiene dependencias externas a propósito: en el evento puede no
haber internet ni pip disponible, y basta con Python 3.11+. Para una LAN con
unas decenas de dispositivos, ThreadingHTTPServer sobra.

Incluye lo poco que hace falta: enrutado con parámetros, parseo de formularios
(incluido multipart para subir archivos), cookies y streaming SSE.
"""

import logging
import re
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

log = logging.getLogger(__name__)


# ── Petición ──────────────────────────────────────────────────────────────────


class Request:
    def __init__(self, method: str, path: str, query: dict, headers, body: bytes,
                 client_ip: str):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.client_ip = client_ip
        self.params: dict[str, str] = {}
        self.client = None  # se rellena con el auth.Client
        self._form: dict[str, str] | None = None
        self._files: dict[str, tuple[str, bytes]] | None = None

    def get(self, key: str, default: str = "") -> str:
        """Valor de formulario o, si no está, de la query string."""
        if key in self.form:
            return self.form[key]
        values = self.query.get(key)
        return values[0] if values else default

    @property
    def cookies(self) -> dict[str, str]:
        raw = self.headers.get("Cookie", "")
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return {}
        return {k: v.value for k, v in jar.items()}

    @property
    def form(self) -> dict[str, str]:
        if self._form is None:
            self._parse_body()
        return self._form

    @property
    def files(self) -> dict[str, tuple[str, bytes]]:
        """{campo: (nombre_archivo, contenido)}"""
        if self._files is None:
            self._parse_body()
        return self._files

    def _parse_body(self):
        self._form, self._files = {}, {}
        content_type = self.headers.get("Content-Type", "")
        if not self.body:
            return
        if content_type.startswith("application/x-www-form-urlencoded"):
            parsed = parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
            self._form = {k: v[0] for k, v in parsed.items()}
        elif content_type.startswith("multipart/form-data"):
            match = re.search(r'boundary="?([^";]+)"?', content_type)
            if match:
                self._form, self._files = parse_multipart(
                    self.body, match.group(1).encode()
                )


def parse_multipart(body: bytes, boundary: bytes) -> tuple[dict, dict]:
    """Parseo mínimo de multipart/form-data. Devuelve (campos, archivos)."""
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}

    delimiter = b"--" + boundary
    for chunk in body.split(delimiter)[1:]:
        if chunk[:2] == b"--":  # epílogo: fin del cuerpo
            break
        chunk = chunk[2:] if chunk[:2] == b"\r\n" else chunk
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]

        disposition = ""
        for line in head.decode("utf-8", "replace").splitlines():
            if line.lower().startswith("content-disposition:"):
                disposition = line
                break
        if not disposition:
            continue

        name_match = re.search(r'name="([^"]*)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        file_match = re.search(r'filename="([^"]*)"', disposition)

        if file_match is not None:
            filename = file_match.group(1)
            if filename:
                files[name] = (filename, data)
        else:
            fields[name] = data.decode("utf-8", "replace")

    return fields, files


# ── Respuesta ─────────────────────────────────────────────────────────────────


class Response:
    def __init__(self, body: bytes | str = b"", status: int = 200,
                 content_type: str = "text/html; charset=utf-8",
                 headers: list[tuple[str, str]] | None = None):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.content_type = content_type
        self.headers = headers or []

    def set_cookie(self, name: str, value: str, max_age: int | None = None,
                   path: str = "/"):
        cookie = f"{name}={value}; Path={path}; HttpOnly; SameSite=Lax"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        self.headers.append(("Set-Cookie", cookie))
        return self


class StreamResponse(Response):
    """Respuesta en streaming (SSE): itera bytes hasta que el cliente cierra."""

    def __init__(self, generator, content_type: str = "text/event-stream"):
        super().__init__(b"", 200, content_type)
        self.generator = generator
        self.headers += [("Cache-Control", "no-cache"), ("X-Accel-Buffering", "no")]


def html(body: str, status: int = 200) -> Response:
    return Response(body, status)


def text(body: str, status: int = 200) -> Response:
    return Response(body, status, "text/plain; charset=utf-8")


def redirect(location: str, flash: str | None = None) -> Response:
    if flash:
        from urllib.parse import quote
        joiner = "&" if "?" in location else "?"
        location = f"{location}{joiner}msg={quote(flash)}"
    return Response(b"", 303, "text/plain", [("Location", location)])


def attachment(data: bytes, filename: str) -> Response:
    return Response(data, 200, "application/octet-stream",
                    [("Content-Disposition", f'attachment; filename="{filename}"')])


# ── Enrutado ──────────────────────────────────────────────────────────────────


class Router:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, callable]] = []

    def add(self, method: str, pattern: str, handler):
        regex = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern)
        self.routes.append((method, re.compile(f"^{regex}$"), handler))

    def get(self, pattern):
        def decorator(fn):
            self.add("GET", pattern, fn)
            return fn
        return decorator

    def post(self, pattern):
        def decorator(fn):
            self.add("POST", pattern, fn)
            return fn
        return decorator

    def match(self, method: str, path: str):
        allowed = False
        for route_method, regex, handler in self.routes:
            match = regex.match(path)
            if not match:
                continue
            if route_method != method:
                allowed = True
                continue
            return handler, {k: unquote(v) for k, v in match.groupdict().items()}
        return (None, {"__405__": "1"}) if allowed else (None, {})


# ── Servidor ──────────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HackServer"
    sys_version = ""

    router: Router = None
    before_request = None       # callable(request) -> Response | None
    max_body: int = 8 * 1024 * 1024

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return b""
        if length > self.max_body:
            return None
        remaining, chunks = length, []
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _handle(self, method: str):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        body = self._read_body() if method == "POST" else b""
        if body is None:
            # No se ha leído el cuerpo: la conexión ya no es reutilizable.
            self.close_connection = True
            self._send(Response("Archivo demasiado grande.", 413,
                                "text/plain; charset=utf-8"))
            return

        request = Request(
            method=method,
            path=path,
            query=parse_qs(parsed.query, keep_blank_values=True),
            headers=self.headers,
            body=body,
            client_ip=self.client_address[0],
        )

        try:
            handler, params = self.router.match(method, path)
            if handler is None:
                status, message = (405, "Método no permitido") if params.get("__405__") \
                    else (404, "Página no encontrada")
                response = self._error_page(request, status, message)
            else:
                request.params = params
                response = None
                if self.before_request:
                    response = self.before_request(request)
                if response is None:
                    response = handler(request)
        except Exception:
            log.error("Error atendiendo %s %s:\n%s", method, path, traceback.format_exc())
            response = self._error_page(request, 500, "Error interno del servidor")

        self._send(response)

    def _error_page(self, request, status: int, message: str) -> Response:
        from app.web import html as H
        return Response(H.simple_page(str(status), message, getattr(request, "client", None)),
                        status)

    def _send(self, response: Response):
        if isinstance(response, StreamResponse):
            self._send_stream(response)
            return
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for key, value in response.headers:
                self.send_header(key, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_stream(self, response: StreamResponse):
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Connection", "close")
            for key, value in response.headers:
                self.send_header(key, value)
            self.end_headers()
            for chunk in response.generator:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            pass
        except Exception:
            log.debug("Stream cerrado: %s", traceback.format_exc(limit=1))


def serve(router: Router, host: str, port: int, before_request=None,
          max_body: int = 8 * 1024 * 1024) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {
        "router": router,
        "before_request": staticmethod(before_request) if before_request else None,
        "max_body": max_body,
    })
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
