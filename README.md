# Audio/Video Processing Tool with Flexible Effects

An advanced audio and video processing tool that uses AI-powered speaker detection to apply flexible effects to different segments. The tool can automatically detect speech, conversations, and apply customizable effects like audio muting and video blacking based on labels.

## Quick Start

### Basic Usage

```bash
# Analyze and generate timeline for a file
uv run main.py process video.mp4 --generate-timeline

# Apply effects based on timeline 
uv run main.py apply-edits ./directory
```

### Time Range Effects (New!)

```bash
# Apply effects to specific time ranges (no timeline needed)
uv run main.py apply-effects video.mp4 "1:30-2:45" "5:00-5:30" --effect black

# Multiple ranges with different effects
uv run main.py apply-effects video.mp4 "0:30-1:00" --effect mute
uv run main.py apply-effects video.mp4 "2:00-3:00" --effect all
```

### Custom Effects

```bash
# Generate timeline and manually edit labels for custom effects  
uv run main.py process video.mp4 --generate-timeline
# Then manually edit the timeline JSON to change labels (e.g., speaking->black)
```

## Installation

### Prerequisites

- Python 3.11 or higher
- FFmpeg installed and available in PATH
- A Hugging Face account for pyannote.audio model access

### Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd audio-processing
   ```

2. **Install dependencies using uv:**
   ```bash
   uv sync
   ```

3. **Get your Hugging Face access token:**
   - Sign up at [Hugging Face](https://huggingface.co/join)
   - Go to [Settings > Access Tokens](https://huggingface.co/settings/tokens)
   - Create a new token with read access

4. **Configure your access token:**
   ```bash
   echo "HUGGINGFACE_ACCESS_TOKEN=your_token_here" > .env
   ```

## Effect Configuration System

The tool uses a flexible label-based effects system. **By default, all speech segments (both single speaker and conversations) are muted**, while silence is preserved. You can customize this behavior by manually editing timeline JSON files or using the direct time range commands.

### Built-in Effect Labels

- **`black`**: Black out video but preserve audio
- **`mute`**: Mute audio only (keep video)
- **`all`**: Remove both voice and video
- **`speaking`**: Mute audio by default (single speaker detected)
- **`conversation`**: Mute audio by default (multiple speakers detected)
- **`silence`**: No effects (no speech detected)

### Effect Types

- **Audio Muting**: Sets volume to 0 during specified segments
- **Video Blacking**: Draws a black box over the entire video during specified segments
- **Combined**: Both audio muting and video blacking simultaneously

## Usage Guide

### Basic Processing

```bash
# Process a single file with timeline generation (automatically mutes speech)
uv run main.py process input.mp4 --generate-timeline

# Process all files in a directory (automatically mutes speech)
uv run main.py process /path/to/directory --generate-timeline

# Apply effects to files with existing timelines (speech segments will be muted)
uv run main.py apply-edits /path/to/directory
```

### Custom Effect Configuration

```bash
# Generate timeline (custom effects require manual editing)
uv run main.py process video.mp4 --generate-timeline

# Process with timeline-based effects after manual editing
# (Edit the timeline JSON to change labels as needed, then:)
uv run main.py apply-edits . --output-suffix "_processed"
```

### Advanced Workflow

1. **Generate timelines for analysis:**
   ```bash
   uv run main.py process /media/directory \
     --speaker-analysis-only \
     --generate-timeline \
     --detailed-analysis
   ```

2. **Review and manually edit timeline JSON files** (optional)
   - Modify labels in `*_timeline.json` files
   - Change "speaking" to "black" for segments you want to black out
   - Change "conversation" to "mute" for segments you want to mute only

3. **Apply effects based on timelines:**
   ```bash
   uv run main.py apply-edits /media/directory --output-suffix "_processed"
   ```

### Manual Timeline Editing

You can manually edit timeline JSON files to control exactly what effects are applied:

```json
{
  "timeline": [
    {
      "start": "0:02.500",
      "end": "0:08.300",
      "duration": "0:05.800", 
      "type": "speech",
      "speakers": 1,
      "label": "black"  // Changed from "speaking" - will black video, preserve audio
    },
    {
      "start": "0:08.300", 
      "end": "0:15.700",
      "duration": "0:07.400",
      "type": "speech",
      "speakers": 2, 
      "label": "conversation"  // Default - will mute audio only
    }
  ]
}
```

### Speaker Analysis

```bash
# Analyze speakers without processing
uv run main.py process audio.mp3 --speaker-analysis-only

