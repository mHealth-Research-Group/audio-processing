# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based audio/video processing tool that provides a comprehensive pipeline for analyzing, merging, and editing media files. The main focus is on speaker detection, voice activity detection, and privacy-preserving video editing.

## Development Commands

### Environment Setup
```bash
# Install dependencies
uv sync

# Install PyTorch with CUDA support (check CUDA version first with `nvcc --version`)
uv add torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

### Running the Application
```bash
# Main entry point
uv run main.py <command> [options]

# Common usage patterns
uv run main.py process /path/to/videos --complete --output final_video.mp4
uv run main.py compress video.mp4 -o compressed.mp4
uv run main.py apply-blank video.mp4 timeline.yaml -o output.mp4
```

### Testing
```bash
# Run the header detection test
uv run python test_header_detection.py

# Run pytest (if tests exist)
uv run pytest
```

### Code Quality
```bash
# Format and lint code (ruff configuration in ruff.toml)
ruff check
ruff format
```

## Project Architecture

### Core Modules (src/)
- **main.py**: CLI entry point with argument parsing
- **commands.py**: High-level command implementations (process, compress, apply-blank)
- **audio_analysis.py**: Speaker detection and voice activity analysis using pyannote-audio
- **video_merger.py**: Merges timestamped videos chronologically with gap filling
- **media_processing.py**: FFmpeg-based audio/video processing operations
- **processing_pipeline.py**: Orchestrates the complete processing workflow
- **batch_processing.py**: Handles large datasets by splitting into batches
- **utils.py**: Shared utilities and file operations
- **file_operations.py**: File naming conventions and path generation
- **merge_operations.py**: Video merging logic and timestamped file detection

### Key Features
1. **Timestamped Video Merging**: Automatically merges videos with format `YYYYMMDDHHMMSS_*.ext`
2. **Speaker Detection**: Uses Hugging Face pyannote-audio models for voice activity detection
3. **Timeline Generation**: Creates YAML timelines for manual editing of speech segments
4. **Batch Processing**: Automatically splits large datasets (>60 videos) into manageable batches
5. **Privacy Editing**: Applies timeline edits to mute/blank specific segments

### Dependencies
- **FFmpeg**: Required for all media processing operations
- **pyannote-audio**: AI model for speaker detection (requires Hugging Face token)
- **librosa**: Audio analysis and feature extraction
- **uv**: Package manager (replaces pip/pipenv)

### Environment Requirements
- Python 3.11+
- NVIDIA GPU with CUDA support (optional, for acceleration)
- Hugging Face access token (set in .env file)

### File Naming Conventions
- Input videos: `YYYYMMDDHHMMSS_*.ext` for automatic chronological merging
- Output files: `YYYYMMDD_processed.mp4`, `YYYYMMDD_compressed.mp4`, etc.
- Timeline files: `*_timeline.yaml`

### Configuration Files
- **pyproject.toml**: Project dependencies and metadata
- **ruff.toml**: Code formatting rules (line length: 120)
- **.env**: Environment variables (Hugging Face token)
- **blank_muted.MP4**: Default blank video for gap filling

## Performance Optimizations

### Incremental Processing for Apply-Blank
The `apply-blank` command now uses intelligent incremental processing to dramatically improve performance:

**Key Features:**
- **Timeline Comparison**: Compares original vs modified timelines to detect changes
- **Segment-Level Processing**: Only processes segments that actually changed (type changed to 'all')
- **Temporal Batching**: Splits large operations into time-based chunks to prevent FFmpeg filter explosion
- **Automatic Caching**: Caches original timeline during `--complete` processing for future comparisons

**Performance Benefits:**
- **Massive speedup**: Only process what actually changed instead of entire video
- **Batch processing**: Prevents "FFmpeg filter explosion" that causes exponential slowdown
- **Smart detection**: Automatically detects which segments need reprocessing

**Usage:**
```bash
# Incremental processing (default)
uv run main.py apply-blank video.mp4 timeline.yaml -o output.mp4

# Disable incremental processing (legacy method)
uv run main.py apply-blank video.mp4 timeline.yaml -o output.mp4 --no-incremental

# Adjust batch size for temporal batching
uv run main.py apply-blank video.mp4 timeline.yaml -o output.mp4 --batch-duration 15
```

**Timeline Caching:**
- Original timeline automatically cached during `main.py process --complete`
- Cache stored in `.timeline_cache/` directory
- Enables fast incremental processing for subsequent `apply-blank` operations