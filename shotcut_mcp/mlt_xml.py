"""MLT XML builder for shotcut-mcp. Pure XML generation - no subprocess calls.

State model
-----------
The project is an ordered list of clips on one video track, plus crossfades
between adjacent clips, per-clip filters, and timed text overlays.

Every tool call loads the project, changes it, and saves it again, so the full
state is persisted next to the .mlt in a sidecar ``<name>.state.json``. The .mlt
is always regenerated from that state. Producer ids are stable across calls.

XML layout (the same shapes Shotcut writes, so the file opens in Shotcut):
- one ``avformat`` producer per clip, carrying its filters;
- ``playlist0`` holds the clips in order. A crossfade between clip A and clip B is
  a small tractor placed between them in the playlist: A's tail and B's head on two
  tracks, mixed by a ``luma`` (video) and a ``mix`` (audio) transition. A loses its
  last ``d`` seconds and B its first ``d`` seconds in the plain entries, so the cut
  gets shorter by ``d`` per crossfade, as in any NLE;
- text overlays are ``qtext`` filters on the main tractor with in/out points.
"""

import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


def _frames(seconds: float, fps: float) -> int:
    return max(0, round(seconds * fps))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# Named filters an agent can ask for. Each maps to real MLT services; fades are
# keyframed brightness/volume so they render in stock melt.
FILTER_SPECS = {
    "brightness": "brightness",
    "volume": "volume",
    "crop": "crop",
    "fadeInBrightness": "brightness",
    "fadeOutBrightness": "brightness",
    "fadeInAudio": "volume",
    "fadeOutAudio": "volume",
}


def _rgba(color: str) -> str:
    """Accept #RRGGBB or #AARRGGBB (Shotcut order) and return MLT's #AARRGGBB."""
    c = color.strip()
    if c.startswith("#") and len(c) == 7:
        return "#ff" + c[1:]
    return c


