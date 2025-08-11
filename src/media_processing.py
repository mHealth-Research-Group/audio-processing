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
    from pathlib import Path

    print(
        f"Using filter script approach for {len(mute_segments)} mute segments and {len(black_segments)} black segments..."
    )

    # Get optimal encoder settings
    encoder_settings = get_optimal_encoder_settings()

    # Create local temp directory like the rest of the codebase does
    input_path = Path(input_path)
    local_temp_dir = input_path.parent / "tmp"
    local_temp_dir.mkdir(exist_ok=True)

    # Create temporary filter script file in local tmp directory
    filter_script_path = local_temp_dir / f"{input_path.stem}_filter_script.txt"

    try:
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
        with open(filter_script_path, "w") as filter_file:
            filter_file.write(";\n".join(filter_lines))

        # Build FFmpeg command using filter script
        ffmpeg_cmd = ["ffmpeg", "-i", str(input_path)]

        # Use the newer filter_complex syntax to avoid deprecated warning
        ffmpeg_cmd.extend(["-/filter_complex", str(filter_script_path)])

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
        if filter_script_path.exists():
            filter_script_path.unlink()
        # Clean up local temp directory if it exists and is empty
        if local_temp_dir.exists():
            try:
                local_temp_dir.rmdir()  # Only removes if empty
            except OSError:
                pass  # Directory not empty or other files exist


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


