# Audio/Video Processing Tool

A powerful AI-driven tool for processing audio and video files with automatic speaker detection, video merging, and intelligent gap filling. Perfect for processing security camera footage, meeting recordings, and timestamped video sequences.

## Quick Start

### The Complete Processing Command

For most use cases, simply use the `--complete` flag for comprehensive processing:

```bash
# Process timestamped videos with complete pipeline
uv run main.py process video-folder --complete --output final_video.mp4

# Equivalent to:
# uv run main.py process video-folder --merge-videos --generate-timeline --analyze-speakers --output final_video.mp4
```

This single command will:
1. **Merge** all timestamped videos chronologically
2. **Fill gaps** with black frames where needed
3. **Analyze speakers** and detect conversations
4. **Generate timeline** JSON for manual editing
5. **Convert to H264** with GPU acceleration
6. **Create final processed video** with conversations muted

## Installation

### Prerequisites

- **FFmpeg** with GPU support (for hardware acceleration)
- **Hugging Face account** and access token

### Setup

1. **Clone and install:**
   ```bash
   git clone <repository-url>
   cd audio-processing
   uv sync
   ```

2. **Configure Hugging Face token:**
   ```bash
   # Create .env file
   echo "HUGGINGFACE_ACCESS_TOKEN=your_token_here" > .env
   ```

3. **Verify GPU acceleration (optional but recommended):**
   ```bash
   ffmpeg -encoders | grep nvenc  # Should show h264_nvenc if GPU available
   ```

## Usage Guide

### Complete Processing (Recommended)

```bash
# Complete processing with default settings
uv run main.py process /path/to/videos/ --complete

# Complete processing with custom output and quality
uv run main.py process /path/to/videos/ --complete \
    --output high_quality.mp4 \
    --h264-preset slow \
    --h264-crf 18

# Complete processing with custom gap threshold
uv run main.py process /path/to/videos/ --complete \
    --max-gap-threshold 120 \
    --output security_footage.mp4
```

### 🎬 Video Merging Only

For timestamped videos that just need merging without speech analysis:

```bash
# Fast merge without speech processing
uv run main.py process /path/to/videos/ --merge-videos --merge-only

# Merge with custom gap handling
uv run main.py process /path/to/videos/ --merge-videos \
    --max-gap-threshold 60 \
    --h264-preset faster

# Merge without H264 conversion (faster, larger files)
uv run main.py process /path/to/videos/ --merge-videos --no-h264
```

### Single File Processing

```bash
# Process individual audio/video file
uv run main.py process your_recording.mp4 --generate-timeline

# Analyze speakers only
uv run main.py process meeting.mp4 --analyze-speakers --speaker-analysis-only

# Advanced speaker detection
uv run main.py process complex_audio.mp4 --detailed-analysis --generate-timeline
```

### Advanced Configuration

**H264 Quality Settings:**
```bash
# High quality (slower encoding)
--h264-preset slow --h264-crf 18

# Balanced (default: optimized for speed)
--h264-preset faster --h264-crf 28

# Fast encoding (larger files)
--h264-preset fast --h264-crf 30

# Maximum speed (largest files)
--h264-preset ultrafast --h264-crf 35
```

**Gap Handling:**
```bash
# Default: Fill all gaps regardless of size
# (no --max-gap-threshold needed)

# Fill gaps up to 2 minutes only
--max-gap-threshold 120

# Fill gaps up to 30 seconds only
--max-gap-threshold 30

# No gap filling (concatenate adjacent videos only)
--max-gap-threshold 0
```

## Two-Pass Workflow

### Pass 1: Generate Timeline
```bash
uv run main.py process video.mp4 --complete
```
This creates:
- `video_timeline.json` - Detected speech segments
- `video_no_conversations.mp4` - Initial processed video

### Pass 2: Apply Manual Edits
Edit the JSON file to customize effects:
```json
{
  "start": 45.2,
  "end": 67.8,
  "label": "speaking",     // Mute audio only
  "label": "black",        // Black video, keep audio  
  "label": "all"           // Mute audio AND black video
}
```

Then apply your changes:
```bash
uv run main.py apply-edits .
```

## File Naming Convention

For automatic video merging, use timestamp-based filenames:

**Format:** `YYYYMMDDHHMMSS_XXXXXX.ext`

**Examples:**
- `20250703175433_000028.MP4` → July 3, 2025 at 17:54:33
- `20250703175633_000029.MP4` → July 3, 2025 at 17:56:33  
- Gap detected: 2 minutes → Black frame inserted

## Real-World Examples

### Security Camera Footage
```bash
# Process 24-hour security footage with 5-minute gap tolerance
uv run main.py process /security/2025-01-15/ --complete \
    --max-gap-threshold 300 \
    --h264-preset faster \
    --output security_2025-01-15_processed.mp4
```

### Meeting Recordings  
```bash
# High-quality meeting processing with detailed speaker analysis
uv run main.py process meeting_parts/ --complete \
    --detailed-analysis \
    --h264-preset slow \
    --h264-crf 18 \
    --output meeting_final.mp4
```

### Quick Preview Generation
```bash
# Fast processing for previews
uv run main.py process raw_footage/ --merge-videos --merge-only \
    --h264-preset ultrafast \
    --output preview.mp4
```

### Batch Processing Directory
```bash
# Process multiple video sets
for dir in footage_*; do
    uv run main.py process "$dir" --complete --output "processed_${dir}.mp4"
done
```

## 🔧 Command Reference

### Main Commands
- `process` - Main processing command
- `apply-edits` - Apply timeline edits from JSON files  
- `apply-effects` - Apply effects to specific time ranges

### Key Flags
- `--complete` - **Enable full processing pipeline**
- `--merge-videos` - Enable video merging for timestamped files
- `--merge-only` - Merge without speech analysis (faster)
- `--generate-timeline` - Create JSON timeline for manual editing
- `--analyze-speakers` - Enable speaker detection and analysis

### Performance Options  
- `--h264-preset` - Encoding speed: `ultrafast` to `veryslow`
- `--h264-crf` - Quality: `0-51` (lower = better quality)
- `--no-h264` - Skip conversion (faster, keeps original codec)
- `--max-gap-threshold` - Maximum gap to fill (seconds)

## 🚀 Performance Features

- **Automatic GPU Detection**: Uses NVIDIA NVENC when available
- **Parallel Processing**: Utilizes all CPU cores for optimal speed  
- **Memory Efficient**: Processes large videos without memory issues
- **Smart Concatenation**: Avoids re-encoding when possible
- **Optimized Pipelines**: Different strategies for different use cases

## 💡 Tips & Best Practices

1. **Use `--complete`** for most workflows - it's the recommended starting point
2. **Start with `--merge-only`** for quick previews, then rerun with full analysis
3. **Adjust `--max-gap-threshold`** based on your content (30s for presentations, 300s for security footage)
4. **Use `--h264-preset faster`** for faster processing, `slow` for best quality
5. **Keep original files** - the tool creates new files without modifying originals
6. **Check GPU acceleration** - significant speed improvement with NVIDIA cards

## 🔍 Troubleshooting

**No audio stream errors:** Tool automatically handles video-only files ✅  
**GPU not detected:** Ensure NVIDIA drivers and ffmpeg with NVENC support  
**Large file sizes:** Use lower `--h264-crf` values or faster presets  
**Timeline editing:** Ensure JSON syntax is valid after manual edits