class MltProject:
    """In-memory model of an MLT project that serializes to valid .mlt XML."""

    def __init__(self, name: str, width: int = 1920, height: int = 1080, fps: float = 25.0) -> None:
        self.name = name
        self.width = width
        self.height = height
        self.fps = fps
        # Ordered clips: {producer_id, path, in_s, out_s}
        self._clips: list[dict] = []
        # Timed overlays: {overlay_id, text, start_s, end_s, font_size, color, bg_color, halign, valign}
        self._text_overlays: list[dict] = []
        # Filters keyed by producer_id: list of {filter_name, params}
        self._filters: dict[str, list[dict]] = {}
        # Crossfades: {from_id, to_id, duration_s, style}
        self._transitions: list[dict] = []

    # ------------------------------------------------------------------
    # Editing API
    # ------------------------------------------------------------------

    def add_clip(self, path: str, in_s: float = 0.0, out_s: Optional[float] = None) -> str:
        """Append a clip to the timeline. Returns producer_id."""
        if out_s is not None and out_s <= in_s:
            raise ValueError(f"out_s ({out_s}) must be greater than in_s ({in_s})")
        producer_id = _new_id("producer")
        self._clips.append({
            "producer_id": producer_id,
            "path": path,
            "in_s": float(in_s),
            "out_s": None if out_s is None else float(out_s),
        })
        self._filters[producer_id] = []
        return producer_id

    def split_clip(self, producer_id: str, at_s: float) -> tuple[str, str]:
        """Split a clip at_s seconds after its own in point. Returns (left_id, right_id)."""
        idx = self._find_clip(producer_id)
        original = self._clips[idx]
        in_s = original["in_s"]
        out_s = original["out_s"]
        split_point = in_s + at_s
        if at_s <= 0 or (out_s is not None and split_point >= out_s):
            raise ValueError(f"at_s={at_s} is outside the clip")

        left_id = _new_id("producer")
        right_id = _new_id("producer")
        self._clips[idx:idx + 1] = [
            {**original, "producer_id": left_id, "out_s": split_point},
            {**original, "producer_id": right_id, "in_s": split_point},
        ]
        filters = self._filters.pop(producer_id, [])
        self._filters[left_id] = [dict(f) for f in filters]
        self._filters[right_id] = [dict(f) for f in filters]
        # A crossfade into the old clip now lands on the left half; out of it, the right half.
        for t in self._transitions:
            if t["to_id"] == producer_id:
                t["to_id"] = left_id
            if t["from_id"] == producer_id:
                t["from_id"] = right_id
        return left_id, right_id

    def remove_segment(self, start_s: float, end_s: float) -> None:
        """Ripple-delete the timeline segment between start_s and end_s (cut-list time)."""
        new_clips = []
        for clip, clip_start, clip_end in self._clip_positions(ignore_transitions=True):
            clip_in = clip["in_s"]
            if clip_end <= start_s or clip_start >= end_s:
                new_clips.append(dict(clip))
            elif clip_start >= start_s and clip_end <= end_s:
                self._filters.pop(clip["producer_id"], None)
            elif clip_start < start_s < clip_end <= end_s:
                new_clips.append({**clip, "out_s": clip_in + (start_s - clip_start)})
            elif start_s <= clip_start < end_s < clip_end:
                new_clips.append({**clip, "in_s": clip_in + (end_s - clip_start)})
            else:  # clip spans the whole removal: keep both sides
                new_clips.append({**clip, "out_s": clip_in + (start_s - clip_start)})
                right_id = _new_id("producer")
                new_clips.append({**clip, "producer_id": right_id, "in_s": clip_in + (end_s - clip_start)})
                self._filters[right_id] = [dict(f) for f in self._filters.get(clip["producer_id"], [])]
        self._clips = new_clips
        self._prune_transitions()

    def append_text_overlay(
        self,
        text: str,
        start_s: float,
        end_s: float,
        font_size: int = 48,
        color: str = "#ffffff",
        bg_color: str = "#80000000",
        halign: str = "center",
        valign: str = "bottom",
    ) -> str:
        """Burn a text overlay onto the timeline between start_s and end_s. Returns overlay_id."""
        if end_s <= start_s:
            raise ValueError("end_s must be greater than start_s")
        overlay_id = _new_id("text")
        self._text_overlays.append({
            "overlay_id": overlay_id,
            "text": text,
            "start_s": float(start_s),
            "end_s": float(end_s),
            "font_size": int(font_size),
            "color": color,
            "bg_color": bg_color,
            "halign": halign,
            "valign": valign,
        })
        return overlay_id

    def apply_filter(self, producer_id: str, filter_name: str, params: dict) -> None:
        """Attach a filter to a producer. Raises KeyError if producer_id not found."""
        if producer_id not in self._filters:
            raise KeyError(producer_id)
        if filter_name not in FILTER_SPECS:
            raise ValueError(f"Unknown filter: {filter_name}")
        self._filters[producer_id].append({"filter_name": filter_name, "params": dict(params)})

    def set_transition(
        self,
        from_producer_id: str,
        to_producer_id: str,
        duration_s: float,
        style: str = "luma",
    ) -> None:
        """Crossfade between two ADJACENT clips (from directly before to)."""
        i = self._find_clip(from_producer_id)
        j = self._find_clip(to_producer_id)
        if j != i + 1:
            raise ValueError("Transitions join adjacent clips: to_producer_id must directly follow from_producer_id")
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        for idx in (i, j):
            if self._clip_len(self._clips[idx]) < duration_s * 2:
                raise ValueError(
                    f"Clip {self._clips[idx]['producer_id']} is too short for a {duration_s}s crossfade"
                )
        self._transitions = [t for t in self._transitions if t["from_id"] != from_producer_id]
        self._transitions.append({
            "from_id": from_producer_id,
            "to_id": to_producer_id,
            "duration_s": float(duration_s),
            "style": style,
        })

    def trim_clip(self, producer_id: str, in_s: Optional[float] = None, out_s: Optional[float] = None) -> None:
        """Set new source in/out points on a clip already on the timeline."""
        clip = self._clips[self._find_clip(producer_id)]
        new_in = clip["in_s"] if in_s is None else float(in_s)
        new_out = clip["out_s"] if out_s is None else float(out_s)
        if new_in < 0 or (new_out is not None and new_out <= new_in):
            raise ValueError(f"Invalid trim: in_s={new_in}, out_s={new_out}")
        clip["in_s"], clip["out_s"] = new_in, new_out

    def remove_clip(self, producer_id: str) -> None:
        """Ripple-delete one clip."""
        idx = self._find_clip(producer_id)
        del self._clips[idx]
        self._filters.pop(producer_id, None)
        self._prune_transitions()

    def move_clip(self, producer_id: str, to_position: int) -> None:
        """Move a clip to a 1-based timeline position."""
        idx = self._find_clip(producer_id)
        if not 1 <= to_position <= len(self._clips):
            raise ValueError(f"to_position must be between 1 and {len(self._clips)}")
        clip = self._clips.pop(idx)
        self._clips.insert(to_position - 1, clip)
        self._prune_transitions()

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def project_duration_s(self) -> float:
        """Total timeline duration in seconds, crossfades included."""
        positions = self._clip_positions()
        return positions[-1][2] if positions else 0.0

    def timeline(self) -> dict:
        """The whole edit as plain data, for agents to inspect and verify."""
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "duration_s": round(self.project_duration_s(), 3),
            "clips": [
                {
                    "position": n + 1,
                    "producer_id": clip["producer_id"],
                    "clip_path": clip["path"],
                    "in_s": round(clip["in_s"], 3),
                    "out_s": None if clip["out_s"] is None else round(clip["out_s"], 3),
                    "timeline_start_s": round(start, 3),
                    "timeline_end_s": round(end, 3),
                    "filters": [f["filter_name"] for f in self._filters.get(clip["producer_id"], [])],
                }
                for n, (clip, start, end) in enumerate(self._clip_positions())
            ],
            "transitions": [dict(t) for t in self._transitions],
            "text_overlays": [dict(o) for o in self._text_overlays],
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_xml(self) -> str:
        """Serialize to MLT XML string (UTF-8)."""
        fps = self.fps
        root = ET.Element("mlt")
        root.set("LC_NUMERIC", "C")
        root.set("version", "7.0.0")
        root.set("title", self.name)
        root.set("width", str(self.width))
        root.set("height", str(self.height))
        root.set("fps", str(fps))

        profile = ET.SubElement(root, "profile")
        profile.set("description", "custom")
        profile.set("width", str(self.width))
        profile.set("height", str(self.height))
        profile.set("progressive", "1")
        profile.set("frame_rate_num", str(int(round(fps * 1000))))
        profile.set("frame_rate_den", "1000")
        profile.set("display_aspect_num", str(self.width))
        profile.set("display_aspect_den", str(self.height))
        profile.set("sample_aspect_num", "1")
        profile.set("sample_aspect_den", "1")
        profile.set("colorspace", "709")

        # One producer per clip, filters attached. The producer spans the whole
        # source; playlist entries choose the in/out.
        for clip in self._clips:
            pid = clip["producer_id"]
            prod = ET.SubElement(root, "producer")
            prod.set("id", pid)
            _prop(prod, "mlt_service", "avformat")
            _prop(prod, "resource", clip["path"])
            for f in self._filters.get(pid, []):
                self._filter_xml(prod, f, clip)

        # MLT resolves ids in document order, so each crossfade tractor must be
        # written before the playlist that references it.
        playlist = ET.Element("playlist")
        playlist.set("id", "playlist0")
        fade_into = {t["to_id"]: t for t in self._transitions}
        fade_out_of = {t["from_id"]: t for t in self._transitions}

        for clip in self._clips:
            pid = clip["producer_id"]
            in_f = _frames(clip["in_s"], fps)
            out_f = _frames(clip["in_s"] + self._clip_len(clip), fps) - 1
            head = fade_into.get(pid)
            tail = fade_out_of.get(pid)
            if head:
                in_f += _frames(head["duration_s"], fps)
            if tail:
                out_f -= _frames(tail["duration_s"], fps)
            if out_f >= in_f:
                entry = ET.SubElement(playlist, "entry")
                entry.set("producer", pid)
                entry.set("in", str(in_f))
                entry.set("out", str(out_f))
            if tail:
                tid = self._transition_tractor(root, clip, tail)
                entry = ET.SubElement(playlist, "entry")
                entry.set("producer", tid)
                entry.set("in", "0")
                entry.set("out", str(_frames(tail["duration_s"], fps) - 1))

        root.append(playlist)

        total_frames = _frames(self.project_duration_s(), fps)
        tractor = ET.SubElement(root, "tractor")
        tractor.set("id", "tractor0")
        tractor.set("in", "0")
        tractor.set("out", str(max(0, total_frames - 1)))
        track = ET.SubElement(tractor, "track")
        track.set("producer", "playlist0")

        for overlay in self._text_overlays:
            filt = ET.SubElement(tractor, "filter")
            filt.set("id", overlay["overlay_id"])
            filt.set("in", str(_frames(overlay["start_s"], fps)))
            filt.set("out", str(max(0, _frames(overlay["end_s"], fps) - 1)))
            _prop(filt, "mlt_service", "qtext")
            _prop(filt, "argument", overlay["text"])
            _prop(filt, "text", overlay["text"])
            _prop(filt, "geometry", "5%/5%:90%x90%:100")
            _prop(filt, "family", "DejaVu Sans")
            _prop(filt, "size", str(overlay["font_size"]))
            _prop(filt, "weight", "600")
            _prop(filt, "fgcolour", _rgba(overlay["color"]))
            _prop(filt, "bgcolour", _rgba(overlay["bg_color"]))
            _prop(filt, "olcolour", "#ff000000")
            _prop(filt, "outline", "0")
            _prop(filt, "pad", "12")
            _prop(filt, "halign", overlay["halign"])
            _prop(filt, "valign", overlay["valign"])

        # Shotcut recognizes its main timeline tractor by this property.
        _prop(tractor, "shotcut", "1")

        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def to_state(self) -> dict:
        return {
            "version": 2,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "clips": self._clips,
            "filters": self._filters,
            "transitions": self._transitions,
            "text_overlays": self._text_overlays,
        }

    def save(self, path: str) -> None:
        """Write the .mlt and its sidecar state file."""
        Path(path).write_text(self.to_xml(), encoding="utf-8")
        _state_path(path).write_text(json.dumps(self.to_state(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "MltProject":
        """Load a project. Uses the sidecar state when present (lossless); otherwise
        imports the plain clip list from a .mlt written elsewhere."""
        state_file = _state_path(path)
        if state_file.exists():
            s = json.loads(state_file.read_text(encoding="utf-8"))
            proj = cls(name=s["name"], width=s["width"], height=s["height"], fps=s["fps"])
            proj._clips = s["clips"]
            proj._filters = {k: v for k, v in s["filters"].items()}
            for clip in proj._clips:
                proj._filters.setdefault(clip["producer_id"], [])
            proj._transitions = s["transitions"]
            proj._text_overlays = s["text_overlays"]
            return proj
        return cls._import_mlt(path)

    @classmethod
    def _import_mlt(cls, path: str) -> "MltProject":
        root = ET.parse(path).getroot()
        profile = root.find("profile")
        if profile is not None:
            width = int(profile.get("width", "1920"))
            height = int(profile.get("height", "1080"))
            fps = int(profile.get("frame_rate_num", "25")) / int(profile.get("frame_rate_den", "1"))
        else:
            width = int(root.get("width", "1920"))
            height = int(root.get("height", "1080"))
            fps = float(root.get("fps", "25.0"))
        proj = cls(name=root.get("title", Path(path).stem), width=width, height=height, fps=fps)

        resources = {}
        for prod in list(root.findall("producer")) + list(root.findall("chain")):
            props = {p.get("name"): p.text for p in prod.findall("property")}
            if props.get("resource"):
                resources[prod.get("id", "")] = props["resource"]

        playlist = root.find("./playlist[@id='playlist0']")
        if playlist is None:
            playlist = root.find("playlist")
        if playlist is not None:
            for entry in playlist.findall("entry"):
                pid = entry.get("producer", "")
                if pid not in resources:
                    continue
                in_f = int(entry.get("in", "0"))
                out_f = entry.get("out")
                proj._clips.append({
                    "producer_id": pid,
                    "path": resources[pid],
                    "in_s": in_f / fps,
                    "out_s": (int(out_f) + 1) / fps if out_f is not None else None,
                })
                proj._filters[pid] = []
        return proj

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_clip(self, producer_id: str) -> int:
        for i, clip in enumerate(self._clips):
            if clip["producer_id"] == producer_id:
                return i
        raise KeyError(producer_id)

    @staticmethod
    def _clip_len(clip: dict) -> float:
        # A clip added without an out point and never probed: 10s is a stand-in only.
        # The server probes real durations, so this is reached only by direct use.
        if clip["out_s"] is None:
            return 10.0
        return clip["out_s"] - clip["in_s"]

    def _prune_transitions(self) -> None:
        order = [c["producer_id"] for c in self._clips]
        keep = []
        for t in self._transitions:
            if t["from_id"] in order and t["to_id"] in order and order.index(t["to_id"]) == order.index(t["from_id"]) + 1:
                keep.append(t)
        self._transitions = keep

    def _clip_positions(self, ignore_transitions: bool = False) -> list[tuple[dict, float, float]]:
        """(clip, start_s, end_s) in timeline order. Each crossfade overlaps the
        two clips it joins, pulling everything after it earlier by its duration."""
        fade_into = {} if ignore_transitions else {t["to_id"]: t["duration_s"] for t in self._transitions}
        result = []
        cursor = 0.0
        for clip in self._clips:
            start = cursor - fade_into.get(clip["producer_id"], 0.0)
            end = start + self._clip_len(clip)
            result.append((clip, start, end))
            cursor = end
        return result

    def _filter_xml(self, parent: ET.Element, f: dict, clip: dict) -> None:
        name = f["filter_name"]
        params = dict(f["params"])
        filt = ET.SubElement(parent, "filter")
        _prop(filt, "mlt_service", FILTER_SPECS[name])
        fps = self.fps
        in_f = _frames(clip["in_s"], fps)
        out_f = _frames(clip["in_s"] + self._clip_len(clip), fps) - 1
        dur = _frames(float(params.pop("duration_s", 1.0)), fps)
        if name == "fadeInBrightness":
            _prop(filt, "level", f"{in_f}=0;{in_f + dur}=1")
        elif name == "fadeOutBrightness":
            _prop(filt, "level", f"{max(in_f, out_f - dur)}=1;{out_f}=0")
        elif name == "fadeInAudio":
            _prop(filt, "level", f"{in_f}=-60;{in_f + dur}=0")
        elif name == "fadeOutAudio":
            _prop(filt, "level", f"{max(in_f, out_f - dur)}=0;{out_f}=-60")
        for k, v in params.items():
            _prop(filt, k, str(v))

    def _transition_tractor(self, root: ET.Element, clip_a: dict, t: dict) -> str:
        """Shotcut-style crossfade: A's tail and B's head on two tracks, luma + mix."""
        fps = self.fps
        clip_b = self._clips[self._find_clip(t["to_id"])]
        d = _frames(t["duration_s"], fps)
        a_out = _frames(clip_a["in_s"] + self._clip_len(clip_a), fps) - 1
        b_in = _frames(clip_b["in_s"], fps)

        tid = _new_id("transition")
        tr = ET.SubElement(root, "tractor")
        tr.set("id", tid)
        tr.set("in", "0")
        tr.set("out", str(d - 1))
        _prop(tr, "shotcut:transition", "lumaMix")
        ta = ET.SubElement(tr, "track")
        ta.set("producer", clip_a["producer_id"])
        ta.set("in", str(a_out - d + 1))
        ta.set("out", str(a_out))
        tb = ET.SubElement(tr, "track")
        tb.set("producer", clip_b["producer_id"])
        tb.set("in", str(b_in))
        tb.set("out", str(b_in + d - 1))

        video = ET.SubElement(tr, "transition")
        video.set("id", _new_id("luma"))
        video.set("out", str(d - 1))
        _prop(video, "a_track", "0")
        _prop(video, "b_track", "1")
        _prop(video, "factory", "loader")
        _prop(video, "mlt_service", "luma")
        audio = ET.SubElement(tr, "transition")
        audio.set("id", _new_id("mix"))
        audio.set("out", str(d - 1))
        _prop(audio, "a_track", "0")
        _prop(audio, "b_track", "1")
        _prop(audio, "start", "-1")
        _prop(audio, "accepts_blanks", "1")
        _prop(audio, "mlt_service", "mix")
        return tid


def _state_path(mlt_path: str) -> Path:
    p = Path(mlt_path)
    return p.with_name(p.stem + ".state.json")


def _prop(parent: ET.Element, name: str, value: str) -> ET.Element:
    """Helper to add a <property name="...">value</property> child."""
    el = ET.SubElement(parent, "property")
    el.set("name", name)
    el.text = value
    return el
