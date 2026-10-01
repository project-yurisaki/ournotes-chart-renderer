"""Our Notes SS parser and flat chart renderer."""
from .config import Config, Palette
from .elements import Chart, Note, RenderMetadata, Slide
from .parser import ChartError, parse_bytes, parse_chart, parse_file
from .judgement import Judgement, JudgementTimeline, build_judgements

__version__ = "0.1.0"
__all__ = ["Chart", "Note", "Slide", "RenderMetadata", "Config", "Palette", "ChartError",
           "parse_bytes", "parse_chart", "parse_file", "render", "save_image",
           "Judgement", "JudgementTimeline", "build_judgements"]


def render(*args, **kwargs):
    from .renderer import render as implementation
    return implementation(*args, **kwargs)


def save_image(*args, **kwargs):
    from .renderer import save_image as implementation
    return implementation(*args, **kwargs)
