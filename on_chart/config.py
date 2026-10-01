"""Geometry and screenshot-inspired colors. Colors are ARGB integers."""
from dataclasses import dataclass, field
from pathlib import Path
import math

from .ss_format import ChartError

LOGO_PATH = Path(__file__).resolve().parent / "assets/logo.png"


@dataclass
class Palette:
    background: int = 0xFF0B1220
    panel: int = 0xFF111D30
    lane: int = 0xFF14243B
    lane_alt: int = 0xFF112035
    minor_grid: int = 0xFF22334B
    major_grid: int = 0xFF52718D
    border: int = 0xFF7398B2
    text: int = 0xFFF1F5FC
    muted: int = 0xFF94ACC4
    tap: int = 0xFF4CE1F4
    critical: int = 0xFFFFD459
    flick_up: int = 0xFFFFD459
    flick_left: int = 0xFF6EEB8B
    flick_right: int = 0xFFFF80AB
    trace: int = 0xFFBEA4FF
    slide_fill: int = 0xB84F6DD5
    slide_edge: int = 0xFF849BF2
    slide_head: int = 0xFFA4B4FF
    guide_fill: int = 0x40AB87F3
    guide_edge: int = 0xFFAF97F7
    fever: int = 0xFFF6CA64
    skill: int = 0xFFB79BFA
    call: int = 0xFF89BDD9


@dataclass
class Config:
    font_path: str = str(Path(__file__).resolve().parent / "assets/ChironHeiHK-SB.otf")
    track_width: int = 300
    pixels_per_beat: float = 40.0
    pixels_per_second: float = 320.0
    spacing: str = "time"
    page_target_height: int = 2560
    column_gap: int = 24
    time_gutter: int = 66
    event_gutter: int = 60
    padding: int = 28
    header_height: int = 248
    legend_height: int = 68
    note_height: float = 9.0
    curve_step: float = 5.0
    max_pixels: int = 80_000_000
    max_grid_lines: int = 40_000
    show_hidden: bool = False
    show_events: bool = True
    palette: Palette = field(default_factory=Palette)

    def validate(self):
        for key in ("track_width", "page_target_height", "column_gap", "time_gutter", "event_gutter",
                    "padding", "header_height", "legend_height",
                    "max_pixels", "max_grid_lines"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ChartError(f"config.{key} must be a positive integer")
        for key in ("pixels_per_beat", "pixels_per_second", "note_height", "curve_step"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ChartError(f"config.{key} must be a finite positive number")
        if self.track_width < 96 or self.header_height < 200 or self.legend_height < 56:
            raise ChartError("track_width >= 96, header_height >= 200 and legend_height >= 56 are required")
        if self.spacing not in ("beat", "time"):
            raise ChartError("spacing must be 'beat' or 'time'")
