# Audio/Video Processing Tool

An AI-driven tool for processing audio and video files with automatic speaker detection, video merging, and intelligent gap filling. It is ideal for processing security footage, meeting recordings, and other timestamped video sequences.

## Quick Start

### Complete Processing

For most use cases, the `--complete` flag provides a comprehensive processing pipeline that merges timestamped videos, fills gaps, analyzes speakers, and generates a timeline.

```bash
# Process a directory of timestamped videos with the complete pipeline
uv run main.py process /path/to/videos --complete --output final_video.mp4
```

This command performs the following actions:
1. **Merges** all timestamped videos in chronological order.
2. **Fills gaps** between videos with a blank video (using the default `blank_muted.MP4`).
3. **Analyzes speakers** to detect conversations.
4. **Generates a timeline** (`.yaml`) with detected speech segments.
5. **Creates a processed video** with conversations muted.

**Note**: Video merging is automatic for directories containing timestamped videos. The tool uses `blank_muted.MP4` as the default blank video file for gap filling.

## Installation

### Prerequisites

- **FFmpeg**: Required for all media processing tasks.
- **Hugging Face Account**: An access token is needed to download the speaker detection model.

### GPU and CUDA

For GPU acceleration, you need a compatible NVIDIA GPU with the appropriate CUDA Toolkit installed. It is critical that your PyTorch version matches your CUDA version.

- **Check your CUDA version**:
  ```bash
  nvcc --version
  ```
- **Install the correct PyTorch version**:
  Visit the [PyTorch website](https://pytorch.org/get-started/locally/) to find the correct installation command for your specific CUDA version. For example, for CUDA 12.8, the command is:
  ```bash
  uv add torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  ```

### Setup

1. **Clone the repository and install dependencies:**
   ```bash
   git clone <repository-url>
   cd audio-processing
   uv sync
   ```

2. **Configure your Hugging Face token:**
   Create a `.env` file in the project root and add your token:
   ```
   HUGGINGFACE_ACCESS_TOKEN=your_token_here
   ```

## Usage Guide

### Processing a Directory

The `process` command is the main entry point for all processing tasks. It can handle both single files and directories.

```bash
# Basic processing of a directory (automatically merges timestamped videos)
uv run main.py process /path/to/videos --output-dir /path/to/output

# Merge-only: merge videos without speech analysis
uv run main.py process /path/to/videos --merge-only --output merged_video.mp4

# Full analysis with timeline generation
uv run main.py process /path/to/videos --generate-timeline --analyze-speakers
```

### Single File Processing

The tool can also process individual audio or video files.

```bash
# Process a single file and generate a timeline
uv run main.py process recording.mp4 --generate-timeline

# Analyze speakers in a file without generating output
uv run main.py process meeting.mp4 --analyze-speakers --speaker-analysis-only

# Use advanced speaker analysis for higher accuracy
uv run main.py process noisy_audio.mp4 --detailed-analysis --generate-timeline
```

### Applying Edits from a Timeline

You can manually edit a generated timeline file and then apply those changes to the video.

**1. Generate a timeline:**
```bash
uv run main.py process video.mp4 --generate-timeline
```

**2. Edit the timeline:**
Open the generated `_timeline.yaml` file and modify the `"type"` of any segment you want to change. For example, to replace a segment with a blank video, change its `"type"` to `"all"`.

**3. Apply the changes:**
Use the `apply-blank` command to replace the marked segments with a blank video.
```bash
uv run main.py apply-blank video.mp4 video_timeline.yaml -o final_video.mp4
```

The command uses `blank_muted.MP4` as the default blank video file. You can specify a different blank video with `--blank-video path/to/blank.mp4`.

**Note:** After processing, segments that were marked as `"type": "all"` will automatically have their `"label"` updated to `"removed"` in the timeline file, making it easy to track which segments have been processed.

## File Naming Convention

For automatic video merging, your files must be named using a timestamp format:

**Format:** `YYYYMMDDHHMMSS_*.ext`

**Examples:**
- `20231026183000_camera1.mp4`
- `20231026183500_camera1.mp4`

The tool will detect the 5-minute gap between these files and fill it with a blank video.

## Command Reference

### Main Commands

- `process`: The main command for processing files and directories.
- `apply-edits`: Applies edits from a timeline file to a media file.
- `apply-blank`: Replaces segments in a video with a blank video based on a timeline (uses blank_muted.MP4 by default).
- `compress`: Compresses video files to H.264 with smaller file sizes.

### Processing Flags

- `--complete`: Enables a full processing pipeline, including merging, timeline generation, and speaker analysis.
- `--merge-videos`: Merges timestamped videos in a directory.
- `--merge-only`: Merges videos without performing speech analysis.
- `--generate-timeline`: Creates a YAML timeline file with speech segments.
- `--analyze-speakers`: Enables speaker detection.
- `--detailed-analysis`: Uses a more accurate (but slower) speaker analysis model.
- `--speaker-analysis-only`: Performs speaker analysis without creating an output file.

### Video Merging Options

- `--force-overwrite` or `-f`: Overwrites existing merged videos without prompting.
- `--min-gap-threshold`: The minimum gap duration (in seconds) to fill with a blank video. Default is `2`.
- `--max-gap-threshold`: The maximum gap duration (in seconds) to fill.
- `--blank-video`: The path to the blank video file to use for filling gaps (default: blank_muted.MP4).

### Compression Command

The `compress` command converts videos to H.264 with optimized settings for smaller file sizes:

```bash
# Compress a single video file
uv run main.py compress input_video.mp4 -o compressed_video.mp4

# Compress all videos in a directory
uv run main.py compress /path/to/videos --output /path/to/compressed

# Adjust compression settings
uv run main.py compress video.mp4 --quality 20 --preset slow --max-width 1280
```

**Compression Options:**
- `--quality`: Video quality (CRF value, 18-28 typical range). Lower = better quality, higher file size (default: 23)
- `--preset`: Encoding speed preset - `ultrafast`, `fast`, `medium`, `slow`, `veryslow` (default: fast)
- `--max-width`: Maximum width for output video. Set to 0 to disable scaling (default: 1280)

The compression uses H.264 video codec with AAC audio, optimized for web streaming and broad compatibility.
