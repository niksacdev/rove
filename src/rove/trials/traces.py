"""Clock-aware trace lanes; producer time never silently becomes host time."""

from __future__ import annotations

from datetime import datetime
from math import isfinite


def _seconds(value, unit):
    try:
        if unit == "iso8601":
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        scale = {"ns": 1e9, "us": 1e6, "ms": 1e3, "s": 1, "second": 1}.get(unit)
        number = float(value) / scale if scale else None
        return number if number is not None and isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def trace_lanes(events: list[dict], *, complete: bool = True) -> dict:
    lanes, spans, points = {}, {}, []
    for event in events:
        clock = event.get("source_clock_id") or "unattributed"
        stage = event.get("stage") or "unassigned"
        lane_id = f"{clock}:{stage}"
        lane = lanes.setdefault(
            lane_id,
            {
                "id": lane_id,
                "clock_id": clock,
                "stage": stage,
                "alignment": event.get("clock_alignment", "unknown"),
                "items": [],
            },
        )
        instant = _seconds(event.get("source_timestamp"), event.get("time_unit"))
        start = _seconds(event.get("start_time_unix_nano"), "ns")
        end = _seconds(event.get("end_time_unix_nano"), "ns")
        if not event.get("source_clock_id"):
            instant = start = end = None
        item = {
            "event_id": event.get("event_id"),
            "sequence": event.get("sequence"),
            "name": event.get("name")
            or (event.get("data") or {}).get("name")
            or event.get("event_type", "Event"),
            "trace_id": event.get("trace_id"),
            "span_id": event.get("span_id"),
            "parent_span_id": event.get("parent_span_id"),
            "start": start if start is not None else instant,
            "end": end,
            "received_at": event.get("recorded_at"),
            "evidence_refs": event.get("evidence_refs", []),
        }
        key = (clock, item["trace_id"], item["span_id"])
        kind = event.get("event_type", "")
        if item["span_id"] and kind.endswith((".ended", ".completed")) and key in spans:
            previous = spans[key]
            if previous["end"] is None or kind.endswith(".ended"):
                previous["end"] = end if end is not None else instant
                previous["end_event_id"] = item["event_id"]
            continue
        if item["span_id"] and kind.endswith(".started") and key in spans:
            continue
        lane["items"].append(item)
        if item["span_id"] and kind.endswith(".started"):
            spans[key] = item
        points.append(item)
    # Compare relative producer timing only inside a source clock. Different trial
    # clocks can be juxtaposed from their own origins, never wall-clock synchronized.
    origins = {}
    for lane in lanes.values():
        starts = [i["start"] for i in lane["items"] if i["start"] is not None]
        if starts:
            origins[lane["clock_id"]] = min(origins.get(lane["clock_id"], min(starts)), min(starts))
    for lane in lanes.values():
        origin = origins.get(lane["clock_id"])
        for item in lane["items"]:
            start, end = item.pop("start"), item.pop("end")
            item["offset_seconds"] = (
                start - origin if start is not None and origin is not None else None
            )
            item["duration_seconds"] = (
                end - start if start is not None and end is not None and end >= start else None
            )
            item["timing"] = "producer" if item["offset_seconds"] is not None else "unavailable"
    return {
        "lanes": list(lanes.values()),
        "complete": complete,
        "note": "Lanes share an origin only within the same source clock. Different clocks are not synchronized. Missing or unfinished spans have no inferred duration.",
    }


def trial_trace(store, trial_id: str, maximum: int = 10000) -> dict:
    trial = store.get(trial_id)
    events = []
    while len(events) < min(trial["event_count"], maximum):
        page = store.events(trial_id, limit=min(1000, maximum - len(events)), offset=len(events))
        if not page:
            break
        events.extend(page)
    return {
        "trial_id": trial_id,
        "loaded_events": len(events),
        "total_events": trial["event_count"],
        **trace_lanes(events, complete=len(events) == trial["event_count"]),
    }
