import subprocess
from .utils import (
    EFFECT_CONFIGS,
    run_subprocess_with_encoding,
    mmss_to_seconds,
    AUDIO_CODEC,
    AUDIO_BITRATE_STANDARD,
    AUDIO_BITRATE_HIGH,
)


def detect_nvidia_encoder():
    """Detect if NVIDIA NVENC encoder is available."""
    try:
        # Check if nvidia-smi is available (indicates NVIDIA GPU)
        subprocess.run(["nvidia-smi"], capture_output=True, check=True)

        # Check if FFmpeg supports h264_nvenc
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True)
        return "h264_nvenc" in result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_optimal_encoder_settings():
    """Get optimal encoder settings based on available hardware."""
    if detect_nvidia_encoder():
        return {
            "video_codec": "h264_nvenc",
            "preset": "fast",  # NVENC presets: slow, medium, fast, hp, hq, bd, ll, llhq, llhp, lossless
            "extra_params": ["-gpu", "0", "-rc", "vbr"],  # Variable bitrate for better quality
        }
    else:
        return {"video_codec": "libx264", "preset": "fast", "extra_params": []}


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

    # For large numbers of segments, use multiple volume filters in chain
    # to avoid command line length limits
    if len(mute_segments) > 30:
        # Chain multiple volume filters, each handling a subset of segments
        filters = []
        chunk_size = 25  # Process segments in chunks of 25

        for i in range(0, len(mute_segments), chunk_size):
            chunk = mute_segments[i : i + chunk_size]
            enable_conditions = []
            for start, end in chunk:
                enable_conditions.append(f"between(t,{start},{end})")
            combined_condition = "+".join(enable_conditions)
            filters.append(f"volume=0:enable='{combined_condition}'")

        # Chain all volume filters together
        return ",".join(filters)
    else:
        # Original approach for smaller numbers of segments
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

    # For large numbers of segments, limit the complexity to avoid command line issues
    if len(black_segments) > 30:
        # For many segments, create a single drawbox filter with complex enable condition
        # Split into chunks to avoid overly long command lines
        chunk_size = 25
        filters = []

        for i in range(0, len(black_segments), chunk_size):
            chunk = black_segments[i : i + chunk_size]
            enable_conditions = []
            for start, end in chunk:
                enable_conditions.append(f"between(t,{start},{end})")
            combined_condition = "+".join(enable_conditions)
            filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{combined_condition}'")

        return ",".join(filters)
    else:
        # Original approach for smaller numbers of segments
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

    # For large numbers of segments, use filter script file to avoid command line length limits
    total_segments = len(all_mute_segments) + len(all_black_segments)
    if total_segments > 30:
        _process_with_filter_script(input_path, output_path, all_mute_segments, all_black_segments)
    else:
        _process_with_inline_filters(input_path, output_path, all_mute_segments, all_black_segments)


