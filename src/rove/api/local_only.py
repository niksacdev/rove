"""Local dashboard boundary: reject DNS rebinding and cross-origin browser calls."""
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse


class LocalOnlyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin")
        try:
            target = urlsplit(f"{scope.get('scheme', 'http')}://{host}")
            valid_host = target.hostname in {"localhost", "127.0.0.1", "::1"}
            # Reject malformed ports, userinfo and paths too.
            _port = target.port
            valid_host = valid_host and not target.username and not target.path
            valid_origin = not origin or origin.decode("latin-1") == target.geturl()
        except ValueError:
            valid_host = valid_origin = False
        if not valid_host or not valid_origin:
            response = PlainTextResponse("ROVE accepts local, same-origin requests only", status_code=403)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)
