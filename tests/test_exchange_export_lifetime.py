"""An abandoned HTTP request must not orphan its private export archive."""

import asyncio
import gc
import threading

import pytest

from rove.api.trials import create_trial_router
from rove.trials.store import TrialStore


@pytest.mark.parametrize("fail_after_export", [False, True])
async def test_cancelled_export_cleans_only_after_real_worker_finishes(
    tmp_path, monkeypatch, fail_after_export
):
    import rove.trials.exchange as exchange

    store = TrialStore(tmp_path / "source")
    trial = store.begin(source="quick", task={"task": "Synthetic fixture"}, strategy={}, config={})
    store.finish(trial, status="completed", result={"outcome": "unknown"})
    real_export = exchange.export_bundle
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    observed = {}

    def controlled_export(root, output):
        observed["path"] = output
        entered.set()
        try:
            if not release.wait(5):
                raise TimeoutError("Test did not release the export worker")
            real_export(root, output)
            observed["archive_written"] = output.is_file()
            if fail_after_export:
                raise OSError("Controlled failure after archive publication")
        finally:
            finished.set()

    monkeypatch.setattr(exchange, "export_bundle", controlled_export)
    router = create_trial_router(lambda: store)
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/exchange")
    request = asyncio.create_task(endpoint())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        directory = observed["path"].parent
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        del request
        await asyncio.sleep(0)
        gc.collect()
        # The old implementation removed this directory during cancellation,
        # allowing the live exporter to recreate an unowned directory later.
        assert directory.is_dir()
        assert not finished.is_set()
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        for _ in range(100):
            if not directory.exists():
                break
            await asyncio.sleep(0.01)
        assert observed["archive_written"]
        assert not directory.exists()
        assert store.get(trial)["result"] == {"outcome": "unknown"}
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 3)
