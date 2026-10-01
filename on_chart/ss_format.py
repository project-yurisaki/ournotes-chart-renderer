"""Read the SsRoot/SsScore format.

The normalized timeline contains source notes, not generated gameplay combos.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from copy import deepcopy
import gzip
import io
import json
import math
from pathlib import Path
import struct
import zlib

PPQ = 480
LANE_UNITS = 24
MAX_BYTES = 64 * 1024 * 1024
NOTE_TYPES = ("tap", "flick", "trace", "long", "guide", "node")
DIRECTIONS = ("up", "left", "right", "down")
EASES = ("linear", "in", "out")
OPERATIONS = {
    1: "Normal", 20: "SlideBegin", 21: "SlideConnection", 22: "SlideEnd",
    40: "Flick", 41: "SlideBeginFlick", 42: "SlideEndFlick", 60: "Trace",
    61: "SlideBeginTrace", 62: "SlideEndTrace", 63: "SlideConnectionTrace",
    80: "HiddenSlideBegin", 82: "HiddenSlideEnd", 100: "GuideBegin",
    101: "GuideBeginNormal", 102: "GuideBeginFlick", 103: "GuideEnd",
    104: "GuideBeginTrace", 105: "GuideEndTrace", 122: "Hidden",
}


class ChartError(ValueError):
    """Invalid input, with a source field path where available."""


def f32(value: float) -> float:
    """Match an IL2CPP System.Single store / ARM64 scalar float operation."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def trunc_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _obj(value, path):
    if not isinstance(value, dict):
        raise ChartError(f"{path}: expected object")
    return value


def _array(value, path):
    if not isinstance(value, list):
        raise ChartError(f"{path}: expected array")
    return value


def _int(value, path):
    if type(value) is not int or not -(2**31) <= value < 2**31:
        raise ChartError(f"{path}: expected signed 32-bit integer")
    return value


