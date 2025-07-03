from pyannote.audio.pipelines import VoiceActivityDetection
from pyannote.audio import Model
import os
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import tempfile

load_dotenv()

# Video file extensions
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}


def load_model():
    """Load the pyannote segmentation model."""
    token = os.getenv("HUGGINGFACE_ACCESS_TOKEN")
    if not token:
        raise ValueError("HUGGINGFACE_ACCESS_TOKEN environment variable is required")

    return Model.from_pretrained("pyannote/segmentation-3.0", use_auth_token=token)


def detect_voice_segments(audio_path, model, min_duration_on=0.1, min_duration_off=0.1):
    """
    Detect voice activity segments in audio file.

    Args:
        audio_path: Path to input audio file
        model: Pyannote segmentation model
        min_duration_on: Minimum duration for speech regions (seconds)
        min_duration_off: Minimum duration for non-speech regions (seconds)

    Returns:
        List of (start_time, end_time) tuples for voice segments
    """
    pipeline = VoiceActivityDetection(segmentation=model)

    hyper_parameters = {
        "min_duration_on": min_duration_on,
        "min_duration_off": min_duration_off,
    }
    pipeline.instantiate(hyper_parameters)

    # Run voice activity detection
    vad_result = pipeline(audio_path)

    # Extract voice segments as (start, end) tuples
    voice_segments = []
    for segment in vad_result.itersegments():
        voice_segments.append((segment.start, segment.end))

    return voice_segments


def create_ffmpeg_filter(voice_segments, audio_duration):
    """
    Create ffmpeg filter to zero out voice segments.

    Args:
        voice_segments: List of (start_time, end_time) tuples
        audio_duration: Total duration of audio in seconds

    Returns:
        ffmpeg filter string
    """
    if not voice_segments:
        return None

    # Create volume filter to mute voice segments
    filter_parts = []
    for start, end in voice_segments:
        # Use volume filter to set volume to 0 for voice segments
        filter_parts.append(f"volume=0:enable='between(t,{start},{end})'")

    # Chain all volume filters
    return ",".join(filter_parts)


def process_audio_with_ffmpeg(input_path, output_path, voice_segments):
    """
    Process audio file with ffmpeg to zero out voice segments.

    Args:
        input_path: Path to input audio file
        output_path: Path for output audio file
        voice_segments: List of (start_time, end_time) tuples for voice segments
    """
    if not voice_segments:
        print("No voice segments detected. Copying original file.")
        subprocess.run(["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"], check=True)
        return

    # Get audio duration using ffprobe
    duration_cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(input_path)]
    duration_result = subprocess.run(duration_cmd, capture_output=True, text=True, check=True)
    audio_duration = float(duration_result.stdout.strip())

    # Create ffmpeg filter
    volume_filter = create_ffmpeg_filter(voice_segments, audio_duration)

    # Build ffmpeg command
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-af",
        volume_filter,
        "-c:a",
        "libmp3lame",  # Use MP3 codec for output
        "-b:a",
        "192k",  # Set bitrate
        str(output_path),
        "-y",
    ]

    print(f"Processing audio with {len(voice_segments)} voice segments to zero out...")
    print(f"Voice segments: {voice_segments}")

    # Run ffmpeg
    subprocess.run(ffmpeg_cmd, check=True)


