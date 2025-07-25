import subprocess
from .utils import (
    EFFECT_CONFIGS,
    run_subprocess_with_encoding,
    mmss_to_seconds,
)


def extract_audio_from_video(video_path, audio_path):
    """Extract audio from video file using ffmpeg."""
    print(f"Extracting audio from {video_path} to {audio_path}")

    # First check if video has audio streams
    probe_cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_path),
    ]

    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            raise subprocess.CalledProcessError(1, probe_cmd, "No audio streams found")
    except subprocess.CalledProcessError:
        raise subprocess.CalledProcessError(1, probe_cmd, "No audio streams found in video")

    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(audio_path),
        "-y",
    ]
    run_subprocess_with_encoding(ffmpeg_cmd, check=True)


def create_audio_filter(mute_segments):
    """Create ffmpeg audio filter to mute specific segments."""
    if not mute_segments:
        return None

    # Create enable conditions for each segment
    enable_conditions = []
    for start, end in mute_segments:
        enable_conditions.append(f"between(t,{start},{end})")

    # Combine all conditions with OR logic
    combined_condition = "+".join(enable_conditions)

    return f"volume=0:enable='{combined_condition}'"


def create_video_filter(black_segments):
    """Create ffmpeg video filter to black out specific segments."""
    if not black_segments:
        return None
    filter_parts = [
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{start},{end})'"
        for start, end in black_segments
    ]
    return ",".join(filter_parts)


def process_media_with_effects(input_path, output_path, effect_segments):
    """Process media file with flexible effects."""
    all_mute_segments = effect_segments["mute_only"] + effect_segments["mute_and_black"]
    all_black_segments = effect_segments["black_only"] + effect_segments["mute_and_black"]

    if not all_mute_segments and not all_black_segments:
        print("No effects to apply. Copying original file.")
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"],
            check=True,
        )
        return

    audio_filter = create_audio_filter(all_mute_segments)
    video_filter = create_video_filter(all_black_segments)
    ffmpeg_cmd = ["ffmpeg", "-i", str(input_path)]

    if video_filter:
        ffmpeg_cmd.extend(["-vf", video_filter, "-c:v", "libx264", "-preset", "fast"])
    else:
        ffmpeg_cmd.extend(["-c:v", "copy"])

    if audio_filter:
        ffmpeg_cmd.extend(["-af", audio_filter, "-c:a", "aac", "-b:a", "192k"])
    else:
        ffmpeg_cmd.extend(["-c:a", "copy"])

    ffmpeg_cmd.extend([str(output_path), "-y"])
    run_subprocess_with_encoding(ffmpeg_cmd, check=True)


def extract_segments_by_effects(timeline_data, target_effects=None):
    """Extract segments that should have specific effects applied."""
    segments = {"mute_only": [], "black_only": [], "mute_and_black": []}
    for segment in timeline_data["timeline"]:
        label = segment.get("label", "")
        effects = EFFECT_CONFIGS.get(label, {"mute_audio": False, "black_video": False})

        if not effects["mute_audio"] and not effects["black_video"]:
            continue
        if target_effects and (
            effects["mute_audio"] != target_effects.get("mute_audio", False)
            or effects["black_video"] != target_effects.get("black_video", False)
        ):
            continue

        start_seconds = mmss_to_seconds(segment["start"])
        end_seconds = mmss_to_seconds(segment["end"])

        if effects["mute_audio"] and effects["black_video"]:
            segments["mute_and_black"].append((start_seconds, end_seconds))
        elif effects["mute_audio"]:
            segments["mute_only"].append((start_seconds, end_seconds))
        elif effects["black_video"]:
            segments["black_only"].append((start_seconds, end_seconds))
    return segments


