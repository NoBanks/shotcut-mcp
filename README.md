# shotcut-mcp

MCP server that drives Shotcut/MLT video editing headlessly via the MLT framework. Shotcut projects are MLT XML files (`.mlt`). Render is done via the `melt` CLI. The purpose is programmatic timeline editing so an AI agent can build, cut, filter, and export video in Shotcut's native format. Generated `.mlt` files also open in the Shotcut GUI.

## Requirements

- Python 3.10+
- `melt` (MLT framework command line tool) on `PATH` or pointed to via `MELT_BIN`
- `ffprobe` (optional, for verifying rendered output)

## Installation
