"""Shotcut MCP Server - programmatic MLT/Shotcut video editing via melt."""

import json
import os
import subprocess
import asyncio
from pathlib import Path
from typing import Any

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
    ListProjectsInput,
    ProjectInfoInput,
    RemoveSegmentInput,
    RenderInput,
    SetTransitionInput,
    SplitClipInput,
)

APP_NAME = "shotcut-mcp"
server = Server(APP_NAME)

WORKDIR = Path(os.environ.get("SHOTCUT_MCP_WORKDIR", "~/shotcut-mcp-projects")).expanduser()
MELT_BIN = os.environ.get("MELT_BIN", "melt")

RENDER_PRESETS = {
    "mp4-1080p": {
        "vcodec": "libx264",
        "s": "1920x1080",
        "acodec": "aac",
        "ar": "48000",
        "ab": "256k",
    },
    "mp4-720p": {
        "vcodec": "libx264",
        "s": "1280x720",
        "acodec": "aac",
        "ar": "48000",
        "ab": "256k",
    },
    "audio-only": {
        "acodec": "aac",
        "ar": "48000",
        "ab": "256k",
        "vn": "1",
    },
}


def _ensure_workdir() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)


def _project_path(name: str) -> Path:
    return WORKDIR / f"{name}.mlt"


def _load_project(name: str) -> MltProject:
    p = _project_path(name)
    if not p.exists():
        raise FileNotFoundError(f"Project not found: {p}")
    return MltProject.load(str(p))


def _save_project(proj: MltProject) -> None:
    proj.save(str(_project_path(proj.name)))


