"""Shotcut MCP Server - programmatic MLT/Shotcut video editing via melt."""

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .mlt_xml import MltProject
from .schemas import (
    ALLOWED_FILTERS,
    AddClipInput,
    AppendTextOverlayInput,
    ApplyFilterInput,
    CreateProjectInput,
    GetTimelineInput,
    ListProjectsInput,
    MoveClipInput,
    ProjectInfoInput,
    RemoveClipInput,
    RemoveSegmentInput,
    RenderInput,
    SetTransitionInput,
    SplitClipInput,
    TrimClipInput,
)

APP_NAME = "shotcut-mcp"
server = Server(APP_NAME)

WORKDIR = Path(os.environ.get("SHOTCUT_MCP_WORKDIR", "~/shotcut-mcp-projects")).expanduser()
MELT_BIN = os.environ.get("MELT_BIN", "melt")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")

# "project" renders at the project's own frame size (keeps vertical projects vertical).
_X264 = {"vcodec": "libx264", "preset": "veryfast", "crf": "20", "pix_fmt": "yuv420p",
         "acodec": "aac", "ar": "48000", "ab": "192k", "movflags": "+faststart"}
RENDER_PRESETS = {
    "mp4-project": dict(_X264),
    "mp4-1080p": {**_X264, "s": "1920x1080"},
    "mp4-720p": {**_X264, "s": "1280x720"},
    "audio-only": {"acodec": "aac", "ar": "48000", "ab": "256k", "vn": "1"},
}

_PROJECT = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9._-]+$"}
_PID = {"type": "string", "minLength": 1}


def _ensure_workdir() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)


def _project_path(name: str) -> Path:
    return WORKDIR / f"{name}.mlt"


def _load_project(name: str) -> MltProject:
    p = _project_path(name)
    if not p.exists():
        raise FileNotFoundError(f"Project not found: {p}")
    return MltProject.load(str(p))


def _save_project(proj: MltProject, name: str) -> None:
    proj.save(str(_project_path(name)))


def _ok(payload: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload))]


def _err(msg: str, code: str) -> list[TextContent]:
    return _ok({"error": msg, "code": code})


def probe_duration_s(path: str) -> float | None:
    """Container duration in seconds via ffprobe, or None when unavailable."""
    if not shutil.which(FFPROBE_BIN) and not Path(FFPROBE_BIN).exists():
        return None
    try:
        out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