# Detailed speaker analysis (slower but more accurate)
uv run main.py process audio.mp3 --speaker-analysis-only --detailed-analysis

# Combined analysis with timeline generation
uv run main.py process audio.mp3 --analyze-speakers --generate-timeline
```

### Batch Processing with Custom Effects

```bash
# Generate timelines for all files in directory
uv run main.py process /media/directory --generate-timeline --speaker-analysis-only

# Manually edit timeline JSON files to set desired effects (change labels to "black", "mute", etc.)
# Then apply timeline-based edits
uv run main.py apply-edits /media/directory --output-suffix "_censored"
```

## Command Reference

### Process Mode

```bash
uv run main.py process INPUT [OPTIONS]
```

**Arguments:**
- `INPUT`: Path to audio/video file or directory

**Options:**
- `-o, --output PATH`: Custom output file or directory
- `--min-duration-on FLOAT`: Minimum speech duration (default: 0.1s)
- `--min-duration-off FLOAT`: Minimum silence duration (default: 0.1s)  
- `--analyze-speakers`: Analyze and report multiple speakers
- `--speaker-analysis-only`: Only analyze, don't process
- `--detailed-analysis`: Use detailed speaker analysis (slower, more accurate)
- `--generate-timeline`: Generate JSON timeline file
- `--timeline-output PATH`: Custom timeline file path


### Apply-Edits Mode

```bash
uv run main.py apply-edits DIRECTORY [OPTIONS]
```

**Arguments:**
- `DIRECTORY`: Directory with timeline JSON files and media files

**Options:**
- `--output-suffix TEXT`: Suffix for output files (default: "_edited")
- `--effect-labels LABEL [...]`: Labels to apply effects to (backward compatibility)

### Apply-Effects Mode (New!)

```bash
uv run main.py apply-effects INPUT TIME_RANGES [OPTIONS]
```

**Arguments:**
- `INPUT`: Path to input media file
- `TIME_RANGES`: One or more time ranges (e.g., "1:30-2:45" "5:00-5:30")

**Options:**
- `-o, --output PATH`: Custom output file path
- `--effect {black,mute,all}`: Type of effect to apply (default: "all")

**Time Range Formats:**
- `"MM:SS-MM:SS"`: Minutes and seconds (e.g., "1:30-2:45")
- `"MM:SS.sss-MM:SS.sss"`: With milliseconds (e.g., "1:30.500-2:45.750")
- `"SS-SS"`: Seconds only (e.g., "90-165")

### Debug Mode

```bash
uv run main.py debug-encoding FILE
```

**Arguments:**
- `FILE`: Path to file for encoding analysis

## Timeline JSON Format

The tool generates detailed JSON timelines with the following structure:

```json
{
  "timeline": [
    {
      "start": "0:00.000",           // Start time (MM:SS.mmm)
      "end": "0:02.500",             // End time  
      "duration": "0:02.500",        // Segment duration
      "type": "silence",             // "silence" or "speech"
      "speakers": 0,                 // Number of speakers detected
      "label": "silence"             // Label for effect processing
    },
    {
      "start": "0:02.500",
      "end": "0:08.300", 
      "duration": "0:05.800",
      "type": "speech",
      "speakers": 1,
      "label": "speaking"            // Single speaker
    },
    {
      "start": "0:08.300",
      "end": "0:15.700",
      "duration": "0:07.400", 
      "type": "speech",
      "speakers": 2,
      "label": "conversation"        // Multiple speakers
    }
  ],
  "summary": {
    "total_duration": "0:15.700",
    "total_speech_time": "0:13.200", 
    "total_conversation_time": "0:07.400",
    "total_speaking_time": "0:05.800",
    "total_silence_time": "0:02.500",
    "speech_percentage": 84.1,
    "conversation_percentage": 47.1,
    "has_multiple_speakers": true,
    "num_segments": 3
  },
  "has_multiple_speakers": true
}
```

### Timeline Labels

- **`silence`**: No voice activity detected (no effects applied)
- **`speaking`**: Single person speaking (audio muted by default)
- **`conversation`**: Multiple people speaking (audio muted by default)
- **`black`**: Custom label for segments to black video but preserve audio (manual editing)
- **`mute`**: Custom label for segments to mute audio only (manual editing)
- **`all`**: Custom label for segments to remove both voice and video (manual editing)

## Supported Formats

### Audio Formats
- **MP3**, **WAV**, **FLAC**, **AAC**, **OGG**, **M4A**

### Video Formats  
- **MP4**, **AVI**, **MOV**, **MKV**, **WEBM**, **FLV**, **WMV**, **M4V**

### Output Quality
- **Audio**: AAC 192kbps (when processing), original codec (when copying)
- **Video**: Original codec (when copying), H.264 fast preset (when processing)

## New Feature: Direct Time Range Processing

The `apply-effects` command allows you to directly specify time ranges without needing to generate and edit timeline files. This is perfect when you already know exactly which segments need effects applied.

### Quick Time Range Examples

```bash
# Black out video (but keep audio) from 1:30 to 2:45
uv run main.py apply-effects video.mp4 "1:30-2:45" --effect black

