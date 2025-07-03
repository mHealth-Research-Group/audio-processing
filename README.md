# Audio/Video Processing - Voice Segment Removal

A Python tool that automatically detects and removes voice segments from audio and video files using AI-powered voice activity detection and FFmpeg processing.

## Features

- **AI-Powered Voice Detection**: Uses pyannote.audio's state-of-the-art voice activity detection model
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
uv run main.py input_audio.mp3
```

This will create `input_audio_no_conversations.mp3` with voice segments zeroed out.

Remove voice segments from a video file:

```bash
uv run main.py input_video.mp4
```

This will create `input_video_no_conversations.mp4` with voice segments zeroed out while preserving the original video quality.

### Custom Output Path

Specify a custom output file:

```bash
uv run main.py input_audio.mp3 -o output_audio.mp3
uv run main.py input_video.mp4 -o output_video.mp4
```

### Advanced Options

Fine-tune the voice detection parameters:

```bash
uv run main.py input_audio.mp3 \
  --min-duration-on 0.2 \
  --min-duration-off 0.1 \
  -o processed_audio.mp3

uv run main.py input_video.mp4 \
  --min-duration-on 0.2 \
  --min-duration-off 0.1 \
  -o processed_video.mp4
```

### Parameters

- `input_file`: Path to the input audio or video file (required)
- `-o, --output`: Custom output file path (optional)
- `--min-duration-on`: Minimum duration for speech regions in seconds (default: 0.1)
- `--min-duration-off`: Minimum duration for non-speech regions in seconds (default: 0.1)

### Help

Get full usage information:

```bash
uv run main.py --help
```

## How It Works

### For Audio Files
1. **Voice Activity Detection**: The script uses pyannote.audio's segmentation model to identify voice segments in the audio
2. **Segment Processing**: Detected voice segments are mapped to time intervals
3. **Audio Processing**: FFmpeg applies volume filters to zero out voice segments while preserving the rest of the audio
4. **Output Generation**: Creates a new audio file with voice segments removed

### For Video Files
1. **Audio Extraction**: Temporarily extracts audio from the video for analysis
2. **Voice Activity Detection**: Uses pyannote.audio to identify voice segments in the extracted audio
3. **Video Processing**: FFmpeg processes the original video, copying the video stream without re-encoding while applying voice removal filters to the audio track
4. **Output Generation**: Creates a new video file with original video quality and processed audio

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
├── main.py          # Main processing script
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

