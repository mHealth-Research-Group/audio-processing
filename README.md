# Audio/Video Processing Tool

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

**Requirements:**
- FFmpeg
- NVIDIA GPU with CUDA (optional, for acceleration)
- Hugging Face token

**Setup:**
```bash
echo "HUGGINGFACE_ACCESS_TOKEN=your_token_here" > .env
```

## Usage

### Process videos with complete pipeline
```bash
uv run main.py process /path/to/videos --complete --output final_video.mp4
```

### Generate timeline for editing
```bash
uv run main.py process video.mp4 --generate-timeline
```

### Apply privacy removal
1. Edit the generated `_timeline.yaml` file - change `type: speech` to `type: all` for segments to remove
2. Apply changes:
```bash
uv run main.py apply-blank video.mp4 video_timeline.yaml -o output.mp4
```

### Merge timestamped videos only
```bash
uv run main.py process /path/to/videos --merge-only --output merged.mp4
```

### Compress videos
```bash
uv run main.py compress video.mp4 -o compressed.mp4
```

### Rename files from CSV
Rename files based on CSV data and set modified timestamp metadata:
```bash
uv run python rename_from_csv.py filenames.csv --dry-run
uv run python rename_from_csv.py filenames.csv  # Apply changes
```

CSV format: `timestamp,filepath`
```csv
timestamp,filepath
20240811143022,data/video1.mp4
20240811144530,data/video2.mp4
```

This will:
- Rename files to `YYYYMMDD_compressed` format (e.g., `20240811_compressed.mp4`)
- Set file modified timestamp to match the original timestamp

## File Naming

### Input Files
For automatic video merging, use timestamp format: `YYYYMMDDHHMMSS_*.ext`

Example: `20231026183000_camera1.mp4`

### Output Files
The tool generates output files with the following naming conventions:

- **Merged videos**: `YYYYMMDD_merged.mp4` (based on first clip's date)
- **Processed videos**: `YYYYMMDD_processed.mp4` (based on original timestamp)
- **Blanked videos**: Same name as processed video (preserves `_processed` suffix)
- **Compressed videos**: `YYYYMMDD_compressed.mp4` (based on original timestamp)

Examples:
- Input: `20240811143022_video.mp4` → Processed: `20240811_processed.mp4`
- Input: `20240811143022_video.mp4` → Compressed: `20240811_compressed.mp4`