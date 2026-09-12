"""Canonical, credential-free identities for evaluation inputs and configuration.

These identities describe supplied configuration; they do not archive remote weights
or guarantee that a robot environment can be reproduced.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET = re.compile(
    r"(?:^|_)(?:api_?key|secret|password|passwd|credential|authorization|access_token|"
    r"refresh_token|bearer_token|private_key|connection_string|sas_token|token)(?:$|_)",
    re.IGNORECASE,
)
_INLINE = {"image_base64", "image_bytes", "audio_base64", "video_base64", "data_base64", "base64"}
_SIGNED_QUERY = {"sig", "signature", "token", "key", "api_key", "access_token", "password"}
_REDACTED = "[REDACTED]"


def _normalized_key(key: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).replace("-", "_")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sanitize(value: object) -> object:
    """Remove credentials and inline media without mutating the caller's objects.

    Environment values are considered only for credential-shaped variable names. Their
    values are never included in a hash or output. Arbitrary prose is retained locally;
    callers exporting telemetry must additionally apply their content-capture policy.
    """
    secrets = {
        item
        for key, item in os.environ.items()
        if _SECRET.search(_normalized_key(key)) and len(item) >= 4
    }

    def clean(item: object, key: str = "") -> object:
        key = _normalized_key(key)
        if _SECRET.search(key) or key.lower() in {"token", "auth", "headers", "env"}:
            return _REDACTED
        if (key.lower() in _INLINE and isinstance(item, str | bytes)) or (
            isinstance(item, str) and item.startswith(("data:image/", "data:audio/", "data:video/"))
        ):
            if isinstance(item, str):
                raw = item.split(",", 1)[-1] if item.startswith("data:") else item
                try:
                    data = base64.b64decode(raw, validate=True)
                    encoding = "decoded"
                except (ValueError, TypeError):
                    data = raw.encode()
                    encoding = "unvalidated-base64"
            else:
                data, encoding = item, "bytes"
            return {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "encoding": encoding,
                "availability": "reference_only",
            }
        if isinstance(item, dict):
            return {str(k): clean(v, str(k)) for k, v in item.items()}
        if isinstance(item, list | tuple):
            return [clean(v) for v in item]
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, _REDACTED)
            if item.startswith(("http://", "https://")):
                try:
                    url = urlsplit(item)
                    host = url.netloc.rsplit("@", 1)[-1]
                    query = urlencode(
                        [
                            (
                                k,
                                _REDACTED
                                if (
                                    set(_normalized_key(k).lower().split("_")) & _SIGNED_QUERY
                                    or _SECRET.search(_normalized_key(k))
                                )
                                else v,
                            )
                            for k, v in parse_qsl(url.query, keep_blank_values=True)
                        ]
                    )
                    item = urlunsplit((url.scheme, host, url.path, query, ""))
                except ValueError:
                    return "[INVALID URL]"
            if item.startswith(("/Users/", "/home/", "C:\\Users\\")):
                return "[PRIVATE PATH]"
        return item

    return clean(value)


def snapshot(task: dict, strategy: dict, config: dict) -> dict:
    """Freeze independently identifiable inputs and the configured system."""
    parts = {"task": sanitize(task), "strategy": sanitize(strategy), "config": sanitize(config)}
    return {
        "schema_version": 1,
        **parts,
        "component_hashes": {name: content_hash(part) for name, part in parts.items()},
        "sha256": content_hash(parts),
        "provenance_status": "captured_before_execution",
    }