TOOLS = [
    Tool(
        name="create_project",
        description="Create a new empty MLT/Shotcut project. Choose width/height to match the delivery (e.g. 1080x1920 for vertical).",
        inputSchema={
            "type": "object",
            "properties": {
                "name": _PROJECT,
                "width": {"type": "integer", "default": 1920},
                "height": {"type": "integer", "default": 1080},
                "fps": {"type": "number", "default": 25.0},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="add_clip",
        description="Append a span of a source video to the end of the timeline. in_s/out_s are seconds in the SOURCE file. Returns the clip's producer_id.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": _PROJECT,
                "clip_path": {"type": "string", "minLength": 1},
                "in_s": {"type": "number", "default": 0.0},
                "out_s": {"type": "number"},
            },
            "required": ["project_name", "clip_path"],
        },
    ),
    Tool(
        name="trim_clip",
        description="Change the source in/out points of a clip already on the timeline.",
        inputSchema={
            "type": "object",
            "properties": {"project_name": _PROJECT, "producer_id": _PID,
                           "in_s": {"type": "number"}, "out_s": {"type": "number"}},
            "required": ["project_name", "producer_id"],
        },
    ),
    Tool(
        name="remove_clip",
        description="Ripple-delete one clip from the timeline.",
        inputSchema={
            "type": "object",
            "properties": {"project_name": _PROJECT, "producer_id": _PID},
            "required": ["project_name", "producer_id"],
        },
    ),
    Tool(
        name="move_clip",
        description="Move a clip to a new 1-based position on the timeline.",
        inputSchema={
            "type": "object",
            "properties": {"project_name": _PROJECT, "producer_id": _PID,
                           "to_position": {"type": "integer", "minimum": 1}},
            "required": ["project_name", "producer_id", "to_position"],
        },
    ),
    Tool(
        name="split_clip",
        description="Split a clip at_s seconds after its own in point.",
        inputSchema={
            "type": "object",
            "properties": {"project_name": _PROJECT, "producer_id": _PID, "at_s": {"type": "number"}},
            "required": ["project_name", "producer_id", "at_s"],
        },
    ),
    Tool(
        name="remove_segment",
        description="Ripple-delete the timeline span between start_s and end_s.",
        inputSchema={
            "type": "object",
            "properties": {"project_name": _PROJECT, "start_s": {"type": "number"}, "end_s": {"type": "number"}},
            "required": ["project_name", "start_s", "end_s"],
        },
    ),
    Tool(
        name="append_text_overlay",
        description="Burn a text title onto the timeline between start_s and end_s (timeline seconds).",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": _PROJECT,
                "text": {"type": "string", "minLength": 1, "maxLength": 256},
                "start_s": {"type": "number"},
                "end_s": {"type": "number"},
                "font_size": {"type": "integer", "default": 48},
                "color": {"type": "string", "default": "#ffffff"},
                "bg_color": {"type": "string", "default": "#80000000"},
                "halign": {"type": "string", "enum": ["left", "center", "right"], "default": "center"},
                "valign": {"type": "string", "enum": ["top", "middle", "bottom"], "default": "bottom"},
            },
            "required": ["project_name", "text", "start_s", "end_s"],
        },
    ),
    Tool(
        name="apply_filter",
        description=(
            f"Apply a filter to a clip. Allowed: {sorted(ALLOWED_FILTERS)}. "
            "Fades take params {\"duration_s\": n}; brightness takes {\"level\": 0-2}; "
            "volume takes {\"level\": dB}; crop takes {\"left\",\"right\",\"top\",\"bottom\"} in pixels."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": _PROJECT,
                "producer_id": _PID,
                "filter_name": {"type": "string", "enum": sorted(ALLOWED_FILTERS)},
                "params": {"type": "object", "default": {}},
            },
            "required": ["project_name", "producer_id", "filter_name"],
        },
    ),
    Tool(
        name="set_transition",
        description="Crossfade (video dissolve + audio mix) between two ADJACENT clips. Shortens the timeline by duration_s.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": _PROJECT,
                "from_producer_id": _PID,
                "to_producer_id": _PID,
                "duration_s": {"type": "number", "exclusiveMinimum": 0},
                "style": {"type": "string", "default": "luma"},
            },
            "required": ["project_name", "from_producer_id", "to_producer_id", "duration_s"],
        },
    ),
    Tool(
        name="get_timeline",
        description="Return the whole edit: every clip with producer_id, source in/out, timeline start/end, filters, transitions and titles.",
        inputSchema={"type": "object", "properties": {"project_name": _PROJECT}, "required": ["project_name"]},
    ),
    Tool(
        name="project_info",
        description="Return summary metadata about an existing project.",
        inputSchema={"type": "object", "properties": {"project_name": _PROJECT}, "required": ["project_name"]},
    ),
    Tool(
        name="render",
        description="Render a project to a video file with melt. 'mp4-project' keeps the project's own frame size.",
        inputSchema={
            "type": "object",
            "properties": {
                "project_name": _PROJECT,
                "output_path": {"type": "string", "minLength": 1},
                "preset": {"type": "string", "enum": list(RENDER_PRESETS.keys()), "default": "mp4-1080p"},
            },
            "required": ["project_name", "output_path"],
        },
    ),
    Tool(
        name="list_projects",
        description="List all .mlt project files in the work directory.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_workdir()
    handlers = {
        "create_project": (CreateProjectInput, _create_project),
        "add_clip": (AddClipInput, _add_clip),
        "trim_clip": (TrimClipInput, _trim_clip),
        "remove_clip": (RemoveClipInput, _remove_clip),
        "move_clip": (MoveClipInput, _move_clip),
        "split_clip": (SplitClipInput, _split_clip),
        "remove_segment": (RemoveSegmentInput, _remove_segment),
        "append_text_overlay": (AppendTextOverlayInput, _append_text_overlay),
        "apply_filter": (ApplyFilterInput, _apply_filter),
        "set_transition": (SetTransitionInput, _set_transition),
        "get_timeline": (GetTimelineInput, _get_timeline),
        "project_info": (ProjectInfoInput, _project_info),
        "render": (RenderInput, _render),
        "list_projects": (ListProjectsInput, _list_projects),
    }
    if name not in handlers:
        return _err(f"Unknown tool: {name}", "unknown_tool")
    model, handler = handlers[name]
    try:
        return await handler(model(**(arguments or {})))
    except FileNotFoundError as e:
        return _err(str(e), "project_not_found")
    except KeyError as e:
        return _err(f"Producer not found: {e.args[0]}", "producer_not_found")
    except ValueError as e:
        return _err(str(e), "validation_error")
    except Exception as e:  # noqa: BLE001 - surface anything else to the agent as data
        return _err(str(e), "internal_error")


async def _create_project(inp: CreateProjectInput) -> list[TextContent]:
    proj = MltProject(name=inp.name, width=inp.width, height=inp.height, fps=inp.fps)
    _save_project(proj, inp.name)
    return _ok({"project_path": str(_project_path(inp.name)), "name": inp.name,
                "width": inp.width, "height": inp.height, "fps": inp.fps})


async def _add_clip(inp: AddClipInput) -> list[TextContent]:
    if not Path(inp.clip_path).exists():
        return _err(f"Clip file not found: {inp.clip_path}", "clip_not_found")
    source_duration = probe_duration_s(inp.clip_path)
    out_s = inp.out_s
    if source_duration is not None:
        if inp.in_s >= source_duration:
            return _err(f"in_s {inp.in_s} is past the end of the source ({source_duration:.3f}s)", "validation_error")
        if out_s is None or out_s > source_duration:
            out_s = source_duration if out_s is None else min(out_s, source_duration)
    proj = _load_project(inp.project_name)
    timeline_pos = proj.project_duration_s()
    producer_id = proj.add_clip(path=inp.clip_path, in_s=inp.in_s, out_s=out_s)
    _save_project(proj, inp.project_name)
    return _ok({"producer_id": producer_id, "clip_path": inp.clip_path, "in_s": inp.in_s,
                "out_s": out_s, "timeline_position_s": round(timeline_pos, 3),
                "source_duration_s": source_duration})


async def _trim_clip(inp: TrimClipInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    proj.trim_clip(inp.producer_id, inp.in_s, inp.out_s)
    _save_project(proj, inp.project_name)
    return _ok({"producer_id": inp.producer_id, "duration_s": round(proj.project_duration_s(), 3)})


async def _remove_clip(inp: RemoveClipInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    proj.remove_clip(inp.producer_id)
    _save_project(proj, inp.project_name)
    return _ok({"removed": inp.producer_id, "duration_s": round(proj.project_duration_s(), 3)})


async def _move_clip(inp: MoveClipInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    proj.move_clip(inp.producer_id, inp.to_position)
    _save_project(proj, inp.project_name)
    return _ok({"producer_id": inp.producer_id, "position": inp.to_position})


async def _split_clip(inp: SplitClipInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    left_id, right_id = proj.split_clip(inp.producer_id, inp.at_s)
    _save_project(proj, inp.project_name)
    return _ok({"left_producer_id": left_id, "right_producer_id": right_id})


async def _remove_segment(inp: RemoveSegmentInput) -> list[TextContent]:
    if inp.start_s >= inp.end_s:
        return _err("start_s must be less than end_s", "invalid_segment")
    proj = _load_project(inp.project_name)
    proj.remove_segment(inp.start_s, inp.end_s)
    _save_project(proj, inp.project_name)
    return _ok({"removed_duration_s": inp.end_s - inp.start_s})


async def _append_text_overlay(inp: AppendTextOverlayInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    overlay_id = proj.append_text_overlay(
        text=inp.text, start_s=inp.start_s, end_s=inp.end_s, font_size=inp.font_size,
        color=inp.color, bg_color=inp.bg_color, halign=inp.halign, valign=inp.valign,
    )
    _save_project(proj, inp.project_name)
    return _ok({"overlay_id": overlay_id, "text": inp.text, "start_s": inp.start_s, "end_s": inp.end_s})


async def _apply_filter(inp: ApplyFilterInput) -> list[TextContent]:
    if inp.filter_name not in ALLOWED_FILTERS:
        return _err(f"Filter '{inp.filter_name}' not allowed. Allowed: {sorted(ALLOWED_FILTERS)}", "invalid_filter")
    proj = _load_project(inp.project_name)
    proj.apply_filter(inp.producer_id, inp.filter_name, inp.params)
    _save_project(proj, inp.project_name)
    return _ok({"producer_id": inp.producer_id, "filter_name": inp.filter_name, "params": inp.params})


async def _set_transition(inp: SetTransitionInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    proj.set_transition(inp.from_producer_id, inp.to_producer_id, inp.duration_s, inp.style)
    _save_project(proj, inp.project_name)
    return _ok({"from_producer_id": inp.from_producer_id, "to_producer_id": inp.to_producer_id,
                "duration_s": inp.duration_s, "timeline_duration_s": round(proj.project_duration_s(), 3)})


async def _get_timeline(inp: GetTimelineInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    return _ok({"project_path": str(_project_path(inp.project_name)), **proj.timeline()})


async def _project_info(inp: ProjectInfoInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    return _ok({"project_path": str(_project_path(inp.project_name)), "name": proj.name,
                "width": proj.width, "height": proj.height, "fps": proj.fps,
                "duration_s": round(proj.project_duration_s(), 3), "clip_count": len(proj._clips)})


async def _render(inp: RenderInput) -> list[TextContent]:
    if inp.preset not in RENDER_PRESETS:
        return _err(f"Unknown preset '{inp.preset}'. Valid: {list(RENDER_PRESETS.keys())}", "invalid_preset")
    proj = _load_project(inp.project_name)
    if not proj._clips:
        return _err("Project has no clips", "render_failed")
    mlt_path = str(_project_path(inp.project_name))
    consumer_args = [f"{k}={v}" for k, v in RENDER_PRESETS[inp.preset].items()]
    cmd = [MELT_BIN, "-quiet", mlt_path, "-consumer", f"avformat:{inp.output_path}",
           "real_time=-1", "terminate_on_pause=1"] + consumer_args

    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3600)
        except asyncio.TimeoutError:
            proc.kill()
            return _err("melt render timed out after 3600s", "render_failed")
    except FileNotFoundError:
        return _err(f"melt binary not found: {MELT_BIN}", "render_failed")

    elapsed = time.time() - t0
    out_path = Path(inp.output_path)
    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        tail = "\n".join(stderr.decode(errors="replace").splitlines()[-10:])
        return _err(f"melt exited {proc.returncode}:\n{tail}", "render_failed")

    return _ok({"output_path": inp.output_path, "preset": inp.preset, "render_time_s": round(elapsed, 2),
                "file_size_bytes": out_path.stat().st_size,
                "rendered_duration_s": probe_duration_s(inp.output_path),
                "timeline_duration_s": round(proj.project_duration_s(), 3)})


async def _list_projects(inp: ListProjectsInput) -> list[TextContent]:
    _ensure_workdir()
    projects = [{"name": p.stem, "path": str(p), "size_bytes": p.stat().st_size}
                for p in sorted(WORKDIR.glob("*.mlt"))]
    return _ok({"projects": projects})


def main() -> None:
    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
