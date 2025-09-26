# Repository Guidelines

## Project Structure & Module Organization
- Core pipeline code lives in `src/`; `processing_pipeline.py` orchestrates audio and video stages while helpers (`audio_analysis.py`, `media_processing.py`, `file_operations.py`) stay pure.
- Command-line entry points sit at the repo root: `main.py` dispatches CLI invocations, and `src/commands.py` hosts subcommand logic.
- Reference media, fixtures, and incremental outputs are under `data/` and `test_incremental_*`; standalone regression scripts (rename, dedupe) remain alongside `README.md`.

## Build, Test, and Development Commands
- `uv sync`: install Python ≥3.11 dependencies declared in `pyproject.toml`.
- `uv run main.py process <video_dir> --complete`: execute the full processing pipeline over a directory of videos.
- `uv run python src/main.py --help`: quick smoke check confirming CLI wiring and available flags.
- `uv run pytest [-k pattern]`: run the automated suite, optionally focusing on targeted modules.
- `uv run ruff check src tests`: lint and enforce the 120-char rule before opening a PR.

## Coding Style & Naming Conventions
- Follow PEP 8 with four-space indentation and descriptive snake_case names for functions, variables, and modules.
- Keep public functions typed; import from `typing` when signatures change.
- Reserve side effects for orchestration layers, keeping helpers pure and testable.

## Testing Guidelines
- Tests use `pytest` with fixture helpers; name new files `test_<feature>.py` and group assertions per scenario.
- Validate media timelines against YAML artifacts or merge lists; prefer lightweight audio/video stubs over large assets.

## Commit & Pull Request Guidelines
- Write commit subjects in imperative mood (e.g., "Handle long concat files"); each commit should pass tests.
- PRs need a short summary, numbered change list, setup notes (e.g., `.env` tokens, model downloads), and links to issues or sample outputs when relevant.

## Environment & Media Notes
- Ensure FFmpeg is available on PATH and store Hugging Face tokens in `.env`.
- Keep large renders in `tmp/` or ignored paths and clean them before pushing unless reviewers request otherwise.
- Document GPU or CUDA assumptions in PRs when modifying accelerated stages.
