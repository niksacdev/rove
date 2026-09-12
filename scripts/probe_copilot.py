"""Exercise a real Copilot CLI against controlled synthetic model responses.

No provider credential is required or discovered. This tests transport, image
delivery, tools, usage and trace correlation; it does not test model quality or
Azure access. Run with the optional copilot dependency and a pinned CLI installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from rove.models.core import TaskPlan
from rove.runtime.copilot import (
    CLI_VERSION,
    SDK_VERSION,
    CopilotRuntime,
    CopilotRuntimeConfig,
    RuntimeTool,
)

# One synthetic 1x1 PNG, never customer data.
IMAGE = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


async def probe(cli_path: str) -> dict:
    import httpx
    from copilot import CopilotClient, CopilotRequestHandler, RuntimeConnection, Tool
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    process = await asyncio.create_subprocess_exec(
        cli_path,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    cli_version = stdout.decode().splitlines()[0]
    if process.returncode or f" {CLI_VERSION}." not in cli_version:
        raise RuntimeError("The probe requires the pinned CLI version")

    requests = []
    tool_calls = []
    events = []
    trace_lines = []
    trace.set_tracer_provider(TracerProvider())

    class ControlledProvider(CopilotRequestHandler):
        async def send_request(self, request, context):
            body = json.loads(request.content or b"{}")
            path = request.url.path
            if not path.endswith("/chat/completions"):
                # Never forward an unrecognized request to the network.
                return httpx.Response(404, json={"error": "controlled probe: unsupported endpoint"})
            requests.append(body)
            if len(requests) == 1:
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "probe-call-1",
                            "type": "function",
                            "function": {"name": "observe_case", "arguments": "{}"},
                        }
                    ],
                }
                reason = "tool_calls"
            else:
                delta = {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "strategy": "Synthetic red cube placement",
                            "target_object": "red cube",
                            "steps": ["Observe red cube", "Place cube in tray"],
                            "confidence": 0.5,
                        }
                    ),
                }
                reason = "stop"
            chunks = [
                {
                    "id": f"probe-{len(requests)}",
                    "object": "chat.completion.chunk",
                    "model": "rove-probe",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": f"probe-{len(requests)}",
                    "object": "chat.completion.chunk",
                    "model": "rove-probe",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
                },
            ]
            content = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content.encode()
            )

        async def open_websocket(self, context):
            raise RuntimeError("Controlled probe does not permit WebSocket requests")

    class RealClient:
        def __init__(self, **kwargs):
            self.trace_path = Path(kwargs["working_directory"]) / "runtime-traces.jsonl"
            kwargs["telemetry"] = {
                "exporter_type": "file",
                "file_path": str(self.trace_path),
                "capture_content": False,
            }
            self.inner = CopilotClient(
                **kwargs,
                connection=RuntimeConnection.for_stdio(path=cli_path, args=["--no-auto-update"]),
                request_handler=ControlledProvider(),
            )

        async def start(self):
            await self.inner.start()

        async def create_session(self, **kwargs):
            kwargs["tools"] = [Tool(**tool) for tool in kwargs["tools"]]
            return await self.inner.create_session(**kwargs)

        async def stop(self):
            await self.inner.stop()
            if self.trace_path.exists():
                trace_lines.extend(self.trace_path.read_text().splitlines())

    def observe(_):
        tool_calls.append(True)
        return {
            "objects": [{"label": "red cube", "frame": "synthetic-camera"}],
            "quality": "synthetic",
        }

    host = CopilotRuntime(
        CopilotRuntimeConfig(
            "rove-probe",
            {"type": "openai", "base_url": "http://127.0.0.1:1/v1", "wire_api": "completions"},
            cli_path=cli_path,
            timeout_seconds=45,
        ),
        tools=(
            RuntimeTool(
                "observe_case",
                "Read the synthetic case observation",
                {"type": "object", "properties": {}},
                observe,
            ),
        ),
        client_factory=RealClient,
        event_sink=events.append,
    )
    start = time.monotonic()
    with trace.get_tracer("rove.compatibility").start_as_current_span("rove.probe") as span:
        expected_trace = f"{span.get_span_context().trace_id:032x}"
        result = await host.run_stage(
            "plan", IMAGE, "Plan to place the red cube in a tray; call observe_case first."
        )
    plan = TaskPlan.model_validate({k: v for k, v in result.items() if k != "_runtime"})
    output_valid = plan.target_object == "red cube" and len(plan.steps) == 2
    image_sent = any("image_url" in json.dumps(body) for body in requests)
    observed_tools = [e for e in events if e["event_type"] == "rove.tool.started"]
    correlation = bool(observed_tools) and all(
        e.get("trace_id") == expected_trace for e in observed_tools
    )
    try:
        sdk_version = version("github-copilot-sdk")
    except PackageNotFoundError:
        sdk_version = "source checkout; verify tag separately"
    return {
        "sdk_version": sdk_version,
        "expected_sdk_version": SDK_VERSION,
        "expected_cli_version": CLI_VERSION,
        "cli_version": cli_version,
        "provider": "controlled synthetic responses; no live inference",
        "model_requests": len(requests),
        "image_delivered": image_sent,
        "tool_invocations": len(tool_calls),
        "trace_correlated": correlation,
        "native_trace_records": len(trace_lines),
        "event_types": sorted({e["event_type"] for e in events}),
        "usage_events": len(result["_runtime"]["usage"] or []),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "output_valid": output_valid,
        "output_schema": "rove.models.core.TaskPlan",
        "passed": image_sent and len(tool_calls) == 1 and correlation and output_valid,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-path", default=shutil.which("copilot"))
    args = parser.parse_args()
    if not args.cli_path:
        parser.error("Copilot CLI was not found; provide --cli-path")
    try:
        result = asyncio.run(probe(args.cli_path))
    except Exception as exc:
        # Provider errors may contain prompts or credentials. Report only type.
        print(
            json.dumps(
                {
                    "passed": False,
                    "failure_type": type(exc).__name__,
                    "detail": "Controlled compatibility probe failed; no live provider was validated.",
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
