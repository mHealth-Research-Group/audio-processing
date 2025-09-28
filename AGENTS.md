# Repository Guidelines

## Project Structure & Module Organization
- Core pipeline in `src/`; `src/processing_pipeline.py` orchestrates audio/video stages.
- Pure helpers: `src/audio_analysis.py`, `src/media_processing.py`, `src/file_operations.py`.
- CLI: root `main.py` (entry), `src/commands.py` (subcommands/flags).
- Reference media, fixtures, and incremental outputs live under `data/` and `test_incremental_*`.
- Standalone regression scripts (e.g., rename, dedupe) sit next to `README.md`.

## Build, Test, and Development Commands
- `uv sync` — install Python ≥3.11 deps from `pyproject.toml`.
- `uv run main.py process <video_dir> --complete` — run full processing pipeline over a directory.
- `uv run python src/main.py --help` — quick smoke check of CLI wiring.
- `uv run pytest [-k pattern]` — run tests; use `-k` to focus (e.g., `-k audio_analysis`).
- `uv run ruff check src tests` — lint; enforces 120‑char line limit.

## Coding Style & Naming Conventions
- PEP 8 with four‑space indentation; descriptive `snake_case` for modules, functions, and variables.
- Type all public functions; update imports from `typing` when signatures change.
- Keep helpers pure; side effects belong in orchestration layers (`processing_pipeline.py`, CLI commands).
- Prefer small, focused functions and explicit parameters over globals.

## Testing Guidelines
- Framework: `pytest` with fixture helpers. Name files `tests/test_<feature>.py`.
- Group assertions per scenario; keep tests deterministic and fast.
- Validate media timelines against YAML artifacts or merge lists; use lightweight audio/video stubs.
- Run selectively during development: `uv run pytest -k merge`.

## Commit & Pull Request Guidelines
- Commits: imperative subjects (e.g., "Handle long concat files"); each commit should pass tests and lint.
- PRs: short summary, numbered change list, setup notes (e.g., `.env` tokens, model downloads), and links to issues/sample outputs.
- Note GPU/CUDA assumptions when modifying accelerated stages.

## Environment & Media Notes
- Ensure FFmpeg is on `PATH` (`ffmpeg -version`); store Hugging Face tokens in `.env`.
- Keep large renders under `tmp/` or ignored paths; clean before pushing unless reviewers request artifacts.
- Document external tools or models added, including versions and download steps.