# Mute audio from multiple segments  
uv run main.py apply-effects video.mp4 "0:30-1:00" "3:15-4:30" --effect mute

# Remove both audio and video from specific ranges
uv run main.py apply-effects video.mp4 "2:00-2:30" "5:45-6:15" --effect all

# Using different time formats
uv run main.py apply-effects video.mp4 "90-120" "300.5-315.75" --effect black
```

### Effect Types Explained

- **`--effect black`**: Video becomes black, but original audio is preserved
- **`--effect mute`**: Audio is silenced, but video remains unchanged  
- **`--effect all`**: Both audio and video are removed (audio muted + video blacked)

### Advantages of Time Range Processing

✅ **No timeline generation required** - process immediately  
✅ **Handle overlapping ranges** - FFmpeg automatically merges overlapping segments  
✅ **Multiple time formats** - use what's convenient (MM:SS, seconds, etc.)  
✅ **Fast and direct** - skip the analyze → edit → apply workflow  
✅ **Perfect for known segments** - when you already know what needs censoring

### When to Use Which Approach

| Use Case | Recommended Command | Why |
|----------|-------------------|-----|
| **You know exact times to censor** | `apply-effects` | Direct and fast, no analysis needed |
| **You need to find where people speak** | `process` + `apply-edits` | AI finds speech segments automatically |
| **You want to distinguish speakers** | `process --detailed-analysis` | Better speaker detection |
| **Batch processing many files** | `process` directory + `apply-edits` | Analyze multiple files at once |
| **You want to review before applying** | `process --generate-timeline` → edit → `apply-edits` | Manual control over each segment |
| **Quick one-off censoring** | `apply-effects` | Immediate results |

## Examples

### Example 1: Basic Content Moderation

```bash
# Generate timeline
uv run main.py process interview.mp4 --generate-timeline

# Manually edit interview_timeline.json to mark inappropriate segments as "black"

