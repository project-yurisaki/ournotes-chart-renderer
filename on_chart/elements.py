"""Typed preview model; source nodes are not generated gameplay judgments."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ss_format import TimingMap


@dataclass(frozen=True)
class Note:
    tick: int
    time_ms: int
    pos: float
    width: float
    kind: str
    critical: bool = False
    direction: str = "up"
    ease: tuple[str, str] = ("linear", "linear")
    visible: bool = True
    alpha: str = "none"
    auto: bool = False
    operation: int = 1
    source_path: str = ""
    group_id: int | None = None
    node_index: int | None = None
    consumed: bool = False


@dataclass(frozen=True)
class Slide:
    id: int
    kind: str
    nodes: tuple[Note, ...]


@dataclass
class Chart:
    notes: list[Note]
    slides: list[Slide]
    timing: TimingMap
    events: dict[str, list[dict]]
    encoding: str
    warnings: list[str]
    source_count: int
    timeline_count: int
    source: dict[str, Any] = field(repr=False)
    mirror: bool = False

    @property
    def all_nodes(self):
        return [*self.notes, *(node for slide in self.slides for node in slide.nodes)]

    @property
    def start_tick(self):
        return min((node.tick for node in self.all_nodes), default=0)

    @property
    def end_tick(self):
        ticks = [0, *(node.tick for node in self.all_nodes)]
        for entries in self.events.values():
            for event in entries:
                ticks.extend((event["startTick"], event["endTick"]) if "startTick" in event else (event["t"],))
        return max(ticks)


@dataclass(frozen=True)
class RenderMetadata:
    title: str = "Our Notes"
    artist: str = ""
    difficulty: str = ""
    level: str = ""
    full_combo: int | None = None
    jacket_path: str = ""
    score_id: int | None = None
