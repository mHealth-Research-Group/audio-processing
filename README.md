# Audio/Video Processing - Voice Segment Removal

A Python tool that automatically detects and removes voice segments from audio and video files using AI-powered voice activity detection and FFmpeg processing.

## Features

- **AI-Powered Voice Detection**: Uses pyannote.audio's state-of-the-art voice activity detection model
- **Multiple Speaker Detection**: Identifies if multiple speakers are present in the audio/video
- **Overlapped Speech Analysis**: Detects when multiple speakers are talking simultaneously
- **Audio & Video Support**: Works with both audio files and video files (preserves video quality)
- **Automatic Voice Removal**: Zeros out detected voice segments while preserving background audio/music
- **Video Quality Preservation**: For video files, copies video stream without re-encoding for maximum quality
- **Flexible Configuration**: Adjustable parameters for detection sensitivity
- **High-Quality Output**: Maintains audio/video quality with configurable bitrate settings
- **Easy to Use**: Simple command-line interface

## Requirements

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/) package manager
- FFmpeg installed on your system
- Hugging Face account with access token

## Installation

### 1. Install uv

If you don't have uv installed, follow the [installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### 2. Clone and Setup

```bash
git clone <repository-url>
cd audio-processing
```

### 3. Install Dependencies

```bash
uv sync
```

This will automatically create a virtual environment and install all required dependencies.

### 4. Install FFmpeg

#### Ubuntu/Debian:
```bash
sudo apt update
sudo apt install ffmpeg
```

#### macOS:
```bash
brew install ffmpeg
```