def _float(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ChartError(f"{path}: expected finite number")
    try:
        result = f32(float(value))
    except (ValueError, OverflowError):
        raise ChartError(f"{path}: expected finite single-precision number") from None
    if not math.isfinite(result):
        raise ChartError(f"{path}: expected finite single-precision number")
    return result


def _bool(value, path):
    if type(value) is not bool:
        raise ChartError(f"{path}: expected boolean")
    return value


def _enum(value, options, path):
    if value not in options:
        raise ChartError(f"{path}: expected one of {', '.join(options)}, got {value!r}")
    return value


def _required(obj, key, path):
    if key not in obj:
        raise ChartError(f"{path}.{key}: required field is missing")
    return obj[key]


def _reject_constant(value):
    raise ChartError(f"non-finite JSON number: {value}")


def _json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ChartError(f"non-finite JSON number: {value}")
    return number


def decode_text(data: bytes, *, max_bytes: int = MAX_BYTES):
    if max_bytes <= 0:
        raise ChartError("max_bytes must be positive")
    if not data:
        raise ChartError("empty chart")
    if len(data) > max_bytes:
        raise ChartError(f"input exceeds {max_bytes} bytes")
    if data.startswith((b"UnityFS", b"UnityRaw", b"UnityWeb")):
        raise ChartError("Unity AssetBundle: extract the chart TextAsset bytes first")
    encoding = "json"
    if data.startswith(b"\x1f\x8b"):
        encoding = "gzip-json"
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(max_bytes + 1)
        except (OSError, EOFError, zlib.error) as exc:
            raise ChartError(f"invalid gzip: {exc}") from None
        if len(data) > max_bytes:
            raise ChartError(f"decompressed chart exceeds {max_bytes} bytes")
    try:
        return data.decode("utf-8-sig"), encoding
    except UnicodeDecodeError:
        raise ChartError("expected UTF-8 JSON or gzip-compressed UTF-8 JSON") from None


def decode_bytes(data: bytes, *, max_bytes: int = MAX_BYTES):
    text, encoding = decode_text(data, max_bytes=max_bytes)
    if text.lstrip().startswith("#") and "#BPM" in text:
        raise ChartError("legacy SUS text chart: retained by extract, not supported by the .ss JSON normalizer")
    try:
        # Newtonsoft JObject semantics: keep the final duplicate property.
        root = json.loads(text, parse_constant=_reject_constant, parse_float=_json_float)
    except json.JSONDecodeError as exc:
        raise ChartError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    except ChartError:
        raise
    except ValueError as exc:
        raise ChartError(f"invalid JSON number: {exc}") from None
    except RecursionError:
        raise ChartError("JSON nesting is too deep") from None
    return _obj(root, "$"), encoding


def _read_events(score, warnings):
    raw = score.get("events")
    if raw is None:
        raw = {}
    raw = _obj(raw, "$.score.events")
    out = {name: [] for name in ("bpm", "sig", "skill", "fever", "call")}
    for name in out:
        items = raw.get(name)
        if items is None:
            continue
        for index, value in enumerate(_array(items, f"$.score.events.{name}")):
            path = f"$.score.events.{name}[{index}]"
            if name == "skill":
                out[name].append({"t": _int(value, path), "sourcePath": path})
                continue
            if name == "fever":
                pair = _array(value, path)
                if len(pair) != 2:
                    raise ChartError(f"{path}: expected [startTick, endTick]")
                start, end = (_int(x, f"{path}[{i}]") for i, x in enumerate(pair))
                if end < start:
                    warnings.append(f"{path}: fever end precedes start")
                out[name].append({"startTick": start, "endTick": end, "sourcePath": path})
                continue
            obj = _obj(value, path)
            tick = _int(_required(obj, "t", path), path + ".t")
            entry = {"t": tick, "sourcePath": path}
            if name == "bpm":
                bpm = _float(_required(obj, "bpm", path), path + ".bpm")
                if bpm <= 0 or not math.isfinite(f32(bpm * PPQ)) or f32(bpm * PPQ) == 0:
                    raise ChartError(f"{path}.bpm: must be positive with a finite tick rate")
                entry["bpm"] = bpm
            elif name == "sig":
                pair = _array(_required(obj, "sig", path), path + ".sig")
                if len(pair) != 2:
                    raise ChartError(f"{path}.sig: expected [numerator, denominator]")
                n, d = (_int(x, f"{path}.sig[{i}]") for i, x in enumerate(pair))
                if n <= 0 or d <= 0 or n * 1920 > 2**31 - 1 or n * 1920 // d < 1:
                    raise ChartError(f"{path}.sig: invalid or overflowing time signature")
                entry.update(numerator=n, denominator=d)
            else:
                timing = _array(obj.get("timing", []), path + ".timing")
                entry["timing"] = [_int(x, f"{path}.timing[{i}]") for i, x in enumerate(timing)]
                # These are a subdivision mask, NOT absolute ticks.
                entry["activeFractions"] = [f32(f32(i + 1) / f32(len(timing)))
                                             for i, x in enumerate(entry["timing"]) if x == 1]
            out[name].append(entry)
    for name in ("bpm", "sig"):
        ticks = [e["t"] for e in out[name]]
        if len(set(ticks)) != len(ticks):
            warnings.append(f"$.score.events.{name}: duplicate ticks; last source entry wins in this tool; native equal-key sort order is unverified")
    return out


class TimingMap:
    """Native tick timing and 0-based bar positions, including float32 rounding."""

    def __init__(self, bpms=(), sigs=()):
        self.bpm_segments = []
        self.sig_segments = []
        bpms = sorted(bpms, key=lambda e: e["t"])
        sigs = sorted(sigs, key=lambda e: e["t"])
        if not bpms or bpms[0]["t"] >= 1:
            self.bpm_segments.append({"t": 0, "timeMs": 0, "bpm": 120.0, "default": True})
        elapsed, last_tick, bpm = 0.0, 0, 120.0
        for e in bpms:
            if e["t"] > last_tick:
                elapsed += (e["t"] - last_tick) * 60000.0 / f32(bpm * PPQ)
            self.bpm_segments.append({"t": e["t"], "timeMs": round(elapsed), "bpm": e["bpm"], "default": False})
            last_tick, bpm = e["t"], e["bpm"]
        if not sigs or sigs[0]["t"] >= 1:
            self.sig_segments.append({"t": 0, "bar": 0, "ticksPerMeasure": 1920,
                                      "numerator": 4, "denominator": 4, "default": True})
        last_tick, bar, ticks_per_measure = 0, 0, 1920
        for e in sigs:
            if e["t"] > last_tick:
                bar += trunc_div(e["t"] - last_tick, ticks_per_measure)
            ticks_per_measure = trunc_div(1920 * e["numerator"], e["denominator"])
            self.sig_segments.append({"t": e["t"], "bar": bar, "ticksPerMeasure": ticks_per_measure,
                                      "numerator": e["numerator"], "denominator": e["denominator"], "default": False})
            last_tick = e["t"]
        self._bpm_ticks = [e["t"] for e in self.bpm_segments]
        self._sig_ticks = [e["t"] for e in self.sig_segments]

    def time_ms(self, tick: int) -> int:
        e = self.bpm_segments[max(0, bisect_right(self._bpm_ticks, tick) - 1)]
        return math.floor(e["timeMs"] + (tick - e["t"]) * 60000.0 / f32(e["bpm"] * PPQ))

    def position(self, tick: int) -> dict:
        e = self.sig_segments[max(0, bisect_right(self._sig_ticks, tick) - 1)]
        delta = tick - e["t"]
        bars = trunc_div(delta, e["ticksPerMeasure"])
        remainder = delta - bars * e["ticksPerMeasure"]
        return {"tick": tick, "timeMs": self.time_ms(tick), "bar": e["bar"] + bars,
                "barProgress": f32(f32(remainder) / f32(e["ticksPerMeasure"])),
                "rhythm": remainder, "rhythmicUnit": e["ticksPerMeasure"]}


def _read_note(raw, path, warnings, depth=0):
    if depth > 64:
        raise ChartError(f"{path}: note nesting exceeds 64")
    obj = _obj(raw, path)
    pos_raw = obj.get("pos", 0)
    auto = pos_raw == "auto"
    pos = 0.0 if auto else _float(pos_raw, path + ".pos")
    ease = obj.get("ease", "linear")
    if isinstance(ease, str):
        ease = [ease, ease]
    ease = _array(ease, path + ".ease")
    if len(ease) != 2:
        raise ChartError(f"{path}.ease: expected string or [leftEase, rightEase]")
    ease = [_enum(x, EASES, f"{path}.ease[{i}]") for i, x in enumerate(ease)]
    alpha = obj.get("alpha", "none")
    if alpha not in ("none", "in", "out"):
        warnings.append(f"{path}.alpha: unknown value {alpha!r}; native fallback is 'none'")
        alpha = "none"
    note = {"sourcePath": path, "type": _enum(obj.get("type", "tap"), NOTE_TYPES, path + ".type"),
            "t": _int(obj.get("t", 0), path + ".t"), "pos": pos, "posAuto": auto,
            "size": _float(obj.get("size", 6), path + ".size"),
            "crit": _bool(obj.get("crit", False), path + ".crit"),
            "dir": _enum(obj.get("dir", "up"), DIRECTIONS, path + ".dir"), "ease": ease,
            "visible": _bool(obj.get("visible", True), path + ".visible"), "alpha": alpha, "node": []}
    if obj.get("node") is not None:
        note["node"] = [_read_note(x, f"{path}.node[{i}]", warnings, depth + 1)
                        for i, x in enumerate(_array(obj["node"], path + ".node"))]
    if note["size"] <= 0:
        warnings.append(f"{path}.size: nonpositive source width; native integer laneWidth clamps to 1")
    return note


def _ease(kind, t):
    if kind == "in":
        return f32(t * t)
    if kind == "out":
        return f32(f32(2.0 - t) * t)
    return t


def _geometry(nodes, i):
    note = nodes[i]
    if not note["posAuto"]:
        return note["pos"], note["size"]
    if i == 0 or i == len(nodes) - 1:
        return nodes[0]["pos"], nodes[0]["size"]
    left, right = i - 1, i + 1
    while left > 0 and nodes[left]["posAuto"]:
        left -= 1
    while right < len(nodes) - 1 and nodes[right]["posAuto"]:
        right += 1
    a, b = nodes[left], nodes[right]
    delta = b["t"] - a["t"]
    t = f32(f32(note["t"] - a["t"]) / f32(delta)) if delta > 0 else 0.0
    start = f32(a["pos"] + f32(_ease(a["ease"][0], t) * f32(b["pos"] - a["pos"])))
    ar, br = f32(a["pos"] + a["size"]), f32(b["pos"] + b["size"])
    end = f32(ar + f32(_ease(a["ease"][1], t) * f32(br - ar)))
    return start, f32(end - start)


def _operation(kind, nodes, index, overlap=None):
    n = nodes[index]
    if kind == "single":
        return {"tap": 1, "flick": 40, "trace": 60}[n["type"]]
    first, last = index == 0, index == len(nodes) - 1
    if kind == "long":
        if first:
            return 80 if not n["visible"] else {"flick": 41, "trace": 61}.get(n["type"], 20)
        if last:
            return 82 if not n["visible"] else {"flick": 42, "trace": 62}.get(n["type"], 22)
        if n["posAuto"]:
            return 21
        return 122 if not n["visible"] else (63 if n["type"] == "trace" else 21)
    if first:
        return {"tap": 101, "flick": 102, "trace": 104}[overlap["type"]] if overlap else 100
    if last:
        return 105 if n["visible"] and n["type"] == "trace" else 103
    return 63 if n["posAuto"] or n["visible"] else 122


def _overlap_key(note):
    return note["t"], round(note["pos"]), round(note["size"])


def _parse_bytes(data: bytes, *, mirror: bool, max_bytes: int) -> dict:
    root, encoding = decode_bytes(data, max_bytes=max_bytes)
    score = _obj(_required(root, "score", "$"), "$.score")
    warnings = []
    events = _read_events(score, warnings)
    notes = [_read_note(x, f"$.score.notes[{i}]", warnings)
             for i, x in enumerate(_array(score.get("notes", []), "$.score.notes"))]
    timing = TimingMap(events["bpm"], events["sig"])
    for kind, entries in events.items():
        for e in entries:
            if kind == "fever":
                e.update(start=timing.position(e["startTick"]), end=timing.position(e["endTick"]))
            else:
                e.update(timing.position(e["t"]))
    guide_keys = {_overlap_key(n["node"][0]) for n in notes if n["type"] == "guide"
                  and len(n["node"]) >= 2 and not n["node"][0]["posAuto"]}
    overlaps = {}
    for n in notes:
        if n["type"] in ("tap", "flick", "trace") and not n["posAuto"]:
            key = _overlap_key(n)
            if key in guide_keys:
                overlaps.setdefault(key, n)
    timeline, groups = [], []
    for source_index, note in enumerate(notes):
        kind = note["type"]
        if kind in ("tap", "flick", "trace"):
            nodes, group_id, kind = [note], None, "single"
            if note["posAuto"]:
                warnings.append(f"{note['sourcePath']}: standalone auto position uses the native stored position 0")
        elif kind in ("long", "guide"):
            nodes, group_id = note["node"], source_index
            groups.append({"id": group_id, "type": kind, "sourcePath": note["sourcePath"],
                           "nodeCount": len(nodes), "nodePaths": [n["sourcePath"] for n in nodes],
                           "startTick": nodes[0]["t"] if nodes else None,
                           "endTick": nodes[-1]["t"] if nodes else None, "converted": len(nodes) >= 2})
            if len(nodes) < 2:
                warnings.append(f"{note['sourcePath']}: native converter ignores groups with fewer than 2 nodes")
                continue
            if any(a["t"] > b["t"] for a, b in zip(nodes, nodes[1:])):
                warnings.append(f"{note['sourcePath']}: node ticks are not ordered; preserving source order")
            if nodes[0]["posAuto"] or nodes[-1]["posAuto"]:
                warnings.append(f"{note['sourcePath']}: auto endpoint inherits the first node's stored geometry")
        else:
            warnings.append(f"{note['sourcePath']}: top-level 'node' is preserved but not converted by the native converter")
            continue
        for i, n in enumerate(nodes):
            if n["node"]:
                warnings.append(f"{n['sourcePath']}: nested node list is preserved; only immediate group nodes enter the timeline")
            pos, size = _geometry(nodes, i)
            if kind == "single":
                pos, size = n["pos"], n["size"]
            if mirror:
                pos = f32(f32(24.0 - pos) - size)
            overlap = overlaps.get(_overlap_key(n)) if kind == "guide" and i == 0 and not n["posAuto"] else None
            direction_source = overlap or n
            direction = direction_source["dir"]
            if mirror:
                direction = {"left": "right", "right": "left"}.get(direction, direction)
            runtime_direction = direction if direction_source["type"] == "flick" and direction in ("left", "right") else "normal"
            op = _operation(kind, nodes, i, overlap)
            row = {"sourcePath": n["sourcePath"], "groupId": group_id, "groupType": kind,
                   "nodeIndex": i if group_id is not None else None, "type": n["type"],
                   **timing.position(n["t"]), "pos": pos, "size": size,
                   "laneIndex": round(pos), "laneWidth": max(1, round(size)),
                   "posAuto": n["posAuto"], "crit": n["crit"], "dir": direction,
                   "runtimeDirection": runtime_direction, "ease": n["ease"],
                   "visible": n["visible"], "alpha": n["alpha"],
                   "operationCode": op, "operation": OPERATIONS[op],
                   "overlapSourcePath": overlap["sourcePath"] if overlap else None,
                   "consumedByGuideStart": kind == "single" and overlaps.get(_overlap_key(n)) is n}
            if pos < 0 or f32(pos + size) > 24:
                warnings.append(f"{n['sourcePath']}: lane geometry extends outside [0, 24]")
            timeline.append(row)
    timeline.sort(key=lambda e: e["tick"])
    for i, row in enumerate(timeline):
        row["index"] = i
    times = [n["timeMs"] for n in timeline]
    return {"format": "OurNotes.ss", "toolVersion": 1, "encoding": encoding, "ppq": PPQ,
            "laneUnits": LANE_UNITS, "mirror": mirror,
            "source": deepcopy(root), "events": events, "notes": notes, "groups": groups,
            "timing": {"bpmSegments": timing.bpm_segments, "sigSegments": timing.sig_segments},
            "timeline": timeline, "warnings": warnings,
            "summary": {"sourceNoteCount": len(notes), "sourceNoteTypes": dict(Counter(n["type"] for n in notes)),
                        "groupCount": len(groups), "timelineEntryCount": len(timeline),
                        "firstNoteTimeMs": min(times) if times else None,
                        "lastNoteTimeMs": max(times) if times else None,
                        "eventCounts": {k: len(v) for k, v in events.items()}, "warningCount": len(warnings)}}


def parse_bytes(data: bytes, *, mirror: bool = False, max_bytes: int = MAX_BYTES) -> dict:
    try:
        return _parse_bytes(data, mirror=mirror, max_bytes=max_bytes)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ChartError(f"numeric overflow or invalid derived value: {exc}") from None
    except RecursionError:
        raise ChartError("chart nesting is too deep") from None


def parse_file(path, *, mirror: bool = False, max_bytes: int = MAX_BYTES) -> dict:
    path = Path(path)
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    result = parse_bytes(data, mirror=mirror, max_bytes=max_bytes)
    result["inputFile"] = str(path.resolve())
    return result
