# shotcut-mcp

<!-- mcp-name: io.github.NoBanks/shotcut-mcp -->

MCP server that edits video through Shotcut's engine, MLT, with no GUI. An agent can create a project, add and trim clips, split, move and remove them, apply filters, add titles and crossfades, read the timeline back and render it with `melt`.

The output is a native Shotcut `.mlt` project, not a locked video file. Open the agent's cut in Shotcut and keep editing it by hand.

## Requirements

- Python 3.11+
- [MLT](https://www.mltframework.org/) `melt` on `PATH`, or set `MELT_BIN` to its full path. Installing [Shotcut](https://shotcut.org/) includes `melt`.
  - Linux: `sudo apt install melt`
  - macOS: install Shotcut, then set `MELT_BIN=/Applications/Shotcut.app/Contents/MacOS/melt`
  - Windows: install Shotcut, then set `MELT_BIN` to `melt.exe` inside the Shotcut folder. Windows rendering is not yet covered by CI.
- `ffprobe` (from FFmpeg) on `PATH` for reading clip durations and checking renders. Override with `FFPROBE_BIN`.

## Install

Run it with no install step using [uv](https://docs.astral.sh/uv/):

```bash
uvx shotcut-mcp
```

Or install it with pip:

```bash
pip install shotcut-mcp
shotcut-mcp
```

The server talks MCP over stdio.

## Use it with goose

### goose Desktop or CLI

1. Run `goose configure`, choose **Add Extension**, then **Command-line Extension**.
2. Name: `shotcut`
3. Command: `uvx shotcut-mcp`
4. Timeout: `600` seconds or more. Renders take longer than goose's default.
5. Add the environment variables `SHOTCUT_MCP_WORKDIR` and, if `melt` is not on your `PATH`, `MELT_BIN`.

### config.yaml

Add this to `~/.config/goose/config.yaml`:

```yaml
extensions:
  shotcut:
    enabled: true
    type: stdio
    name: shotcut
    cmd: uvx
    args:
      - shotcut-mcp
    envs:
      SHOTCUT_MCP_WORKDIR: /path/to/your/projects
    timeout: 600
```

Then ask goose something like: *"Make a project called promo from clip1.mp4 and clip2.mp4, keep the first 5 seconds of each, crossfade them, add the title 'Launch day' and render it."*

## Use it with other MCP clients

Any MCP client that runs stdio servers works. For example, Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "shotcut": {
      "command": "uvx",
      "args": ["shotcut-mcp"],
      "env": { "SHOTCUT_MCP_WORKDIR": "/path/to/your/projects" }
    }
  }
}
```

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `SHOTCUT_MCP_WORKDIR` | `~/shotcut-mcp-projects` | Where `.mlt` projects and their state files are saved |
| `MELT_BIN` | `melt` | Path to the `melt` binary used for rendering |
| `FFPROBE_BIN` | `ffprobe` | Path to `ffprobe`, used to read source durations |

## Tools

| Tool | What it does |
|---|---|
| `create_project` | Create a new project with a frame size and frame rate |
| `add_clip` | Append a span of a source video to the end of the timeline. Returns the clip's id |
| `trim_clip` | Change a clip's source in and out points |
| `remove_clip` | Ripple-delete one clip |
| `move_clip` | Move a clip to a new position on the timeline |
| `split_clip` | Split a clip into two at a point inside it |
| `remove_segment` | Ripple-delete a span of the timeline |
| `append_text_overlay` | Burn a text title onto the timeline between two times |
| `apply_filter` | Apply `brightness`, `volume`, `crop`, `fadeInBrightness`, `fadeOutBrightness`, `fadeInAudio` or `fadeOutAudio` to a clip |
| `set_transition` | Crossfade video and audio between two adjacent clips |
| `get_timeline` | Return the whole edit: clips, source in and out, timeline positions, filters, transitions and titles |
| `project_info` | Return summary metadata for a project |
| `render` | Render a project to a video file with `melt` |
| `list_projects` | List the projects in the work directory |

Clip ids stay stable across calls. The edit state is kept in a `.state.json` file next to each `.mlt`, and the `.mlt` is regenerated from it after every change.

## Current limits

- One video track. Multitrack video and audio is planned.
- Edits made by hand in the Shotcut GUI are overwritten by the next tool call. Safe round trips are planned.
- The `style` option on `set_transition` is accepted but only a dissolve is rendered today.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The render tests are skipped when `melt` is not installed.

## License

MIT
