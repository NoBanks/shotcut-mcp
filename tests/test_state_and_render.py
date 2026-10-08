"""State survives tool calls, and real melt renders match the timeline.

The render tests need melt + ffmpeg on PATH and are skipped otherwise."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import shotcut_mcp.server as srv
from shotcut_mcp.mlt_xml import MltProject

HAVE_MELT = bool(shutil.which("melt") and shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def call(name, args):
    return json.loads((await srv.call_tool(name, args))[0].text)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "WORKDIR", tmp_path)
    return tmp_path


def make_clip(path: Path, seconds: float, color: str, freq: int) -> str:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=24:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )
    return str(path)


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout)


def test_ids_filters_titles_and_transitions_survive_reload(tmp_path):
    p = str(tmp_path / "keep.mlt")
    proj = MltProject("keep", fps=24)
    a = proj.add_clip("/a.mp4", 0, 4)
    b = proj.add_clip("/b.mp4", 1, 5)
    proj.apply_filter(a, "fadeInBrightness", {"duration_s": 1})
    proj.append_text_overlay("TITLE", 0, 2)
    proj.set_transition(a, b, 1.0)
    proj.save(p)

    again = MltProject.load(p)
    assert [c["producer_id"] for c in again._clips] == [a, b]
    assert again._filters[a][0]["filter_name"] == "fadeInBrightness"
    assert again._text_overlays[0]["text"] == "TITLE"
    assert again._transitions[0]["to_id"] == b
    assert again.project_duration_s() == pytest.approx(7.0)


def test_transition_requires_adjacent_clips():
    proj = MltProject("adj", fps=24)
    a = proj.add_clip("/a.mp4", 0, 4)
    proj.add_clip("/b.mp4", 0, 4)
    c = proj.add_clip("/c.mp4", 0, 4)
    with pytest.raises(ValueError):
        proj.set_transition(a, c, 1.0)


def test_move_and_remove_drop_broken_transitions():
    proj = MltProject("mv", fps=24)
    a = proj.add_clip("/a.mp4", 0, 4)
    b = proj.add_clip("/b.mp4", 0, 4)
    proj.add_clip("/c.mp4", 0, 4)
    proj.set_transition(a, b, 1.0)
    proj.move_clip(a, 3)
    assert proj._transitions == []
    assert proj.timeline()["clips"][2]["producer_id"] == a


def test_import_plain_mlt_keeps_ids(tmp_path):
    proj = MltProject("plain", fps=25)
    a = proj.add_clip("/a.mp4", 1, 3)
    p = tmp_path / "plain.mlt"
    p.write_text(proj.to_xml(), encoding="utf-8")  # no sidecar
    imported = MltProject.load(str(p))
    assert imported._clips[0]["producer_id"] == a
    assert imported.project_duration_s() == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_tool_ids_are_usable_on_the_next_call(workdir, monkeypatch):
    monkeypatch.setattr(srv, "probe_duration_s", lambda path: 6.0)
    clip = workdir / "x.mp4"
    clip.write_bytes(b"x")
    await call("create_project", {"name": "ids", "fps": 24})
    a = (await call("add_clip", {"project_name": "ids", "clip_path": str(clip), "in_s": 0, "out_s": 3}))["producer_id"]
    b = (await call("add_clip", {"project_name": "ids", "clip_path": str(clip), "in_s": 3}))["producer_id"]
    t = await call("set_transition", {"project_name": "ids", "from_producer_id": a, "to_producer_id": b, "duration_s": 0.5})
    assert "error" not in t
    tl = await call("get_timeline", {"project_name": "ids"})
    assert [c["producer_id"] for c in tl["clips"]] == [a, b]
    assert tl["clips"][1]["out_s"] == 6.0  # probed, not a 10s guess
    assert tl["duration_s"] == pytest.approx(5.5)


@pytest.mark.skipif(not HAVE_MELT, reason="melt/ffmpeg not installed")
@pytest.mark.asyncio
async def test_real_render_matches_timeline(workdir):
    red = make_clip(workdir / "red.mp4", 4, "red", 440)
    blue = make_clip(workdir / "blue.mp4", 4, "blue", 880)
    await call("create_project", {"name": "real", "width": 320, "height": 240, "fps": 24})
    a = (await call("add_clip", {"project_name": "real", "clip_path": red, "in_s": 0.5, "out_s": 3.5}))["producer_id"]
    b = (await call("add_clip", {"project_name": "real", "clip_path": blue, "in_s": 0, "out_s": 3}))["producer_id"]
    await call("set_transition", {"project_name": "real", "from_producer_id": a, "to_producer_id": b, "duration_s": 1})
    await call("apply_filter", {"project_name": "real", "producer_id": a, "filter_name": "fadeInBrightness", "params": {"duration_s": 0.5}})
    await call("append_text_overlay", {"project_name": "real", "text": "CUTROOM", "start_s": 0, "end_s": 2, "font_size": 32})
    out = str(workdir / "real.mp4")
    r = await call("render", {"project_name": "real", "output_path": out, "preset": "mp4-project"})
    assert "error" not in r, r
    # 3s + 3s - 1s crossfade
    assert duration(out) == pytest.approx(5.0, abs=0.1)

    def frame_rgb(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", out, "-frames:v", "1",
                              "-vf", "crop=20:20:300:100,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return tuple(raw[:3])

    r0, g0, b0 = frame_rgb(1.0)   # red clip, after fade-in
    assert r0 > 150 and b0 < 80
    rm, gm, bm = frame_rgb(2.5)   # middle of the dissolve: both colors
    assert rm > 60 and bm > 60
    r1, g1, b1 = frame_rgb(4.5)   # blue clip
    assert b1 > 150 and r1 < 80
    r_start, _, _ = frame_rgb(0.0)  # fade-in starts dark
    assert r_start < 60


@pytest.mark.skipif(not HAVE_MELT, reason="melt/ffmpeg not installed")
@pytest.mark.asyncio
async def test_real_title_renders_text_not_placeholder(workdir):
    grey = make_clip(workdir / "grey.mp4", 3, "gray", 440)
    await call("create_project", {"name": "title", "width": 320, "height": 240, "fps": 24})
    await call("add_clip", {"project_name": "title", "clip_path": grey})
    await call("append_text_overlay", {"project_name": "title", "text": "HELLO", "start_s": 0, "end_s": 3,
                                       "font_size": 40, "valign": "middle", "bg_color": "#00000000"})
    out = str(workdir / "title.mp4")
    r = await call("render", {"project_name": "title", "output_path": out, "preset": "mp4-project"})
    assert "error" not in r, r
    png = str(workdir / "title.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", out, "-frames:v", "1", png], check=True)
    assert Path(png).stat().st_size > 0
