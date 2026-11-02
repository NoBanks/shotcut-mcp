# shotcut-mcp: Sentient Foundation Open Source AGI Grant

## 1. One line

Open-source tools that let AI agents edit real video projects.

## 2. Problem and why now

AI agents cannot operate desktop video editors. They cannot click through a timeline, so video, the most common medium on the internet, stays out of reach of agent workflows. shotcut-mcp is an MCP server that gives any MCP-capable agent Shotcut's engine, MLT, with no GUI (`README.md`). MCP clients such as goose and Claude Desktop already run stdio servers, so the agent side exists. The editing side did not.

## 3. What exists today

- 14 tools, listed in the README Tools table and defined in `shotcut_mcp/server.py` and `shotcut_mcp/schemas.py`: create projects, add, trim, split, move and remove clips, ripple-delete a span, add titles, apply filters, set crossfades, read the timeline back, render, list projects.
- Output is a native Shotcut `.mlt` project, generated in `shotcut_mcp/mlt_xml.py`, so a human can open the agent's cut in Shotcut.
- Stable clip ids and persisted state in a `.state.json` beside each `.mlt` (README, "Tools").
- Real renders with `melt`, checked with `ffprobe`.
- 34 test functions in `tests/`; CI runs on Linux, macOS and Windows (`.github/workflows/ci.yml`).
- Published to PyPI and the MCP Registry as version 0.2.1 (`pyproject.toml`, `server.json`). Setup docs for goose and Claude Desktop are in the README.
- 4 commits in git history so far.

## 4. Who it helps

Developers building agent workflows that touch video. Creators who want an agent to assemble a first cut they then finish by hand. Anyone running a local agent such as goose who wants video editing without a cloud service. Usage numbers: [NEED: PyPI download count].

## 5. What is open and who loses if it closes

All of it: the server, the project generator, the tests and the release workflow are MIT licensed (`LICENSE`, "Copyright (c) 2026 Ryan Hammer (NoBanks Nearby)"). The output format is Shotcut's own open `.mlt`, so work is never locked in. If this closed, agent builders would lose a free way to edit video, and creators would lose the ability to take an agent's edit into an open editor and keep working.

## 6. Who is building it and why him

Winner, XPRIZE Build with Gemini ($50,000, Entrepreneurship & Job Creation track, Sept 2026). Winner, Turing Test Hackathon 2026 on Mantle (TRAIDE, 1,000 USDT). Both won solo, within months of starting to build with AI agents.

Ryan Hammer is a solo builder with 30 years as a professional cinematographer, so he knows what an editor needs from a timeline. He is also the solo musician NoBanks Nearby, with 600+ released tracks. He began building with AI agents in 2026 and has built 58 MCP servers.

## 7. What $10k unlocks over 3 months

1. [NEED: Ryan's 3-month shipping list] Suggested: multitrack video and audio. The README lists "One video track" as a current limit.
2. [NEED: Ryan's 3-month shipping list] Suggested: safe round trips, so edits made by hand in the Shotcut GUI are not overwritten by the next tool call (README, "Current limits").
3. [NEED: Ryan's 3-month shipping list] Suggested: render more than a dissolve in `set_transition`; the `style` option is accepted but only a dissolve is rendered today (README, "Current limits").

## 8. Links

- Repo: https://github.com/NoBanks/shotcut-mcp
- PyPI package: `shotcut-mcp` (`pip install shotcut-mcp` or `uvx shotcut-mcp`, per README)
- MCP Registry name: `io.github.NoBanks/shotcut-mcp` (`server.json`)
- Demo video: [NEED: demo video URL]