# Apply effects
uv run main.py apply-edits ./
```

### Example 2: Conference Call Processing

```bash
# Process with timeline generation, then edit JSON to mute conversations
uv run main.py process meeting.mp4 --generate-timeline
# Edit meeting_timeline.json: change "conversation" labels to "mute"
uv run main.py apply-edits . --output-suffix "_clean"
```

### Example 3: Batch Privacy Protection

```bash
# Generate timelines, then manually edit to black out all speech
uv run main.py process /recordings/ --generate-timeline --speaker-analysis-only
# Edit timeline JSON files: change "speaking" and "conversation" labels to "black"
uv run main.py apply-edits /recordings/ --output-suffix "_private"
```

### Example 4: Selective Audio Processing

```bash
# Generate timeline, then edit to mute conversations but keep single speaker segments
uv run main.py process podcast.mp3 --generate-timeline
# Edit podcast_timeline.json: change "conversation" labels to "mute"
uv run main.py apply-edits . --output-suffix "_solo"
```

### Example 5: Direct Time Range Processing (New!)

```bash
# Quick censoring without timeline analysis
uv run main.py apply-effects interview.mp4 "5:30-6:15" "12:00-12:45" --effect black

# Mute specific questions in a Q&A video
uv run main.py apply-effects qa_session.mp4 "2:15-2:30" "8:45-9:00" --effect mute

# Remove entire segments completely
uv run main.py apply-effects conference.mp4 "45:30-47:00" "1:15:20-1:16:30" --effect all

# Mix of different time formats
uv run main.py apply-effects video.mp4 "30-45" "1:30.5-2:15.75" "180-200" --effect black
```

## Troubleshooting

### Common Issues

1. **Unicode Errors (Windows)**:
   ```bash
   set PYTHONIOENCODING=utf-8
   uv run main.py debug-encoding problematic_file.json
   ```

2. **FFmpeg Not Found**:
   - Install FFmpeg: https://ffmpeg.org/download.html
   - Add FFmpeg to your system PATH

3. **Hugging Face Authentication**:
   ```bash
   # Check your .env file
   cat .env
   
   # Regenerate token if needed
   echo "HUGGINGFACE_ACCESS_TOKEN=new_token_here" > .env
   ```

4. **Memory Issues with Large Files**:
   ```bash
   # Process in smaller chunks or use detailed analysis
   uv run main.py process large_file.mp4 --detailed-analysis
   ```

5. **Time Range Format Errors**:
   ```bash
   # Make sure time ranges use proper format
   # Good: "1:30-2:45", "90-120", "1:30.5-2:45.75"
   # Bad: "1:30:2:45", "1.30-2.45"
   
   # Test time parsing first
   uv run main.py apply-effects test.mp4 "0:05-0:10" --effect mute
   ```

### Debug Commands

```bash
# Check file encoding
uv run main.py debug-encoding file.json

# Test with minimal processing
uv run main.py process file.mp4 --speaker-analysis-only

# Verbose timeline generation
uv run main.py process file.mp4 --generate-timeline --detailed-analysis
```

## Advanced Configuration

### Environment Variables

- `HUGGINGFACE_ACCESS_TOKEN`: Required for pyannote.audio model access
- `PYTHONIOENCODING`: Set to "utf-8" on Windows if experiencing encoding issues

### Performance Tuning

- Use `--detailed-analysis` for better accuracy (slower)
- Adjust `--min-duration-on` and `--min-duration-off` for sensitivity
- Process directories in batches for very large datasets

### Custom Effect Development

You can modify the `EFFECT_CONFIGS` in `main.py` to add your own label-to-effect mappings:

```python
EFFECT_CONFIGS = {
    "speaking": {"mute_audio": True, "black_video": False},   # Mute single speaker segments
    "conversation": {"mute_audio": True, "black_video": False}, # Mute conversation segments
    "silence": {"mute_audio": False, "black_video": False},   # No effects for silence
    "black": {"mute_audio": False, "black_video": True},      # Black video, preserve audio
    "mute": {"mute_audio": True, "black_video": False},       # Mute audio, keep video
    "all": {"mute_audio": True, "black_video": True},         # Remove both
    
    # Add your custom effects:
    "custom_label": {"mute_audio": True, "black_video": False},
    "sensitive": {"mute_audio": True, "black_video": True},
    "blur": {"mute_audio": False, "black_video": True},      # Could be extended for blur effects
}
```

## Contributing

Contributions are welcome! Please ensure:

1. Code follows the existing style and structure
2. New features include appropriate documentation
3. Test with various file formats and edge cases
4. Update README for significant changes

## License

[Add your license information here]

