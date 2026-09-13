"""Keep the local workspace's HTML, scripts and styles on the same revision."""

from fastapi.staticfiles import StaticFiles


class FrontendStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Retain ETag/Last-Modified validation, but never reuse an unchecked old
        # script alongside newer HTML/CSS after a local checkout changes.
        response.headers["Cache-Control"] = "no-cache"
        return response
