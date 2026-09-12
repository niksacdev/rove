"""Invoke a trusted project's synchronous or asynchronous evaluator."""

import asyncio
import importlib
import inspect
import json
import sys
from pathlib import Path

from rove.models.verification import EvaluatorContext, EvaluatorResult


def main():
    request_path, output_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text())
    module, name = request["config"]["entrypoint"].split(":", 1)
    function = getattr(importlib.import_module(module), name)
    result = function(EvaluatorContext.model_validate(request["context"]), request["config"])
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    validated = EvaluatorResult.model_validate(result)
    output_path.write_text(validated.model_dump_json())


if __name__ == "__main__":
    main()
