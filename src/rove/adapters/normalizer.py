"""Action normalization utilities for VLA adapters.

Stateless helpers that adapters call explicitly. The pipeline never touches
action values directly — adapters own all model-specific preprocessing.
"""

from __future__ import annotations


def slice_to_target_dim(
    raw_actions: list[list[float]],
    target_dim: int,
) -> list[list[float]]:
    """Slice padded action arrays to target robot's action dimension.

    Pi0.5 always outputs 32-dim regardless of target robot — this slices
    to the first ``target_dim`` dimensions. Safe to call on actions that
    are already the correct size.
    """
    return [a[:target_dim] for a in raw_actions]


def normalize_proprioception(
    state: list[float],
    mean: list[float],
    std: list[float],
) -> list[float]:
    """Z-score normalize proprioception using per-dim statistics.

    ``mean`` and ``std`` come from the adapter's normalization stats
    (loaded from checkpoint or openpi server).
    """
    return [(s - m) / max(sd, 1e-8) for s, m, sd in zip(state, mean, std, strict=False)]


def denormalize_actions(
    actions: list[list[float]],
    mean: list[float],
    std: list[float],
) -> list[list[float]]:
    """Reverse z-score normalization on predicted actions."""
    return [
        [a * max(sd, 1e-8) + m for a, m, sd in zip(action, mean, std, strict=False)]
        for action in actions
    ]
