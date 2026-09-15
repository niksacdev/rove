"""The shared worker deadline owns nested simulator/model processes too."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

import pytest

from rove.benchmarks.runner import run_attempt
from rove.evaluation.registry import resolve
from rove.trials.store import TrialStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Worker groups require macOS/Linux")


def process_is_running(pid):
    # Orphans can briefly remain as zombies until the host's reaper collects
    # them. A zombie has exited and cannot keep a model or GPU context alive.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if sys.platform == "linux":
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
            return state != "Z"
        except FileNotFoundError:
            return False
    return True


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancel"])
async def test_shared_worker_terminates_runtime_and_model_grandchild(tmp_path, monkeypatch, cancel):
    plugin = tmp_path / "cleanup_plugin.py"
    plugin.write_text("""import json, os, subprocess, sys, time
from pathlib import Path
class Adapter:
    revision = "cleanup-test-v1"
    async def predict(self, case, strategy, seed):
        root = Path(case.inputs["root"])
        (root / "worker.json").write_text(json.dumps({"pid": os.getpid(), "group": os.getpgrp()}))
        subprocess.Popen([sys.executable, str(root / "runtime.py"), str(root)])
        time.sleep(2)
        return {}
    async def grade(self, case, output, reference, grading):
        raise AssertionError("Timed-out execution must never be graded")
""")
    (tmp_path / "runtime.py").write_text("""import json, os, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
(root / "runtime.json").write_text(json.dumps({"pid": os.getpid(), "group": os.getpgrp()}))
subprocess.Popen([sys.executable, str(root / "model.py"), str(root)])
time.sleep(2)
""")
    (tmp_path / "model.py").write_text("""import json, os, sys, time
from pathlib import Path
root = Path(sys.argv[1])
(root / "model.tmp").write_text(json.dumps({"pid": os.getpid(), "group": os.getpgrp()}))
(root / "model.tmp").replace(root / "model.json")
time.sleep(2)
""")
    metadata = tmp_path / "rove_cleanup_test-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: rove-cleanup-test\nVersion: 1.0\n")
    (metadata / "entry_points.txt").write_text(
        "[rove.evaluations]\ncleanup-test = cleanup_plugin:Adapter\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", f"{tmp_path}{os.pathsep}{Path('src').resolve()}")
    monkeypatch.delitem(sys.modules, "cleanup_plugin", raising=False)
    _, identity = resolve("cleanup-test")
    root = tmp_path / "store"
    trial_id = TrialStore(root).begin(source="quick", task={}, strategy={}, config={})
    request = {
        "execution": "cleanup-test",
        "executor_identity": identity,
        "trial_root": str(root),
        "trial_id": trial_id,
        "task": {"id": "case", "task": "Test lifecycle", "inputs": {"root": str(tmp_path)}},
        "strategy_id": "policy",
        "seed": 0,
        "config": {"strategies": {"policy": {}}, "grading": {}, "environment": {}},
    }
    attempt = asyncio.create_task(run_attempt(request, timeout_s=1))
    processes = []
    try:
        # Establish that both descendants really started before testing cleanup.
        while not (tmp_path / "model.json").exists() and not attempt.done():
            await asyncio.sleep(0.01)
        assert (tmp_path / "model.json").is_file(), "Worker did not reach nested model startup"
        processes = [
            json.loads((tmp_path / f"{name}.json").read_text())
            for name in ("worker", "runtime", "model")
        ]
        group = processes[0]["pid"]
        assert group != os.getpgrp()
        assert all(process["group"] == group for process in processes)
        assert all(process_is_running(process["pid"]) for process in processes)
        if cancel:
            attempt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await attempt
        else:
            assert await attempt == {"outcome": "unknown", "execution": "timeout"}
        for _ in range(25):
            if not any(process_is_running(process["pid"]) for process in processes):
                break
            await asyncio.sleep(0.01)
        assert not any(process_is_running(process["pid"]) for process in processes)
    finally:
        if not attempt.done():
            attempt.cancel()
            with suppress(asyncio.CancelledError):
                await attempt
        # A regression must not leave the test's child processes behind.
        for process in processes:
            with suppress(ProcessLookupError):
                if os.getpgid(process["pid"]) == processes[0]["pid"]:
                    os.kill(process["pid"], signal.SIGKILL)
