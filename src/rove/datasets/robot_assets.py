"""Resolve an explicitly supplied robot description from the managed asset store."""

import hashlib


def robot_asset_path(store, reference):
    if not isinstance(reference, dict) or set(reference) - {"sha256", "size_bytes", "media_type"}:
        raise ValueError("Robot descriptions require a managed asset reference")
    digest = reference.get("sha256", "")
    metadata = store.asset_metadata(digest)
    if metadata["media_type"] not in {"application/xml", "text/xml", "application/urdf+xml"}:
        raise ValueError("Robot description must be a managed XML asset")
    if metadata["size_bytes"] > 4 * 1024 * 1024:
        raise ValueError("Robot description exceeds 4 MiB")
    path = store.asset_path(digest)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("Robot description asset integrity changed")
    return path