#### Windows:
Download from [FFmpeg official website](https://ffmpeg.org/download.html) or use chocolatey:
```bash
choco install ffmpeg
```

### 5. Setup Hugging Face Token

1. Create a free account at [Hugging Face](https://huggingface.co/)
2. Get your access token from [Settings > Access Tokens](https://huggingface.co/settings/tokens)
3. Create a `.env` file in the project root:

```bash
echo "HUGGINGFACE_ACCESS_TOKEN=your_token_here" > .env
```

## Usage

### Basic Usage

Remove voice segments from an audio file:

```bash
uv run main.py process input_audio.mp3
```

This will create `input_audio_no_conversations.mp3` with voice segments zeroed out.

Remove voice segments from a video file:

```bash
uv run main.py process input_video.mp4
```

This will create `input_video_no_conversations.mp4` with voice segments zeroed out while preserving the original video quality.

**Note:** For backward compatibility, the old format without `process` still works:

```bash
uv run main.py input_audio.mp3
uv run main.py input_video.mp4
```

### Custom Output Path

Specify a custom output file:

```bash
uv run main.py process input_audio.mp3 -o output_audio.mp3
uv run main.py process input_video.mp4 -o output_video.mp4
```

### Batch Processing a Directory

Process all media files in a directory:

```bash
uv run main.py process /path/to/your/media
```

This will process all supported audio and video files in that directory. You can specify an output directory with `-o`.

To generate timelines for all files in a directory:
```bash
uv run main.py process /path/to/your/media --generate-timeline --speaker-analysis-only -o /path/to/output_dir
```

### Speaker Analysis

Analyze if multiple speakers are present in your audio/video:

```bash
# Analyze speakers and then process the file
uv run main.py process input_audio.mp3 --analyze-speakers

# Only analyze speakers without processing
uv run main.py process input_audio.mp3 --speaker-analysis-only

# Use detailed analysis (more accurate but slower)
uv run main.py process input_audio.mp3 --speaker-analysis-only --detailed-analysis
```

### Timeline Generation

Generate detailed JSON timeline with speaker information:

```bash
# Generate timeline and process the file
uv run main.py process input_audio.mp3 --generate-timeline

# Only generate timeline without processing
uv run main.py process input_audio.mp3 --speaker-analysis-only --generate-timeline

# Specify custom timeline output path
uv run main.py process input_audio.mp3 --generate-timeline --timeline-output my_timeline.json

# Combine speaker analysis with timeline generation
uv run main.py process input_audio.mp3 --analyze-speakers --generate-timeline
```

### Advanced Options

Fine-tune the voice detection parameters:

```bash
uv run main.py process input_audio.mp3 \
  --min-duration-on 0.2 \
  --min-duration-off 0.1 \
  --analyze-speakers \
  -o processed_audio.mp3

uv run main.py process input_video.mp4 \
  --min-duration-on 0.2 \
  --min-duration-off 0.1 \
  --analyze-speakers \
  -o processed_video.mp4
```

### Parameters

#### Process Mode (File or Directory Processing)

- `input_path`: Path to the input audio/video file or directory (required)
- `-o, --output`: Custom output file or directory path (optional)
- `--min-duration-on`: Minimum duration for speech regions in seconds (default: 0.1)
- `--min-duration-off`: Minimum duration for non-speech regions in seconds (default: 0.1)
- `--analyze-speakers`: Analyze and report if multiple speakers are detected
- `--speaker-analysis-only`: Only analyze speakers without processing the file
- `--detailed-analysis`: Use detailed direct model analysis for speaker detection (more accurate but slower)
- `--generate-timeline`: Generate JSON timeline with speaker analysis
- `--timeline-output`: Path for timeline JSON file or directory (default: input_filename_timeline.json)

#### Apply-Edits Mode (Timeline-Based Batch Processing)

- `directory`: Directory containing timeline JSON files and media files (required)
- `--output-suffix`: Suffix for output files (default: "_edited")

### Help

Get full usage information:

```bash
# General help
uv run main.py --help

# Help for the process command
uv run main.py process --help

# Help for timeline-based batch editing
uv run main.py apply-edits --help
```

**Note:** The `process` subcommand is available but optional for backward compatibility.

## How It Works

### Speaker Analysis
1. **Overlapped Speech Detection**: Uses pyannote.audio to detect when multiple speakers are talking simultaneously
2. **Direct Model Analysis**: Optionally analyzes raw model output to detect multiple speakers in audio chunks
3. **Statistical Analysis**: Calculates confidence scores and overlap percentages to determine if multiple speakers are present

### For Audio Files
1. **Voice Activity Detection**: The script uses pyannote.audio's segmentation model to identify voice segments in the audio
2. **Speaker Analysis** (optional): Analyzes if multiple speakers are present using overlapped speech detection
3. **Segment Processing**: Detected voice segments are mapped to time intervals
4. **Audio Processing**: FFmpeg applies volume filters to zero out voice segments while preserving the rest of the audio
5. **Output Generation**: Creates a new audio file with voice segments removed

### For Video Files
1. **Audio Extraction**: Temporarily extracts audio from the video for analysis
2. **Voice Activity Detection**: Uses pyannote.audio to identify voice segments in the extracted audio
3. **Speaker Analysis** (optional): Analyzes if multiple speakers are present in the extracted audio
4. **Video Processing**: FFmpeg processes the original video, copying the video stream without re-encoding while applying voice removal filters to the audio track
5. **Output Generation**: Creates a new video file with original video quality and processed audio

## Supported Formats

### Audio Formats
The script supports any audio format that is supported by torchaudio, including:
- **MP3**
- **WAV** (recommended)
- **FLAC**
- **AAC**
- **OGG**
- **M4A**

### Video Formats
The script supports common video formats:
- **MP4** (recommended)
- **AVI**
- **MOV**
- **MKV**
- **WEBM**
- **FLV**
- **WMV**
- **M4V**

### Format Conversion (if needed)

If you encounter issues with a specific format, you can convert it using FFmpeg. For example:

```bash
# Convert M4A to WAV
ffmpeg -i input_file.m4a output_file.wav

# Convert MKV to MP4
ffmpeg -i input_file.mkv -c copy output_file.mp4
```

Output formats:
- Audio files: MP3 with 192kbps bitrate by default
- Video files: Original video codec (copied), AAC audio with 192kbps bitrate

## Timeline JSON Format

When using `--generate-timeline`, the tool generates a JSON file with detailed speaker analysis:

```json
{
  "timeline": [
    {
      "start": "0:00.000",
      "end": "0:02.500",
      "duration": "0:02.500",
      "type": "silence",
      "speakers": 0,
      "label": "silence"
    },
    {
      "start": "0:02.500",
      "end": "0:08.300",
      "duration": "0:05.800",
      "type": "speech",
      "speakers": 1,
      "label": "speaking"
    },
    {
      "start": "0:08.300",
      "end": "0:15.700",
      "duration": "0:07.400",
      "type": "speech",
      "speakers": 2,
      "label": "conversation"
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

### Timeline Segment Types:
- **silence**: No speech detected
- **speaking**: Single speaker detected
- **conversation**: Multiple speakers detected (simultaneous or overlapped speech)

### Labels:
- **silence**: No voice activity
- **speaking**: Single person speaking
- **conversation**: Multiple people speaking (indicates dialogue/conversation)

## Custom Timeline Editing

The main script now includes built-in functionality to apply custom edits to media files based on timeline JSON files. This is useful when you want to:

- Selectively zero out specific conversation segments
- Process multiple files with existing timeline analyses
- Apply custom edits based on manually reviewed or modified timelines

**Note:** Timeline editing functionality is now fully integrated into the main script for a streamlined experience.

### How It Works

The apply-edits mode processes timeline JSON files (generated by the main script with `--generate-timeline`) and automatically zeros out segments labeled as "conversation" (multiple speakers). This allows for batch processing and selective editing based on the timeline analysis.

### Basic Usage

Process all timeline files in a directory:

```bash
uv run main.py apply-edits /path/to/directory
```

This will:
1. Find all `*_timeline.json` files in the directory
2. For each timeline file, find the corresponding media file
3. Extract segments labeled as "conversation" 
4. Create edited versions with those segments zeroed out
5. Save output files with `_edited` suffix

### Custom Output Suffix

Specify a custom suffix for output files:

```bash
uv run main.py apply-edits /path/to/directory --output-suffix "_no_conversations"
```

### Workflow Example

1. **Generate timeline files for analysis:**
   You can generate timelines for all media files in a directory in one go:
   ```bash
   uv run main.py process /path/to/media --speaker-analysis-only --generate-timeline --timeline-output /path/to/media
   ```
   
   Alternatively, you can process files individually:
   ```bash
   uv run main.py process video1.mp4 --speaker-analysis-only --generate-timeline
   uv run main.py process video2.mp4 --speaker-analysis-only --generate-timeline
   uv run main.py process audio1.mp3 --speaker-analysis-only --generate-timeline
   ```

2. **Review the generated timeline files** (optional):
   - `video1_timeline.json`
   - `video2_timeline.json` 
   - `audio1_timeline.json`

3. **Apply edits to all files:**
   ```bash
   uv run main.py apply-edits ./
   ```

4. **Output files created:**
   - `video1_edited.mp4`
   - `video2_edited.mp4`
   - `audio1_edited.mp3`

### Directory Structure Requirements

The script expects timeline files and their corresponding media files to be in the same directory:

```
my_media/
├── video1.mp4
├── video1_timeline.json
├── video2.mp4
├── video2_timeline.json
├── audio1.mp3
└── audio1_timeline.json
```

### What Gets Zeroed Out

The script automatically identifies and zeros out segments where:
- **Label** = "conversation" (multiple speakers detected)
- **Type** = "speech" with **speakers** > 1

Segments labeled as "speaking" (single speaker) and "silence" are preserved.

### Custom Timeline Editing

You can manually edit the timeline JSON files before running the apply-edits command:

1. **Change labels**: Modify segment labels to control what gets zeroed out
   - Change "speaking" to "conversation" to zero out single-speaker segments
   - Change "conversation" to "speaking" to preserve multi-speaker segments

2. **Example manual edit:**
   ```json
   {
     "start": "0:02.500",
     "end": "0:08.300", 
     "duration": "0:05.800",
     "type": "speech",
     "speakers": 1,
     "label": "conversation"  // Changed from "speaking" to zero this out
   }
   ```

### Error Handling

The apply-edits mode will:
- Skip timeline files without corresponding media files
- Report missing files and processing errors
- Continue processing remaining files if some fail
- Show progress and results for each file

### Help

Get full usage information:

```bash
uv run main.py apply-edits --help
```

## Troubleshooting

### Common Issues

1. **"HUGGINGFACE_ACCESS_TOKEN environment variable is required"**
   - Make sure you've created the `.env` file with your Hugging Face token

2. **"ffmpeg: command not found"**
   - Install FFmpeg following the installation instructions above

3. **"No voice segments detected"**
   - Try adjusting the `--min-duration-on` and `--min-duration-off` parameters
   - Ensure the audio file contains clear voice segments

4. **Memory issues with large files**
   - The AI model requires significant memory. Consider processing shorter audio segments for very large files

### Performance Tips

- For better performance on large files, consider splitting them into smaller chunks
- Adjust the minimum duration parameters based on your audio characteristics
- Use SSD storage for faster processing of large audio files

## Project Structure

```
audio-processing/
├── main.py              # Main processing script with timeline editing
├── pyproject.toml       # Project dependencies and metadata
├── .env                 # Environment variables (create this)
├── .python-version      # Python version specification
├── uv.lock             # Dependency lockfile
└── README.md           # This file
```

## Contributing

This project uses uv for dependency management. To contribute:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with `uv run main.py`
5. Submit a pull request

## License

[Add your license information here]

## Acknowledgments

- [pyannote.audio](https://github.com/pyannote/pyannote-audio) for the voice activity detection model
- [FFmpeg](https://ffmpeg.org/) for audio processing capabilities