def _process_with_filter_script(input_path, output_path, mute_segments, black_segments):
    """Process media using FFmpeg filter script file to avoid command line length limits."""
    import tempfile
    import os

    print(
        f"Using filter script approach for {len(mute_segments)} mute segments and {len(black_segments)} black segments..."
    )

    # Get optimal encoder settings
    encoder_settings = get_optimal_encoder_settings()

    # Create temporary filter script file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as filter_file:
        filter_script_path = filter_file.name

        # Write filter graph to file
        filter_lines = []

        # Start with input
        current_label = "[0:v]"
        audio_label = "[0:a]"

        # Apply video filters if needed
        if black_segments:
            video_filters = []
            for start, end in black_segments:
                video_filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{start},{end})'")

            filter_lines.append(f"{current_label}{','.join(video_filters)}[v_filtered]")
            current_label = "[v_filtered]"

        # Apply audio filters if needed
        if mute_segments:
            # Create multiple volume filters for large numbers of segments
            chunk_size = 25
            temp_labels = []

            for i in range(0, len(mute_segments), chunk_size):
                chunk = mute_segments[i : i + chunk_size]
                enable_conditions = []
                for start, end in chunk:
                    enable_conditions.append(f"between(t,{start},{end})")
                combined_condition = "+".join(enable_conditions)

                if i == 0:
                    input_label = audio_label
                else:
                    input_label = f"[a_temp{i - chunk_size}]"

                if i + chunk_size >= len(mute_segments):
                    output_label = "[a_filtered]"
                else:
                    output_label = f"[a_temp{i}]"
                    temp_labels.append(output_label)

                filter_lines.append(f"{input_label}volume=0:enable='{combined_condition}'{output_label}")

            audio_label = "[a_filtered]"

        # Write all filter lines to file
        filter_file.write(";\n".join(filter_lines))

    try:
        # Build FFmpeg command using filter script
        ffmpeg_cmd = ["ffmpeg", "-i", str(input_path)]

        # Use filter_complex_script to read filters from file
        ffmpeg_cmd.extend(["-filter_complex_script", filter_script_path])

        # Map outputs
        if black_segments:
            ffmpeg_cmd.extend(["-map", "[v_filtered]"])
        else:
            ffmpeg_cmd.extend(["-map", "0:v"])

        if mute_segments:
            ffmpeg_cmd.extend(["-map", "[a_filtered]"])
        else:
            ffmpeg_cmd.extend(["-map", "0:a"])

        # Set codecs
        if black_segments:
            ffmpeg_cmd.extend(["-c:v", encoder_settings["video_codec"], "-preset", encoder_settings["preset"]])
            ffmpeg_cmd.extend(encoder_settings["extra_params"])
        else:
            ffmpeg_cmd.extend(["-c:v", "copy"])

        if mute_segments:
            ffmpeg_cmd.extend(["-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE_HIGH])
        else:
            ffmpeg_cmd.extend(["-c:a", "copy"])

        ffmpeg_cmd.extend([str(output_path), "-y"])

        if encoder_settings["video_codec"] == "h264_nvenc":
            print("Using NVIDIA GPU encoding (NVENC)")

        run_subprocess_with_encoding(ffmpeg_cmd, check=True)

    finally:
        # Clean up temporary filter script file
        if os.path.exists(filter_script_path):
            os.unlink(filter_script_path)


def _process_with_inline_filters(input_path, output_path, mute_segments, black_segments):
    """Process media using inline filters for smaller numbers of segments."""
    # Get optimal encoder settings
    encoder_settings = get_optimal_encoder_settings()

    audio_filter = create_audio_filter(mute_segments)
    video_filter = create_video_filter(black_segments)
    ffmpeg_cmd = ["ffmpeg", "-i", str(input_path)]

    if video_filter:
        # Use GPU encoding when video processing is needed
        ffmpeg_cmd.extend(
            ["-vf", video_filter, "-c:v", encoder_settings["video_codec"], "-preset", encoder_settings["preset"]]
        )
        ffmpeg_cmd.extend(encoder_settings["extra_params"])
    else:
        ffmpeg_cmd.extend(["-c:v", "copy"])

    if audio_filter:
        ffmpeg_cmd.extend(["-af", audio_filter, "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE_HIGH])
    else:
        ffmpeg_cmd.extend(["-c:a", "copy"])

    ffmpeg_cmd.extend([str(output_path), "-y"])

    if encoder_settings["video_codec"] == "h264_nvenc":
        print("Using NVIDIA GPU encoding (NVENC)")

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


def apply_blank_video_to_segments(input_video, output_path, speech_segments, blank_video_path, timeline_path=None):
    """
    Replace speech segments in video with blank video using filter approach for efficiency.
    When there are many segments, this avoids the "Argument list too long" error by using
    a single FFmpeg command with a complex filter instead of concat demuxer.

    Args:
        input_video: Path to input video
        output_path: Path for output video
        speech_segments: List of (start_time, end_time) tuples for speech segments
        blank_video_path: Path to blank video file
        timeline_path: Optional path to existing timeline JSON to update with VideoRemoved segments
    """
    print(f"Applying blank video to {len(speech_segments)} speech segments...")

    # Sort speech segments by start time
    speech_segments = sorted(speech_segments, key=lambda x: x[0])

    if len(speech_segments) > 50:
        # For large numbers of segments, use the optimized filter-based approach
        _apply_blank_video_filter_method(input_video, output_path, speech_segments, blank_video_path, timeline_path)
    else:
        # For smaller numbers, use the original concat method
        _apply_blank_video_concat_method(input_video, output_path, speech_segments, blank_video_path, timeline_path)


def _apply_blank_video_filter_method(input_video, output_path, speech_segments, blank_video_path, timeline_path=None):
    """
    Use FFmpeg filter approach for large numbers of segments to avoid argument list length issues.
    """
    print("Using filter-based method for large number of segments...")

    # Create a drawbox filter that blacks out all speech segments
    black_filters = []
    for start_time, end_time in speech_segments:
        black_filters.append(
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{start_time},{end_time})'"
        )

    # Combine all black filters
    video_filter = ",".join(black_filters)

    # Create mute filter for the same segments
    mute_conditions = []
    for start_time, end_time in speech_segments:
        mute_conditions.append(f"between(t,{start_time},{end_time})")

    audio_filter = f"volume=0:enable='{'+'.join(mute_conditions)}'"

    # Single FFmpeg command with complex filters
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-vf",
        video_filter,
        "-af",
        audio_filter,
        "-c:v",
        "libx264",  # Need to re-encode when using filters
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        AUDIO_CODEC,
        "-b:a",
        AUDIO_BITRATE_STANDARD,
        str(output_path),
    ]

    print("Processing with complex filters...")
    run_subprocess_with_encoding(cmd, check=True)

    # Add VideoRemoved segments to timeline if path provided
    if timeline_path and timeline_path.exists():
        from .video_merger import add_video_removed_to_timeline

        add_video_removed_to_timeline(timeline_path, speech_segments)


def _apply_blank_video_concat_method(input_video, output_path, speech_segments, blank_video_path, timeline_path=None):
    """
    Original concat demuxer method for smaller numbers of segments.
    """
    from .video_merger import create_gap_video_from_blank
    import shutil

    # Create temporary directory for processing
    temp_dir = input_video.parent / "temp_blank_processing"
    temp_dir.mkdir(exist_ok=True)

    try:
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

        # Process video segments
        video_segments_to_extract = []
        with open(concat_list_path, "w") as f:
            for item in timeline_items:
                if item[0] == "video":
                    # Extract video segment
                    start_time, duration = item[1], item[2]
                    if duration > 0:  # Only add if duration > 0
                        video_segment_path = temp_dir / f"video_{start_time:.3f}_{duration:.3f}.mp4"
                        video_segments_to_extract.append((start_time, duration, video_segment_path))
                        f.write(f"file '{video_segment_path.resolve()}'\n")
                elif item[0] == "blank":
                    # Use pre-created blank video
                    blank_index = item[3]
                    blank_video = blank_videos[blank_index]
                    f.write(f"file '{blank_video.resolve()}'\n")

        # Extract all video segments
        for start_time, duration, video_segment_path in video_segments_to_extract:
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
            AUDIO_CODEC,  # Re-encode audio for consistency
            "-b:a",
            AUDIO_BITRATE_STANDARD,  # Set audio bitrate
            str(output_path),
        ]

        print("Concatenating segments...")
        run_subprocess_with_encoding(concat_cmd, check=True)

        # Add VideoRemoved segments to timeline if path provided
        if timeline_path and timeline_path.exists():
            from .video_merger import add_video_removed_to_timeline

            add_video_removed_to_timeline(timeline_path, speech_segments)

    finally:
        # Clean up temporary directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def compress_video(input_path, output_path=None, quality="23", preset="medium", max_width=1920):
    """
    Compress a video to H.264 with smaller file size using GPU acceleration when available.

    Args:
        input_path: Path to input video file
        output_path: Path for output file (optional, will auto-generate if not provided)
        quality: CRF value for quality (lower = better quality, higher file size)
        preset: Encoding preset (varies by encoder)
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

    # Get optimal encoder settings
    encoder_settings = get_optimal_encoder_settings()

    print(f"Compressing {input_path.name} to {output_path.name}...")
    print(f"Encoder: {encoder_settings['video_codec']}, preset: {encoder_settings['preset']}")
    if encoder_settings["video_codec"] == "h264_nvenc":
        print("Using NVIDIA GPU acceleration (NVENC)")
        print(f"Quality: CQ={quality} (NVENC quality scale)")
    else:
        print(f"Quality: CRF={quality} (CPU encoding)")

    # Build ffmpeg command for compression
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-c:v",
        encoder_settings["video_codec"],
    ]

    # Add encoder-specific quality settings
    if encoder_settings["video_codec"] == "h264_nvenc":
        # NVENC uses different quality parameters
        ffmpeg_cmd.extend(
            [
                "-cq",
                str(quality),  # Constant Quality mode for NVENC
                "-preset",
                encoder_settings["preset"],
            ]
        )
        ffmpeg_cmd.extend(encoder_settings["extra_params"])
    else:
        # libx264 uses CRF
        ffmpeg_cmd.extend(
            [
                "-crf",
                str(quality),
                "-preset",
                encoder_settings["preset"],
            ]
        )

    # Add common encoding parameters
    ffmpeg_cmd.extend(
        [
            "-c:a",
            AUDIO_CODEC,  # AAC audio codec
            "-b:a",
            AUDIO_BITRATE_STANDARD,  # Audio bitrate
            "-movflags",
            "+faststart",  # Optimize for web streaming
            "-pix_fmt",
            "yuv420p",  # Ensure compatibility
        ]
    )

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