def _err(msg: str, code: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg, "code": code}))]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_project",
            description="Create a new empty MLT/Shotcut project file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "width": {"type": "integer", "default": 1920},
                    "height": {"type": "integer", "default": 1080},
                    "fps": {"type": "number", "default": 25.0},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="add_clip",
            description="Append a video/audio clip to the project timeline.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "clip_path": {"type": "string", "minLength": 1},
                    "in_s": {"type": "number", "default": 0.0},
                    "out_s": {"type": "number"},
                },
                "required": ["project_name", "clip_path"],
            },
        ),
        Tool(
            name="split_clip",
            description="Split an existing clip at a timeline offset.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "producer_id": {"type": "string", "minLength": 1},
                    "at_s": {"type": "number"},
                },
                "required": ["project_name", "producer_id", "at_s"],
            },
        ),
        Tool(
            name="remove_segment",
            description="Ripple-delete a timeline segment between start_s and end_s.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                },
                "required": ["project_name", "start_s", "end_s"],
            },
        ),
        Tool(
            name="append_text_overlay",
            description="Burn a text overlay onto the timeline between start_s and end_s.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "text": {"type": "string", "minLength": 1, "maxLength": 256},
                    "start_s": {"type": "number"},
                    "end_s": {"type": "number"},
                    "font_size": {"type": "integer", "default": 48},
                    "color": {"type": "string", "default": "#ffffff"},
                    "bg_color": {"type": "string", "default": "#80000000"},
                    "halign": {"type": "string", "default": "center"},
                    "valign": {"type": "string", "default": "bottom"},
                },
                "required": ["project_name", "text", "start_s", "end_s"],
            },
        ),
        Tool(
            name="apply_filter",
            description=f"Apply a filter to a clip. Allowed: {sorted(ALLOWED_FILTERS)}",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "producer_id": {"type": "string", "minLength": 1},
                    "filter_name": {"type": "string"},
                    "params": {"type": "object", "default": {}},
                },
                "required": ["project_name", "producer_id", "filter_name"],
            },
        ),
        Tool(
            name="set_transition",
            description="Add a luma crossfade transition between two adjacent clips.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "from_producer_id": {"type": "string", "minLength": 1},
                    "to_producer_id": {"type": "string", "minLength": 1},
                    "duration_s": {"type": "number"},
                    "style": {"type": "string", "default": "luma"},
                },
                "required": ["project_name", "from_producer_id", "to_producer_id", "duration_s"],
            },
        ),
        Tool(
            name="project_info",
            description="Return metadata about an existing project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                },
                "required": ["project_name"],
            },
        ),
        Tool(
            name="render",
            description="Render a project to an output video file using melt.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "output_path": {"type": "string", "minLength": 1},
                    "preset": {
                        "type": "string",
                        "enum": list(RENDER_PRESETS.keys()),
                        "default": "mp4-1080p",
                    },
                },
                "required": ["project_name", "output_path"],
            },
        ),
        Tool(
            name="list_projects",
            description="List all .mlt project files in the work directory.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_workdir()
    try:
        if name == "create_project":
            return await _create_project(CreateProjectInput(**arguments))
        elif name == "add_clip":
            return await _add_clip(AddClipInput(**arguments))
        elif name == "split_clip":
            return await _split_clip(SplitClipInput(**arguments))
        elif name == "remove_segment":
            return await _remove_segment(RemoveSegmentInput(**arguments))
        elif name == "append_text_overlay":
            return await _append_text_overlay(AppendTextOverlayInput(**arguments))
        elif name == "apply_filter":
            return await _apply_filter(ApplyFilterInput(**arguments))
        elif name == "set_transition":
            return await _set_transition(SetTransitionInput(**arguments))
        elif name == "project_info":
            return await _project_info(ProjectInfoInput(**arguments))
        elif name == "render":
            return await _render(RenderInput(**arguments))
        elif name == "list_projects":
            return await _list_projects(ListProjectsInput(**arguments))
        else:
            return _err(f"Unknown tool: {name}", "unknown_tool")
    except FileNotFoundError as e:
        return _err(str(e), "project_not_found")
    except ValueError as e:
        return _err(str(e), "validation_error")
    except Exception as e:
        return _err(str(e), "internal_error")


async def _create_project(inp: CreateProjectInput) -> list[TextContent]:
    proj = MltProject(name=inp.name, width=inp.width, height=inp.height, fps=inp.fps)
    _save_project(proj)
    path = str(_project_path(inp.name))
    return [TextContent(type="text", text=json.dumps({
        "project_path": path,
        "name": inp.name,
        "width": inp.width,
        "height": inp.height,
        "fps": inp.fps,
    }))]


async def _add_clip(inp: AddClipInput) -> list[TextContent]:
    clip_path = Path(inp.clip_path)
    if not clip_path.exists():
        return _err(f"Clip file not found: {inp.clip_path}", "clip_not_found")
    proj = _load_project(inp.project_name)
    timeline_pos = proj.project_duration_s()
    producer_id = proj.add_clip(
        path=inp.clip_path,
        in_s=inp.in_s,
        out_s=inp.out_s,
    )
    _save_project(proj)
    out_s = inp.out_s if inp.out_s is not None else (proj.project_duration_s() - timeline_pos + inp.in_s)
    return [TextContent(type="text", text=json.dumps({
        "producer_id": producer_id,
        "clip_path": inp.clip_path,
        "in_s": inp.in_s,
        "out_s": out_s,
        "timeline_position_s": timeline_pos,
    }))]


async def _split_clip(inp: SplitClipInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    try:
        left_id, right_id = proj.split_clip(inp.producer_id, inp.at_s)
    except KeyError:
        return _err(f"Producer not found: {inp.producer_id}", "producer_not_found")
    _save_project(proj)
    return [TextContent(type="text", text=json.dumps({
        "left_producer_id": left_id,
        "right_producer_id": right_id,
    }))]


async def _remove_segment(inp: RemoveSegmentInput) -> list[TextContent]:
    if inp.start_s >= inp.end_s:
        return _err("start_s must be less than end_s", "invalid_segment")
    proj = _load_project(inp.project_name)
    removed = inp.end_s - inp.start_s
    proj.remove_segment(inp.start_s, inp.end_s)
    _save_project(proj)
    return [TextContent(type="text", text=json.dumps({"removed_duration_s": removed}))]


async def _append_text_overlay(inp: AppendTextOverlayInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    proj.append_text_overlay(
        text=inp.text,
        start_s=inp.start_s,
        end_s=inp.end_s,
        font_size=inp.font_size,
        color=inp.color,
        bg_color=inp.bg_color,
        halign=inp.halign,
        valign=inp.valign,
    )
    _save_project(proj)
    return [TextContent(type="text", text=json.dumps({
        "text": inp.text,
        "start_s": inp.start_s,
        "end_s": inp.end_s,
    }))]


async def _apply_filter(inp: ApplyFilterInput) -> list[TextContent]:
    if inp.filter_name not in ALLOWED_FILTERS:
        return _err(
            f"Filter '{inp.filter_name}' not allowed. Allowed: {sorted(ALLOWED_FILTERS)}",
            "invalid_filter",
        )
    proj = _load_project(inp.project_name)
    try:
        proj.apply_filter(inp.producer_id, inp.filter_name, inp.params)
    except KeyError:
        return _err(f"Producer not found: {inp.producer_id}", "producer_not_found")
    _save_project(proj)
    return [TextContent(type="text", text=json.dumps({
        "producer_id": inp.producer_id,
        "filter_name": inp.filter_name,
        "params": inp.params,
    }))]


async def _set_transition(inp: SetTransitionInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    try:
        proj.set_transition(
            from_producer_id=inp.from_producer_id,
            to_producer_id=inp.to_producer_id,
            duration_s=inp.duration_s,
            style=inp.style,
        )
    except KeyError as e:
        return _err(f"Producer not found: {e}", "producer_not_found")
    _save_project(proj)
    return [TextContent(type="text", text=json.dumps({
        "from_producer_id": inp.from_producer_id,
        "to_producer_id": inp.to_producer_id,
        "duration_s": inp.duration_s,
    }))]


async def _project_info(inp: ProjectInfoInput) -> list[TextContent]:
    proj = _load_project(inp.project_name)
    path = str(_project_path(inp.project_name))
    return [TextContent(type="text", text=json.dumps({
        "project_path": path,
        "name": proj.name,
        "width": proj.width,
        "height": proj.height,
        "fps": proj.fps,
        "duration_s": proj.project_duration_s(),
        "clip_count": len(proj._clips),
    }))]


async def _render(inp: RenderInput) -> list[TextContent]:
    if inp.preset not in RENDER_PRESETS:
        return _err(
            f"Unknown preset '{inp.preset}'. Valid: {list(RENDER_PRESETS.keys())}",
            "invalid_preset",
        )
    proj = _load_project(inp.project_name)
    mlt_path = str(_project_path(inp.project_name))
    preset_args = RENDER_PRESETS[inp.preset]

    consumer_args = [f"{k}={v}" for k, v in preset_args.items()]
    cmd = [MELT_BIN, mlt_path, "-consumer", f"avformat:{inp.output_path}"] + consumer_args

    import time
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        return _err("melt render timed out after 3600s", "render_failed")
    except FileNotFoundError:
        return _err(f"melt binary not found: {MELT_BIN}", "render_failed")

    elapsed = time.time() - t0

    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.splitlines()[-10:])
        return _err(
            f"melt exited {result.returncode}:\n{stderr_tail}",
            "render_failed",
        )

    out_path = Path(inp.output_path)
    file_size = out_path.stat().st_size if out_path.exists() else 0

    return [TextContent(type="text", text=json.dumps({
        "output_path": inp.output_path,
        "preset": inp.preset,
        "duration_s": round(elapsed, 2),
        "file_size_bytes": file_size,
    }))]


async def _list_projects(inp: ListProjectsInput) -> list[TextContent]:
    _ensure_workdir()
    projects = []
    for p in sorted(WORKDIR.glob("*.mlt")):
        projects.append({
            "name": p.stem,
            "path": str(p),
            "size_bytes": p.stat().st_size,
        })
    return [TextContent(type="text", text=json.dumps({"projects": projects}))]


def main() -> None:
    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
