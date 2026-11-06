"""Playback support: which files browsers can play natively, tags, and embedded cover art."""

from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4, MP4Cover

# Formats every current browser plays in <audio> without help. Anything else isn't offered.
PLAYABLE = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}

COVER_FILES = ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "front.png")


def mime_type(path: Path | str) -> str | None:
    return PLAYABLE.get(Path(path).suffix.lower())


def _first(tags, *keys) -> str | None:
    if not tags:
        return None
    for k in keys:
        v = tags.get(k)
        if v:
            v = v[0] if isinstance(v, list) else v
            text = getattr(v, "text", None)
            v = text[0] if text else v
            return str(v)
    return None


def tags(path: Path) -> dict:
    """Title/artist/album/... plus duration and bitrate. Missing values are None."""
    try:
        f = mutagen.File(path)
    except Exception:
        f = None
    if f is None:
        return {}
    t = f.tags
    if isinstance(f, MP4):
        get = lambda *k: _first(t, *k)
        out = {"title": get("\xa9nam"), "artist": get("\xa9ART"), "album": get("\xa9alb"),
               "albumartist": get("aART"), "year": get("\xa9day"), "genre": get("\xa9gen")}
        trkn = t.get("trkn") if t else None
        out["track"] = str(trkn[0][0]) if trkn else None
    elif t is not None and isinstance(t, ID3):
        get = lambda *k: _first(t, *k)
        out = {"title": get("TIT2"), "artist": get("TPE1"), "album": get("TALB"),
               "albumartist": get("TPE2"), "year": get("TDRC", "TYER"), "genre": get("TCON"),
               "track": get("TRCK")}
    else:  # Vorbis comments (FLAC, Ogg, Opus)
        get = lambda *k: _first(t, *k)
        out = {"title": get("title"), "artist": get("artist"), "album": get("album"),
               "albumartist": get("albumartist", "album artist"), "year": get("date", "year"),
               "genre": get("genre"), "track": get("tracknumber")}
    info = f.info
    out["duration"] = round(getattr(info, "length", 0) or 0, 2) or None
    bitrate = getattr(info, "bitrate", 0)
    out["bitrate"] = bitrate or None
    out["sample_rate"] = getattr(info, "sample_rate", None)
    out["bits_per_sample"] = getattr(info, "bits_per_sample", None)
    out["channels"] = getattr(info, "channels", None)
    return out


def cover(path: Path) -> tuple[bytes, str] | None:
    """Embedded front cover (or the first picture), else a cover image in the album folder."""
    try:
        f = mutagen.File(path)
    except Exception:
        f = None
    if f is not None:
        if isinstance(f, FLAC) and f.pictures:
            pic = next((p for p in f.pictures if p.type == 3), f.pictures[0])
            return pic.data, pic.mime or "image/jpeg"
        if isinstance(f.tags, ID3):
            apics = f.tags.getall("APIC")
            if apics:
                pic = next((p for p in apics if p.type == 3), apics[0])
                return pic.data, pic.mime or "image/jpeg"
        if isinstance(f, MP4) and f.tags and f.tags.get("covr"):
            c = f.tags["covr"][0]
            return bytes(c), "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        if f.tags is not None and "metadata_block_picture" in f.tags:  # Ogg/Opus
            import base64
            from mutagen.flac import Picture
            try:
                pic = Picture(base64.b64decode(f.tags["metadata_block_picture"][0]))
                return pic.data, pic.mime or "image/jpeg"
            except Exception:
                pass
    folder = path.parent
    try:
        names = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for n in COVER_FILES:
        if n in names:
            return names[n].read_bytes(), "image/png" if n.endswith(".png") else "image/jpeg"
    return None