def apply_blank_video_to_segments(
    input_video, output_path, speech_segments, blank_video_path, timeline_path=None, trim_first_frame=True
):
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
        trim_first_frame: Whether to trim the first frame for privacy preservation (default: True)
    """
    from pathlib import Path

    print(f"Applying blank video to {len(speech_segments)} speech segments...")

    # Sort speech segments by start time
    speech_segments = sorted(speech_segments, key=lambda x: x[0])

    # For trimming, we need an intermediate output path
    if trim_first_frame:
        temp_output = Path(output_path).parent / f"temp_{Path(output_path).name}"
    else:
        temp_output = output_path

    # Always use concat method to avoid re-encoding (maintains original video quality)
    _apply_blank_video_concat_method(input_video, temp_output, speech_segments, blank_video_path, timeline_path)

    # Trim the first frame for privacy preservation if requested
    if trim_first_frame:
        print("Applying privacy trimming to remove first frame...")
        trim_first_frame_func(temp_output, output_path, first_frame_duration=0.033)

        # Clean up temporary file
        try:
            temp_output.unlink()
        except Exception as e:
            print(f"Warning: Could not remove temporary file {temp_output}: {e}")


def _apply_blank_video_concat_method(input_video, output_path, speech_segments, blank_video_path, timeline_path=None):
    """
    Simple concat method that replaces segments marked as type: 'all' with blank video.
    Works exactly like video merging with gap filling.
    """
    from .video_merger import create_gap_video_from_blank
    from .utils import load_timeline
    import shutil

    if not timeline_path or not timeline_path.exists():
        raise ValueError("Timeline path is required for proper segment processing")

    # Load complete timeline
    timeline_data = load_timeline(timeline_path)
    timeline_segments = timeline_data.get("timeline", [])

    if not timeline_segments:
        raise ValueError("No timeline segments found in timeline file")

    # Create temporary directory for processing
    temp_dir = input_video.parent / "temp_blank_processing"
    temp_dir.mkdir(exist_ok=True)

    try:

        def hhmmss_to_seconds(time_str):
            """Convert HH:MM:SS format to seconds."""
            parts = time_str.split(":")
            if len(parts) == 3:  # HH:MM:SS
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:  # MM:SS
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
            else:
                return float(time_str)

        # Get total video duration
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(input_video)]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        import json

        video_info = json.loads(result.stdout)
        total_duration = float(video_info["format"]["duration"])

        # Extract privacy segments (type: "all") and sort them
        privacy_segments = []
        for segment in timeline_segments:
            if segment.get("type") == "all":
                start_seconds = hhmmss_to_seconds(segment["start"])
                end_seconds = hhmmss_to_seconds(segment["end"])
                duration = end_seconds - start_seconds

                # Skip segments that are too short
                if duration < 0.1:
                    print(f"Warning: Skipping very short segment ({duration:.6f}s) - below minimum 0.1s threshold")
                    continue

                privacy_segments.append((start_seconds, end_seconds, segment.get("label", "")))

        privacy_segments.sort(key=lambda x: x[0])  # Sort by start time
        print(f"Found {len(privacy_segments)} privacy segments to process")

        if not privacy_segments:
            print("No privacy segments to process - copying original file")
            shutil.copy(input_video, output_path)
            return

        # Build simple concat list: alternate between video segments and blank segments
        concat_list = []
        current_time = 0.0
        blank_index = 0

        for start_seconds, end_seconds, label in privacy_segments:
            # Add original video segment before this privacy segment
            if current_time < start_seconds:
                video_duration = start_seconds - current_time
                video_segment_path = temp_dir / f"video_{current_time:.3f}_{video_duration:.3f}.mp4"

                # Extract video segment
                extract_cmd = [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(input_video),
                    "-ss",
                    str(current_time),
                    "-t",
                    str(video_duration),
                    "-c",
                    "copy",
                    str(video_segment_path),
                ]
                run_subprocess_with_encoding(extract_cmd, check=True)
                concat_list.append(str(video_segment_path.resolve()))

            # Add blank segment for privacy removal
            blank_duration = end_seconds - start_seconds
            blank_segment_path = temp_dir / f"blank_{blank_index}.mp4"

            print(f"Creating blank video {blank_index} for {blank_duration:.3f}s segment ({label})")
            create_gap_video_from_blank(blank_video_path, blank_segment_path, blank_duration)

            if not blank_segment_path.exists() or blank_segment_path.stat().st_size == 0:
                raise RuntimeError(f"Failed to create blank video: {blank_segment_path}")

            concat_list.append(str(blank_segment_path.resolve()))
            blank_index += 1
            current_time = end_seconds

        # Add final video segment if needed
        if current_time < total_duration:
            final_duration = total_duration - current_time
            final_segment_path = temp_dir / f"video_{current_time:.3f}_{final_duration:.3f}.mp4"

            extract_cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_video),
                "-ss",
                str(current_time),
                "-t",
                str(final_duration),
                "-c",
                "copy",
                str(final_segment_path),
            ]
            run_subprocess_with_encoding(extract_cmd, check=True)
            concat_list.append(str(final_segment_path.resolve()))

        # Create concat file
        concat_file = temp_dir / "concat_list.txt"
        with open(concat_file, "w") as f:
            for file_path in concat_list:
                f.write(f"file '{file_path}'\n")

        # Concatenate all segments
        print(f"Concatenating {len(concat_list)} segments...")
        final_concat_cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",  # Stream copy to maintain quality
            str(output_path),
        ]

        run_subprocess_with_encoding(final_concat_cmd, check=True)
        print(f"Successfully created output: {output_path}")

        # Add VideoRemoved segments to timeline if path provided
        if timeline_path and timeline_path.exists():
            from .video_merger import add_video_removed_to_timeline

            # Extract segments that were actually processed
            removed_segments_with_labels = []
            for start_seconds, end_seconds, label in privacy_segments:
                new_label = f"Removed {label}".strip() if label else "Removed"
                removed_segments_with_labels.append((start_seconds, end_seconds, new_label))

            add_video_removed_to_timeline(timeline_path, removed_segments_with_labels)

    finally:
        # Clean up temporary directory but preserve concat list
        if temp_dir.exists():
            # Move concat list to parent directory to preserve it
            final_concat_list = input_video.parent / "concat_list.txt"
            if concat_file.exists():
                shutil.move(str(concat_file), str(final_concat_list))
                print(f"Concat list preserved at: {final_concat_list}")

            # Remove temporary directory with all other files
            shutil.rmtree(temp_dir)


def trim_first_frame_func(input_video, output_path, first_frame_duration=0.033):
    """
    Trim the first frame from a video for privacy preservation.

    This function removes the first frame that was preserved during processing
    to maintain encoding compatibility. The duration removed should match the
    min_video_start value used during processing (typically 33ms).

    Args:
        input_video: Path to input video file
        output_path: Path for trimmed output video
        first_frame_duration: Duration to trim from start (in seconds)
    """
    from pathlib import Path

    input_video = Path(input_video)
    output_path = Path(output_path)

    print(f"Trimming first {first_frame_duration:.3f}s from video for privacy preservation...")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build ffmpeg command to trim the first frame
    trim_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-ss",
        str(first_frame_duration),  # Skip the first frame duration
        "-c",
        "copy",  # Use stream copy for speed - no re-encoding needed
        str(output_path),
    ]

    print("Removing first frame for privacy...")
    run_subprocess_with_encoding(trim_cmd, check=True)
    print(f"Privacy trimming complete: {output_path}")


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
