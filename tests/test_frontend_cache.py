"""A local update must not combine new markup with cached old row renderers."""

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from rove.api.frontend import FrontendStaticFiles


def test_frontend_revalidates_assets_and_keeps_conditional_requests(tmp_path):
    script = tmp_path / "history.js"
    script.write_text("old renderer")
    client = TestClient(
        Starlette(routes=[Mount("/static", FrontendStaticFiles(directory=tmp_path))])
    )
    first = client.get("/static/history.js?v=same-version")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"
    unchanged = client.get(
        "/static/history.js?v=same-version", headers={"If-None-Match": first.headers["etag"]}
    )
    assert unchanged.status_code == 304
    assert unchanged.headers["cache-control"] == "no-cache"
    script.write_text("new full width trial renderer")
    changed = client.get(
        "/static/history.js?v=same-version", headers={"If-None-Match": first.headers["etag"]}
    )
    assert changed.status_code == 200
    assert changed.text == "new full width trial renderer"
    assert changed.headers["cache-control"] == "no-cache"


def test_frontend_html_also_revalidates(tmp_path):
    (tmp_path / "history.html").write_text("<main>Trials</main>")
    client = TestClient(
        Starlette(routes=[Mount("/static", FrontendStaticFiles(directory=tmp_path))])
    )
    assert client.get("/static/history.html").headers["cache-control"] == "no-cache"