def apply_blank_video_to_segments(input_video, output_path, speech_segments, blank_video_path):
    """
    Replace speech segments in video with blank video using the same approach as video merging.
    Uses FFmpeg's concat demuxer with stream copy - exactly like gap filling in video merger.

    Args:
        input_video: Path to input video
        output_path: Path for output video
        speech_segments: List of (start_time, end_time) tuples for speech segments
        blank_video_path: Path to blank video file
    """
    from .video_merger import create_gap_video_from_blank, normalize_path_for_ffmpeg
    import shutil

    print(f"Applying blank video to {len(speech_segments)} speech segments...")

    # Create temporary directory for processing
    temp_dir = input_video.parent / "temp_blank_processing"
    temp_dir.mkdir(exist_ok=True)

    try:
        # Sort speech segments by start time
        speech_segments = sorted(speech_segments, key=lambda x: x[0])

        # Create blank video segments for each speech segment
        blank_videos = {}
        for i, (speech_start, speech_end) in enumerate(speech_segments):
            speech_duration = speech_end - speech_start
            blank_segment_path = temp_dir / f"blank_{i}.mp4"
            create_gap_video_from_blank(blank_video_path, blank_segment_path, speech_duration)
            blank_videos[i] = blank_segment_path

        # Create timeline items with timings
        timeline_items = []

        # Always start with a tiny video segment to ensure audio stream exists
        min_video_start = 0.033  # Always include first ~1 frame (33ms) as video

        # Add video segments (non-speech parts)
        current_time = 0.0
        for i, (speech_start, speech_end) in enumerate(speech_segments):
            # Ensure we always have some video at the start
            video_start = current_time
            video_end = (
                max(min_video_start, min(speech_start, current_time + min_video_start))
                if current_time == 0
                else speech_start
            )

            # Add video segment before this speech segment
            if video_start < video_end:
                timeline_items.append(("video", video_start, video_end - video_start))

            # Add blank segment for this speech (but only if it doesn't overlap with our minimum video)
            blank_start = max(video_end, speech_start)
            if blank_start < speech_end:
                timeline_items.append(("blank", blank_start, speech_end - blank_start, i))
            current_time = speech_end

        # Add final video segment if needed
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(input_video)]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        import json

        video_info = json.loads(result.stdout)
        total_duration = float(video_info["format"]["duration"])

        if current_time < total_duration:
            timeline_items.append(("video", current_time, total_duration - current_time))

        # Create concat list using the same approach as video merger
        concat_list_path = temp_dir / "concat_list.txt"
        with open(concat_list_path, "w") as f:
            for item in timeline_items:
                if item[0] == "video":
                    # Extract video segment
                    start_time, duration = item[1], item[2]
                    if duration > 0:  # Only add if duration > 0
                        video_segment_path = temp_dir / f"video_{start_time:.3f}_{duration:.3f}.mp4"
                        extract_cmd = [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(input_video),
                            "-ss",
                            str(start_time),
                            "-t",
                            str(duration),
                            "-c",
                            "copy",
                            str(video_segment_path),
                        ]
                        run_subprocess_with_encoding(extract_cmd, check=True)
                        f.write(f"file '{normalize_path_for_ffmpeg(video_segment_path)}'\n")
                elif item[0] == "blank":
                    # Use pre-created blank video
                    blank_index = item[3]
                    blank_video = blank_videos[blank_index]
                    f.write(f"file '{normalize_path_for_ffmpeg(blank_video)}'\n")

        # Use concat demuxer with video copy but audio re-encode for reliability
        concat_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-c:v",
            "copy",  # Keep video stream copy for speed
            "-c:a",
            "aac",  # Re-encode audio for consistency
            "-b:a",
            "128k",  # Set audio bitrate
            str(output_path),
        ]

        # Debug: Print the concat list contents
        print("=== DEBUG: Concat list contents ===")
        with open(concat_list_path, "r") as f:
            print(f.read())
        print("=== END DEBUG ===")

        print("Concatenating segments...")
        run_subprocess_with_encoding(concat_cmd, check=True)

    finally:
        # Clean up temporary directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def compress_video(input_path, output_path=None, quality="23", preset="medium", max_width=1920):
    """
    Compress a video to H.264 with smaller file size.

    Args:
        input_path: Path to input video file
        output_path: Path for output file (optional, will auto-generate if not provided)
        quality: CRF value for quality (lower = better quality, higher file size)
        preset: Encoding preset (ultrafast, fast, medium, slow, veryslow)
        max_width: Maximum width for the output video (maintains aspect ratio)
    """
    from pathlib import Path

    input_path = Path(input_path)

    if not output_path:
        output_path = input_path.parent / f"{input_path.stem}_compressed{input_path.suffix}"
    else:
        output_path = Path(output_path)

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Compressing {input_path.name} to {output_path.name}...")
    print(f"Settings: CRF={quality}, preset={preset}, max_width={max_width}")

    # Build ffmpeg command for compression
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-c:v",
        "libx264",  # H.264 video codec
        "-crf",
        str(quality),  # Quality setting (18-28 typical range)
        "-preset",
        preset,  # Encoding speed/compression trade-off
        "-c:a",
        "aac",  # AAC audio codec
        "-b:a",
        "128k",  # Audio bitrate
        "-movflags",
        "+faststart",  # Optimize for web streaming
        "-pix_fmt",
        "yuv420p",  # Ensure compatibility
    ]

    # Add scaling filter if max_width is specified
    if max_width:
        scale_filter = f"scale='min({max_width},iw):-2'"
        ffmpeg_cmd.extend(["-vf", scale_filter])

    # Add output path and overwrite flag
    ffmpeg_cmd.extend([str(output_path), "-y"])

    try:
        run_subprocess_with_encoding(ffmpeg_cmd, check=True)

        # Get file sizes for comparison
        original_size = input_path.stat().st_size
        compressed_size = output_path.stat().st_size
        compression_ratio = (1 - compressed_size / original_size) * 100

        print("Compression complete!")
        print(f"Original size: {original_size / (1024**2):.1f} MB")
        print(f"Compressed size: {compressed_size / (1024**2):.1f} MB")
        print(f"Size reduction: {compression_ratio:.1f}%")

        return output_path

    except subprocess.CalledProcessError as e:
        print(f"Error during compression: {e}")
        raise
