"""Smoke tests for shotcut-mcp.

Tests MUST import list_tools and call_tool from server.py as functions and
await them directly. Do NOT call server.list_tools() because mcp Server decorators
register handlers, not callable methods.
Pattern reference: lambda-cloud-mcp/tests/test_server.py
"""

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shotcut_mcp.mlt_xml import MltProject
from shotcut_mcp.schemas import (
    ALLOWED_FILTERS,
    AddClipInput,
    ApplyFilterInput,
    CreateProjectInput,
    ListProjectsInput,
    ProjectInfoInput,
    RemoveSegmentInput,
    RenderInput,
    SplitClipInput,
    SetTransitionInput,
    AppendTextOverlayInput,
)
from shotcut_mcp.server import call_tool, list_tools


@pytest.mark.asyncio
async def test_list_tools_registers_nine_tools():
    tools = await list_tools()
    names = {t.name for t in tools}
    assert names == {
        "create_project",
        "add_clip",
        "split_clip",
        "remove_segment",
        "append_text_overlay",
        "apply_filter",
        "set_transition",
        "project_info",
        "render",
        "list_projects",
    }


# ---- Schema validation ------------------------------------------------

def test_create_project_input_defaults():
    inp = CreateProjectInput(name="myfilm")
    assert inp.width == 1920
    assert inp.height == 1080
    assert inp.fps == 25.0


def test_add_clip_input_defaults():
    inp = AddClipInput(project_name="myfilm", clip_path="/some/video.mp4")
    assert inp.in_s == 0.0
    assert inp.out_s is None


def test_split_clip_input():
    inp = SplitClipInput(project_name="myfilm", producer_id="producer_abc", at_s=3.5)
    assert inp.at_s == 3.5


def test_remove_segment_input():
    inp = RemoveSegmentInput(project_name="myfilm", start_s=2.0, end_s=5.0)
    assert inp.start_s == 2.0


def test_append_text_overlay_input_defaults():
    inp = AppendTextOverlayInput(project_name="myfilm", text="Hello", start_s=0.0, end_s=3.0)
    assert inp.font_size == 48
    assert inp.halign == "center"
    assert inp.valign == "bottom"


def test_apply_filter_input_defaults():
    inp = ApplyFilterInput(project_name="myfilm", producer_id="producer_abc", filter_name="brightness")
    assert inp.params == {}


def test_set_transition_input_defaults():
    inp = SetTransitionInput(
        project_name="myfilm",
        from_producer_id="p1",
        to_producer_id="p2",
        duration_s=0.5,
    )
    assert inp.style == "luma"


def test_render_input_defaults():
    inp = RenderInput(project_name="myfilm", output_path="/tmp/out.mp4")
    assert inp.preset == "mp4-1080p"


def test_allowed_filters_set():
    assert isinstance(ALLOWED_FILTERS, set)
    assert "brightness" in ALLOWED_FILTERS
    assert "volume" in ALLOWED_FILTERS
    assert "fadeInBrightness" in ALLOWED_FILTERS
    assert "fadeOutBrightness" in ALLOWED_FILTERS
    assert "crop" in ALLOWED_FILTERS


# ---- MltProject XML --------------------------------------------------

def test_mlt_project_to_xml_produces_valid_xml():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    xml_str = proj.to_xml()
    root = ET.fromstring(xml_str)
    assert root.tag == "mlt"
    assert root.get("title") == "test"
    assert root.get("width") == "1920"
    assert root.get("height") == "1080"
    assert root.get("fps") == "25.0"


def test_mlt_project_add_clip_updates_duration():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    pid = proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=10.0)
    assert pid.startswith("producer_")
    assert proj.project_duration_s() == pytest.approx(10.0)


def test_mlt_project_xml_has_avformat_producer():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=5.0)
    root = ET.fromstring(proj.to_xml())
    producers = root.findall("producer")
    assert len(producers) >= 1
    props = {p.get("name"): p.text for p in producers[0].findall("property")}
    assert props.get("mlt_service") == "avformat"
    assert props.get("resource") == "/fake/clip.mp4"


def test_mlt_project_split_clip():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    pid = proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=10.0)
    left, right = proj.split_clip(pid, at_s=5.0)
    assert left != right
    assert proj.project_duration_s() == pytest.approx(10.0)


def test_mlt_project_remove_segment():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=20.0)
    proj.remove_segment(5.0, 10.0)
    assert proj.project_duration_s() == pytest.approx(15.0)


def test_mlt_project_text_overlay_in_xml():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=10.0)
    proj.append_text_overlay("HELLO", start_s=2.0, end_s=5.0)
    root = ET.fromstring(proj.to_xml())
    text_prods = [p for p in root.findall("producer")
                  if any(prop.get("name") == "mlt_service" and prop.text == "text:html"
                         for prop in p.findall("property"))]
    assert len(text_prods) == 1


