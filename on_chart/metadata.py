"""Optional metadata lookup from the extraction pipeline's bot resources."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .elements import RenderMetadata
from .ss_format import ChartError


@dataclass(frozen=True)
class ResourceEntry:
    chart_path: Path
    metadata: RenderMetadata
    sha256: str


def _resource_path(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ChartError(f"missing/outside resource file: {relative}")
    return target


def find_resource(resources, score_id: int) -> ResourceEntry:
    root = Path(resources).resolve()
    try:
        songs = json.loads((root / "songs.json").read_text(encoding="utf-8"))
        bands = json.loads((root / "bands.json").read_text(encoding="utf-8"))
        if songs.get("format") != "OurNotes.bot.songs" or bands.get("format") != "OurNotes.bot.bands":
            raise ChartError("expected pipeline songs.json and bands.json")
        band_names = {band["id"]: band["name"] for band in bands["bands"]}
        matches = [(song, difficulty, chart) for song in songs["songs"]
                   for difficulty, chart in song["difficulties"].items() if chart["id"] == score_id]
        if len(matches) != 1:
            raise ChartError(f"expected one song/difficulty for score {score_id}; found {len(matches)}")
        song, difficulty, chart = matches[0]
        override = song.get("bandNameOverride", {}).get("text", "")
        artist = override or " / ".join(band_names[band_id] for band_id in song["bandIds"])
        jacket = _resource_path(root, song["cover"]["path"])
        chart_path = _resource_path(root, chart["path"])
        metadata = RenderMetadata(song["title"], artist, difficulty, f'{chart["displayLevel"]:g}',
                                  chart["fullComboCount"], str(jacket), score_id)
        return ResourceEntry(chart_path, metadata, chart["sha256"])
    except ChartError:
        raise
    except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ChartError(f"invalid bot resource metadata: {exc}") from None


def verify_resource_bytes(entry, data):
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ChartError("input SS SHA256 does not match resource metadata; check score ID/resource version")
