import subprocess
from .utils import (
    EFFECT_CONFIGS,
    run_subprocess_with_encoding,
    mmss_to_seconds,
    normalize_path_for_ffmpeg,
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
    Replace speech segments in video with blank video using efficient stream copy approach.

    Args:
        input_video: Path to input video
        output_path: Path for output video
        speech_segments: List of (start_time, end_time) tuples for speech segments
        blank_video_path: Path to blank video file
    """
    from .video_merger import create_gap_video_from_blank
    import shutil

    print(f"Applying blank video to {len(speech_segments)} speech segments...")

    # Create temporary directory for processing
    temp_dir = input_video.parent / "temp_blank_processing"
    temp_dir.mkdir(exist_ok=True)

    try:
        # Get video duration
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(input_video)]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        import json

        video_info = json.loads(result.stdout)
        total_duration = float(video_info["format"]["duration"])

        # Sort speech segments by start time
        speech_segments = sorted(speech_segments, key=lambda x: x[0])

        # Create timeline with alternating video and blank segments
        timeline = []
        current_time = 0.0

        for i, (speech_start, speech_end) in enumerate(speech_segments):
            # Add video segment before speech (if any)
            if current_time < speech_start:
                video_segment_path = temp_dir / f"video_{i}_before.mp4"
                duration = speech_start - current_time

                # Extract video segment
                extract_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_video),
                    "-ss",
                    str(current_time),
                    "-t",
                    str(duration),
                    "-c",
                    "copy",
                    str(video_segment_path),
                ]
                run_subprocess_with_encoding(extract_cmd, check=True)
                timeline.append(("video", video_segment_path))

            # Add blank video for speech segment
            blank_segment_path = temp_dir / f"blank_{i}.mp4"
            speech_duration = speech_end - speech_start

            create_gap_video_from_blank(blank_video_path, blank_segment_path, speech_duration)
            timeline.append(("blank", blank_segment_path))

            current_time = speech_end

        # Add final video segment (if any)
        if current_time < total_duration:
            final_segment_path = temp_dir / "video_final.mp4"
            duration = total_duration - current_time

            extract_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_video),
                "-ss",
                str(current_time),
                "-t",
                str(duration),
                "-c",
                "copy",
                str(final_segment_path),
            ]
            run_subprocess_with_encoding(extract_cmd, check=True)
            timeline.append(("video", final_segment_path))

        # Create concat list
        concat_list_path = temp_dir / "concat_list.txt"
        with open(concat_list_path, "w") as f:
            for segment_type, segment_path in timeline:
                normalized_path = normalize_path_for_ffmpeg(segment_path)
                f.write(f"file '{normalized_path}'\n")

        # Concatenate all segments using stream copy
        concat_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-c",
            "copy",
            str(output_path),
        ]

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