def is_video_file(file_path):
    """Check if the file is a video file based on its extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(file_path):
    """Check if the file is an audio file based on its extension."""
    return Path(file_path).suffix.lower() in AUDIO_EXTENSIONS


def extract_audio_from_video(video_path, audio_path):
    """
    Extract audio from video file using ffmpeg.

    Args:
        video_path: Path to input video file
        audio_path: Path for extracted audio file
    """
    print(f"Extracting audio from video: {video_path}")

    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",  # No video
        "-acodec",
        "pcm_s16le",  # Use uncompressed audio for better processing
        "-ar",
        "16000",  # Sample rate suitable for pyannote
        "-ac",
        "1",  # Mono audio
        str(audio_path),
        "-y",
    ]

    subprocess.run(ffmpeg_cmd, check=True)
    print(f"Audio extracted to: {audio_path}")


def process_video_with_ffmpeg(input_path, output_path, voice_segments):
    """
    Process video file with ffmpeg to zero out voice segments in audio track.

    Args:
        input_path: Path to input video file
        output_path: Path for output video file
        voice_segments: List of (start_time, end_time) tuples for voice segments
    """
    if not voice_segments:
        print("No voice segments detected. Copying original file.")
        subprocess.run(["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"], check=True)
        return

    # Create ffmpeg filter for audio
    volume_filter = create_ffmpeg_filter(voice_segments, None)  # Duration not needed for video processing

    # Build ffmpeg command for video processing
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-c:v",
        "copy",  # Copy video stream without re-encoding
        "-af",
        volume_filter,  # Apply audio filter
        "-c:a",
        "aac",  # Use AAC codec for audio
        "-b:a",
        "192k",  # Set audio bitrate
        str(output_path),
        "-y",
    ]

    print(f"Processing video with {len(voice_segments)} voice segments to zero out...")
    print(f"Voice segments: {voice_segments}")

    # Run ffmpeg
    subprocess.run(ffmpeg_cmd, check=True)


def main():
    """Main function to process audio/video file and zero out voice segments."""
    parser = argparse.ArgumentParser(description="Remove voice segments from audio or video files using pyannote.audio and ffmpeg")
    parser.add_argument("input_file", help="Path to input audio or video file")
    parser.add_argument("-o", "--output", help="Path for output file (default: input_filename_no_conversations.ext)")
    parser.add_argument("--min-duration-on", type=float, default=0.1, help="Minimum duration for speech regions in seconds (default: 0.1)")
    parser.add_argument("--min-duration-off", type=float, default=0.1, help="Minimum duration for non-speech regions in seconds (default: 0.1)")

    args = parser.parse_args()

    # Validate input file
    input_path = Path(args.input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Check if input is video or audio
    is_video = is_video_file(input_path)
    is_audio = is_audio_file(input_path)

    if not (is_video or is_audio):
        raise ValueError(f"Unsupported file format. Supported formats: {VIDEO_EXTENSIONS | AUDIO_EXTENSIONS}")

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        if is_video:
            output_path = input_path.parent / f"{input_path.stem}_no_conversations{input_path.suffix}"
        else:
            output_path = input_path.parent / f"{input_path.stem}_no_conversations.mp3"

    try:
        print("Loading pyannote model...")
        model = load_model()

        # Handle video files
        if is_video:
            # Create temporary file for extracted audio
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio_path = temp_audio.name

            try:
                # Extract audio from video
                extract_audio_from_video(input_path, temp_audio_path)

                print(f"Detecting voice segments in video: {input_path}")
                voice_segments = detect_voice_segments(temp_audio_path, model, args.min_duration_on, args.min_duration_off)

                print(f"Found {len(voice_segments)} voice segments")

                print("Processing video to remove voice segments...")
                process_video_with_ffmpeg(input_path, output_path, voice_segments)

                print(f"✓ Successfully created video with zeroed voice segments: {output_path}")

            finally:
                # Clean up temporary audio file
                if os.path.exists(temp_audio_path):
                    os.unlink(temp_audio_path)

        # Handle audio files
        else:
            print(f"Detecting voice segments in audio: {input_path}")
            voice_segments = detect_voice_segments(str(input_path), model, args.min_duration_on, args.min_duration_off)

            print(f"Found {len(voice_segments)} voice segments")

            print("Processing audio to remove voice segments...")
            process_audio_with_ffmpeg(input_path, output_path, voice_segments)

            print(f"✓ Successfully created audio with zeroed voice segments: {output_path}")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
