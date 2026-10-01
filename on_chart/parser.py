"""SS TextAsset -> typed chart, using the native-verified format reader."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .elements import Chart, Note, Slide
from .ss_format import ChartError, MAX_BYTES, TimingMap, parse_bytes as normalize


def parse_chart(data: bytes | str, *, mirror=False, max_bytes=MAX_BYTES) -> Chart:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        raise ChartError("parse_chart expects UTF-8 text or raw SS/gzip bytes")
    result = normalize(data, mirror=mirror, max_bytes=max_bytes)
    by_path = {node["sourcePath"]: node for node in result["timeline"]}
    singles, groups = [], defaultdict(list)
    for row in result["timeline"]:
        critical = row["crit"]
        if row["overlapSourcePath"]:
            critical = by_path[row["overlapSourcePath"]]["crit"]
        ease = tuple(reversed(row["ease"])) if mirror else tuple(row["ease"])
        note = Note(row["tick"], row["timeMs"], row["pos"], row["size"], row["type"],
                    critical, row["runtimeDirection"] if row["runtimeDirection"] != "normal" else "up",
                    ease, row["visible"], row["alpha"], row["posAuto"], row["operationCode"],
                    row["sourcePath"], row["groupId"], row["nodeIndex"], row["consumedByGuideStart"])
        if row["groupId"] is None:
            singles.append(note)
        else:
            groups[row["groupId"]].append(note)
    slides = [Slide(group["id"], group["type"], tuple(sorted(groups[group["id"]], key=lambda n: n.node_index)))
              for group in result["groups"] if group["converted"]]
    return Chart(singles, slides, TimingMap(result["events"]["bpm"], result["events"]["sig"]),
                 result["events"], result["encoding"], result["warnings"], result["summary"]["sourceNoteCount"],
                 result["summary"]["timelineEntryCount"], result["source"], bool(mirror))


parse_bytes = parse_chart


def parse_file(path, *, mirror=False, max_bytes=MAX_BYTES) -> Chart:
    with Path(path).open("rb") as stream:
        return parse_chart(stream.read(max_bytes + 1), mirror=mirror, max_bytes=max_bytes)
