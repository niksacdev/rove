"""User-initiated Copilot device login with a bounded, private CLI process."""

import asyncio
import re
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from rove.runtime.copilot import github_login_environment


@dataclass
class LoginOperation:
    operation_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "starting"
    user_code: str | None = None
    reason: str | None = None
    started: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = field(default=None, repr=False)

    def public(self):
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            "verification_uri": "https://github.com/login/device" if self.user_code else None,
            "user_code": self.user_code if self.status == "pending" else None,
            "reason": self.reason,
        }


class CopilotLogin:
    def __init__(self, directory, *, spawn=asyncio.create_subprocess_exec, timeout=600):
        self.directory = directory
        self.spawn = spawn
        self.timeout = timeout
        self.operation = None
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            if self.operation and self.operation.task and not self.operation.task.done():
                return self.operation.public()
            cli = shutil.which("copilot")
            if not cli:
                raise ValueError("Install the Copilot CLI on the ROVE host before signing in")
            Path(self.directory).mkdir(parents=True, exist_ok=True, mode=0o700)
            self.operation = LoginOperation()
            self.operation.task = asyncio.create_task(self._run(self.operation, cli))
            return self.operation.public()

    def get(self, operation_id):
        if not self.operation or self.operation.operation_id != operation_id:
            raise KeyError(operation_id)
        return self.operation.public()

    async def cancel(self, operation_id):
        self.get(operation_id)
        task = self.operation.task
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.operation.status, self.operation.reason = "cancelled", "Sign-in cancelled."
        return self.operation.public()

    async def close(self):
        if self.operation and self.operation.task and not self.operation.task.done():
            await self.cancel(self.operation.operation_id)

    async def _run(self, operation, cli):
        process = None
        try:
            process = await self.spawn(
                cli,
                "--no-auto-update",
                "login",
                "--device-code",
                env=github_login_environment(self.directory),
                cwd=self.directory,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async with asyncio.timeout(self.timeout):
                text = ""
                while chunk := await process.stdout.read(1024):
                    text = (text + chunk.decode("utf-8", errors="replace"))[-8192:]
                    # Only the fixed GitHub device code is exposed; never echo CLI output.
                    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
                    code = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", clean)
                    if code and "github.com/login/device" in clean:
                        operation.user_code, operation.status = code.group(1), "pending"
                result = await process.wait()
                operation.status = "complete" if result == 0 else "failed"
                operation.reason = (
                    None if result == 0 else "GitHub sign-in did not complete. Try again."
                )
        except TimeoutError:
            operation.status, operation.reason = (
                "expired",
                "The sign-in request expired. Start again.",
            )
        except asyncio.CancelledError:
            operation.status, operation.reason = "cancelled", "Sign-in cancelled."
            raise
        except Exception:
            operation.status, operation.reason = (
                "failed",
                "Could not start Copilot sign-in on the ROVE host.",
            )
        finally:
            if process and process.returncode is None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (TimeoutError, ProcessLookupError):
                    if process.returncode is None:
                        with suppress(ProcessLookupError):
                            process.kill()
                        await process.wait()
