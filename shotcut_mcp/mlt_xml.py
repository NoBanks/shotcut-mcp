"""MLT XML builder for shotcut-mcp. Pure XML generation - no subprocess calls."""

import uuid
import xml.etree.ElementTree as ET
from typing import Optional


def _frames(seconds: float, fps: float) -> int:
    return max(0, round(seconds * fps))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class MltProject:
    """In-memory model of an MLT project that serializes to valid .mlt XML."""

    def __init__(self, name: str, width: int = 1920, height: int = 1080, fps: float = 25.0) -> None:
        self.name = name
        self.width = width
        self.height = height
        self.fps = fps
        # Ordered list of clip entries: dicts with keys:
        #   producer_id, path, in_s, out_s (float, None means full clip)
        self._clips: list[dict] = []
        # Text overlay entries: dicts with keys:
        #   text, start_s, end_s, font_size, color, bg_color, halign, valign
        self._text_overlays: list[dict] = []
        # Filter entries keyed by producer_id: list of {filter_name, params}
        self._filters: dict[str, list[dict]] = {}
        # Transition entries: list of {from_id, to_id, duration_s, style}
        self._transitions: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_clip(self, path: str, in_s: float = 0.0, out_s: Optional[float] = None) -> str:
        """Append a clip to the timeline. Returns producer_id."""
        producer_id = _new_id("producer")
        self._clips.append({
            "producer_id": producer_id,
            "path": path,
            "in_s": in_s,
            "out_s": out_s,
        })
        self._filters[producer_id] = []
        return producer_id

    def split_clip(self, producer_id: str, at_s: float) -> tuple[str, str]:
        """Split a clip at at_s seconds from clip start. Returns (left_id, right_id)."""
        idx = self._find_clip(producer_id)
        original = self._clips[idx]
        in_s = original["in_s"]
        out_s = original["out_s"]

        split_point = in_s + at_s

        left_id = _new_id("producer")
        right_id = _new_id("producer")

        left_clip = {
            "producer_id": left_id,
            "path": original["path"],
            "in_s": in_s,
            "out_s": split_point,
        }
        right_clip = {
            "producer_id": right_id,
            "path": original["path"],
            "in_s": split_point,
            "out_s": out_s,
        }

        self._clips[idx:idx + 1] = [left_clip, right_clip]
        self._filters[left_id] = list(self._filters.get(producer_id, []))
        self._filters[right_id] = list(self._filters.get(producer_id, []))
        self._filters.pop(producer_id, None)

        return left_id, right_id

    def remove_segment(self, start_s: float, end_s: float) -> None:
        """Ripple-delete the timeline segment between start_s and end_s."""
        # Build running timeline positions
        positions = self._clip_positions()
        new_clips = []
        for clip, clip_start, clip_end in positions:
            clip_in = clip["in_s"]
            clip_out = clip["out_s"]
            duration = (clip_out - clip_in) if clip_out is not None else (clip_end - clip_start)

            # No overlap: keep as-is
            if clip_end <= start_s or clip_start >= end_s:
                new_clips.append(dict(clip))
                continue

            # Fully inside removal window: drop
            if clip_start >= start_s and clip_end <= end_s:
                continue

            # Partial overlap on the left: trim right end
            if clip_start < start_s < clip_end <= end_s:
                trim_dur = start_s - clip_start
                new_out = clip_in + trim_dur
                new_clips.append({**clip, "out_s": new_out})
                continue

            # Partial overlap on the right: trim left start
            if start_s <= clip_start < end_s < clip_end:
                skip = end_s - clip_start
                new_in = clip_in + skip
                new_clips.append({**clip, "in_s": new_in})
                continue

            # Clip spans the whole removal: split into two
            if clip_start < start_s and clip_end > end_s:
                left_out = clip_in + (start_s - clip_start)
                new_clips.append({**clip, "out_s": left_out})
                right_in = clip_in + (end_s - clip_start)
                right_id = _new_id("producer")
                new_clips.append({
                    **clip,
                    "producer_id": right_id,
                    "in_s": right_in,
                })
                self._filters[right_id] = list(self._filters.get(clip["producer_id"], []))

        self._clips = new_clips

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
    ) -> None:
        """Burn a text overlay onto the timeline between start_s and end_s."""
        self._text_overlays.append({
            "text": text,
            "start_s": start_s,
            "end_s": end_s,
            "font_size": font_size,
            "color": color,
            "bg_color": bg_color,
            "halign": halign,
            "valign": valign,
        })

    def apply_filter(self, producer_id: str, filter_name: str, params: dict) -> None:
        """Attach a filter to a producer. Raises KeyError if producer_id not found."""
        if producer_id not in self._filters:
            raise KeyError(producer_id)
        self._filters[producer_id].append({"filter_name": filter_name, "params": dict(params)})

    def set_transition(
        self,
        from_producer_id: str,
        to_producer_id: str,
        duration_s: float,
        style: str = "luma",
    ) -> None:
        """Add a crossfade transition between two adjacent clips."""
        if from_producer_id not in self._filters:
            raise KeyError(from_producer_id)
        if to_producer_id not in self._filters:
            raise KeyError(to_producer_id)
        self._transitions.append({
            "from_id": from_producer_id,
            "to_id": to_producer_id,
            "duration_s": duration_s,
            "style": style,
        })

    def project_duration_s(self) -> float:
        """Return total timeline duration in seconds."""
        positions = self._clip_positions()
        if not positions:
            return 0.0
        return positions[-1][2]

    def to_xml(self) -> str:
        """Serialize to MLT XML string (deterministic, UTF-8)."""
        root = ET.Element("mlt")
        root.set("version", "7.0.0")
        root.set("title", self.name)
        root.set("width", str(self.width))
        root.set("height", str(self.height))
        root.set("fps", str(self.fps))

        # Profile
        profile = ET.SubElement(root, "profile")
        profile.set("description", "custom")
        profile.set("width", str(self.width))
        profile.set("height", str(self.height))
        profile.set("progressive", "1")
        fps_num = str(int(round(self.fps * 1000)))
        fps_den = "1000"
        profile.set("frame_rate_num", fps_num)
        profile.set("frame_rate_den", fps_den)
        profile.set("display_aspect_num", str(self.width))
        profile.set("display_aspect_den", str(self.height))
        profile.set("sample_aspect_num", "1")
        profile.set("sample_aspect_den", "1")
        profile.set("colorspace", "709")

        # Clip producers
        positions = self._clip_positions()
        for clip, clip_start, clip_end in positions:
            pid = clip["producer_id"]
            in_s = clip["in_s"]
            out_s = clip["out_s"]
            in_frames = _frames(in_s, self.fps)
            out_frames = _frames(out_s, self.fps) - 1 if out_s is not None else -1

            prod = ET.SubElement(root, "producer")
            prod.set("id", pid)
            prod.set("in", str(in_frames))
            if out_frames >= 0:
                prod.set("out", str(out_frames))

            _prop(prod, "mlt_type", "producer")
            _prop(prod, "mlt_service", "avformat")
            _prop(prod, "resource", clip["path"])

            for f in self._filters.get(pid, []):
                filt = ET.SubElement(prod, "filter")
                filt.set("mlt_service", f["filter_name"])
                for k, v in f["params"].items():
                    _prop(filt, k, str(v))

        # Text overlay producers
        text_ids: list[tuple[str, dict]] = []
        for overlay in self._text_overlays:
            tid = _new_id("text")
            dur_frames = _frames(overlay["end_s"] - overlay["start_s"], self.fps)
            text_ids.append((tid, overlay))

            prod = ET.SubElement(root, "producer")
            prod.set("id", tid)
            prod.set("in", "0")
            prod.set("out", str(max(0, dur_frames - 1)))

            _prop(prod, "mlt_service", "text:html")
            _prop(prod, "string", overlay["text"])
            _prop(prod, "font", "Sans")
            _prop(prod, "fontsize", str(overlay["font_size"]))
            _prop(prod, "color", overlay["color"])
            _prop(prod, "bgcolor", overlay["bg_color"])
            _prop(prod, "halign", overlay["halign"])
            _prop(prod, "valign", overlay["valign"])

        # Build total duration
        total_dur = self.project_duration_s()
        total_frames = _frames(total_dur, self.fps)

        # Main video playlist
        playlist = ET.SubElement(root, "playlist")
        playlist.set("id", "playlist0")

        for clip, clip_start, clip_end in positions:
            entry = ET.SubElement(playlist, "entry")
            entry.set("producer", clip["producer_id"])
            in_s = clip["in_s"]
            out_s = clip["out_s"]
            entry.set("in", str(_frames(in_s, self.fps)))
            if out_s is not None:
                entry.set("out", str(_frames(out_s, self.fps) - 1))

        # Text overlay playlist (separate track)
        text_playlist = ET.SubElement(root, "playlist")
        text_playlist.set("id", "playlist_text")

        # Fill with blank segments, replacing where text appears
        if text_ids:
            # Sort overlays by start_s
            sorted_texts = sorted(text_ids, key=lambda x: x[1]["start_s"])
            cursor = 0.0
            for tid, overlay in sorted_texts:
                gap = overlay["start_s"] - cursor
                if gap > 0.001:
                    blank = ET.SubElement(text_playlist, "blank")
                    blank.set("length", str(_frames(gap, self.fps)))
                dur_frames = _frames(overlay["end_s"] - overlay["start_s"], self.fps)
                entry = ET.SubElement(text_playlist, "entry")
                entry.set("producer", tid)
                entry.set("in", "0")
                entry.set("out", str(max(0, dur_frames - 1)))
                cursor = overlay["end_s"]

        # Tractor (main sequence)
        tractor = ET.SubElement(root, "tractor")
        tractor.set("id", "tractor0")
        tractor.set("in", "0")
        tractor.set("out", str(max(0, total_frames - 1)))

        track0 = ET.SubElement(tractor, "track")
        track0.set("producer", "playlist0")

        if text_ids:
            track1 = ET.SubElement(tractor, "track")
            track1.set("producer", "playlist_text")

            # Composite transition to layer text over video
            trans = ET.SubElement(tractor, "transition")
            trans.set("id", "trans_composite")
            trans.set("in", "0")
            trans.set("out", str(max(0, total_frames - 1)))
            _prop(trans, "mlt_service", "composite")
            _prop(trans, "a_track", "0")
            _prop(trans, "b_track", "1")
            _prop(trans, "geometry", "0%/0%:100%x100%:100")

        # Luma transitions between clips
        for t in self._transitions:
            from_positions = [p for p in positions if p[0]["producer_id"] == t["from_id"]]
            to_positions = [p for p in positions if p[0]["producer_id"] == t["to_id"]]
            if from_positions and to_positions:
                trans_start = from_positions[0][2] - t["duration_s"]
                trans_end = to_positions[0][2]
                trans = ET.SubElement(tractor, "transition")
                trans.set("id", _new_id("trans"))
                trans.set("in", str(_frames(trans_start, self.fps)))
                trans.set("out", str(_frames(trans_end, self.fps) - 1))
                _prop(trans, "mlt_service", t["style"])
                _prop(trans, "a_track", "0")
                _prop(trans, "b_track", "0")

        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def save(self, path: str) -> None:
        """Write .mlt file to disk."""
        xml = self.to_xml()
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)

    @classmethod
    def load(cls, path: str) -> "MltProject":
        """Parse an existing .mlt file into an MltProject."""
        tree = ET.parse(path)
        root = tree.getroot()

        name_from_title = root.get("title", "untitled")
        width = int(root.get("width", "1920"))
        height = int(root.get("height", "1080"))
        fps = float(root.get("fps", "25.0"))

        proj = cls(name=name_from_title, width=width, height=height, fps=fps)

        # Rebuild clips from producers
        producer_map: dict[str, dict] = {}
        for prod in root.findall("producer"):
            pid = prod.get("id", "")
            if not pid or pid.startswith("text"):
                continue
            path_val = None
            svc = None
            for prop in prod.findall("property"):
                if prop.get("name") == "resource":
                    path_val = prop.text
                if prop.get("name") == "mlt_service":
                    svc = prop.text
            if svc == "avformat" and path_val:
                in_f = int(prod.get("in", "0"))
                out_f_str = prod.get("out")
                producer_map[pid] = {
                    "path": path_val,
                    "in_s": in_f / fps,
                    "out_s": (int(out_f_str) + 1) / fps if out_f_str else None,
                }

        # Rebuild ordering from main playlist
        playlist = root.find("./playlist[@id='playlist0']")
        if playlist is not None:
            for entry in playlist.findall("entry"):
                pid = entry.get("producer", "")
                if pid in producer_map:
                    p = producer_map[pid]
                    new_pid = proj.add_clip(p["path"], p["in_s"], p["out_s"])
                    # Fix the producer_id to match the saved one
                    proj._clips[-1]["producer_id"] = new_pid
                    proj._filters[new_pid] = []

        return proj

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_clip(self, producer_id: str) -> int:
        for i, clip in enumerate(self._clips):
            if clip["producer_id"] == producer_id:
                return i
        raise KeyError(producer_id)

    def _clip_positions(self) -> list[tuple[dict, float, float]]:
        """Return list of (clip, start_s, end_s) in timeline order."""
        result = []
        cursor = 0.0
        for clip in self._clips:
            in_s = clip["in_s"]
            out_s = clip["out_s"]
            if out_s is None:
                # Unknown duration - use a sentinel 10s default
                duration = 10.0
            else:
                duration = out_s - in_s
            clip_end = cursor + duration
            result.append((clip, cursor, clip_end))
            cursor = clip_end
        return result


def _prop(parent: ET.Element, name: str, value: str) -> ET.Element:
    """Helper to add a <property name="...">value</property> child."""
    el = ET.SubElement(parent, "property")
    el.set("name", name)
    el.text = value
    return el