def test_mlt_project_apply_filter_invalid_raises():
    proj = MltProject(name="test", width=1920, height=1080, fps=25.0)
    pid = proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=5.0)
    # Valid filter should not raise
    proj.apply_filter(pid, "brightness", {"level": 1.5})
    # Invalid producer should raise
    with pytest.raises(KeyError):
        proj.apply_filter("nonexistent_id", "brightness", {})


def test_mlt_project_save_and_load(tmp_path):
    proj = MltProject(name="roundtrip", width=1280, height=720, fps=30.0)
    proj.add_clip("/fake/clip.mp4", in_s=0.0, out_s=5.0)
    mlt_path = str(tmp_path / "roundtrip.mlt")
    proj.save(mlt_path)
    assert Path(mlt_path).exists()
    loaded = MltProject.load(mlt_path)
    assert loaded.name == "roundtrip"
    assert loaded.width == 1280
    assert loaded.height == 720
    assert loaded.fps == 30.0


# ---- Tool: create_project --------------------------------------------

@pytest.mark.asyncio
async def test_create_project_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    # Reload server module to pick up new env
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    result = await ct("create_project", {"name": "myfilm"})
    data = json.loads(result[0].text)
    assert data["name"] == "myfilm"
    assert data["width"] == 1920
    assert "project_path" in data
    assert Path(data["project_path"]).exists()


# ---- Tool: add_clip --------------------------------------------------

@pytest.mark.asyncio
async def test_add_clip_uses_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "myfilm2"})
    # Use this test file as the clip_path (it exists on disk)
    result = await ct("add_clip", {
        "project_name": "myfilm2",
        "clip_path": __file__,
        "in_s": 0.0,
        "out_s": 5.0,
    })
    data = json.loads(result[0].text)
    assert "producer_id" in data
    assert data["clip_path"] == __file__


@pytest.mark.asyncio
async def test_add_clip_missing_file_returns_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "myfilm3"})
    result = await ct("add_clip", {
        "project_name": "myfilm3",
        "clip_path": "/does/not/exist.mp4",
    })
    data = json.loads(result[0].text)
    assert data["code"] == "clip_not_found"


# ---- Tool: project_info ----------------------------------------------

@pytest.mark.asyncio
async def test_project_info_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "info_test", "width": 1280, "height": 720})
    result = await ct("project_info", {"project_name": "info_test"})
    data = json.loads(result[0].text)
    assert data["name"] == "info_test"
    assert data["width"] == 1280
    assert data["height"] == 720
    assert "duration_s" in data
    assert "clip_count" in data


# ---- Tool: apply_filter error path -----------------------------------

@pytest.mark.asyncio
async def test_apply_filter_invalid_filter_name(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "filter_test"})
    result = await ct("apply_filter", {
        "project_name": "filter_test",
        "producer_id": "anything",
        "filter_name": "notafilter",
    })
    data = json.loads(result[0].text)
    assert data["code"] == "invalid_filter"


# ---- Tool: remove_segment error path ---------------------------------

@pytest.mark.asyncio
async def test_remove_segment_invalid_range(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "seg_test"})
    result = await ct("remove_segment", {
        "project_name": "seg_test",
        "start_s": 5.0,
        "end_s": 5.0,
    })
    data = json.loads(result[0].text)
    assert data["code"] == "invalid_segment"


# ---- Tool: render (mocked) -------------------------------------------

@pytest.mark.asyncio
async def test_render_mocked(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "render_test"})
    output_path = str(tmp_path / "output.mp4")
    # Create a dummy output file so stat succeeds
    Path(output_path).write_bytes(b"dummy")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = "Current Frame: 49, percentage: 98"
    mock_result.stdout = ""

    with patch("shotcut_mcp.server.subprocess.run", return_value=mock_result):
        result = await ct("render", {
            "project_name": "render_test",
            "output_path": output_path,
            "preset": "mp4-1080p",
        })
    data = json.loads(result[0].text)
    assert data["output_path"] == output_path
    assert data["preset"] == "mp4-1080p"
    assert data["duration_s"] >= 0
    assert data["file_size_bytes"] >= 0


@pytest.mark.asyncio
async def test_render_invalid_preset(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "preset_test"})
    result = await ct("render", {
        "project_name": "preset_test",
        "output_path": "/tmp/out.mp4",
        "preset": "4k-dolby-vision",
    })
    data = json.loads(result[0].text)
    assert data["code"] == "invalid_preset"


# ---- Tool: list_projects ---------------------------------------------

@pytest.mark.asyncio
async def test_list_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOTCUT_MCP_WORKDIR", str(tmp_path))
    import importlib
    import shotcut_mcp.server as srv
    importlib.reload(srv)
    from shotcut_mcp.server import call_tool as ct

    await ct("create_project", {"name": "film_a"})
    await ct("create_project", {"name": "film_b"})
    result = await ct("list_projects", {})
    data = json.loads(result[0].text)
    assert "projects" in data
    names = {p["name"] for p in data["projects"]}
    assert "film_a" in names
    assert "film_b" in names
