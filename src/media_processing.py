import subprocess
import concurrent.futures
import os
from .utils import (
    EFFECT_CONFIGS,
    run_subprocess_with_encoding,
    mmss_to_seconds,
    hhmmss_to_seconds,
    AUDIO_CODEC,
    AUDIO_BITRATE_STANDARD,
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

    # Use single-pass audio filtering to preserve exact duration
    if all_mute_segments and not all_black_segments:
        print(f"Using single-pass audio filtering for {len(all_mute_segments)} mute segments...")
        _process_with_audio_filtering(input_path, output_path, all_mute_segments)
    else:
        # Fall back to concat method for complex effects (black video, etc.)
        total_segments = len(all_mute_segments) + len(all_black_segments)
        print(f"Using concat method for {total_segments} segments...")
        _process_with_concat_method_optimized(input_path, output_path, all_mute_segments, all_black_segments)


def _process_with_audio_filtering(input_path, output_path, mute_segments):
    """Process media using single-pass audio filtering to preserve exact duration."""
    from pathlib import Path
    import os

    input_path = Path(input_path)
    output_path = Path(output_path)

    print(f"Applying audio muting to {len(mute_segments)} segments using single-pass filtering...")

    # Build audio filter string for muting specific time ranges
    audio_filters = []
    for start_time, end_time in mute_segments:
        # Use FFmpeg's volume filter with enable condition to mute during time range
        audio_filters.append(f"volume=enable='between(t,{start_time:.6f},{end_time:.6f})':volume=0")

    # Combine all filters with comma separator
    filter_string = ",".join(audio_filters)

    # Check if filter string would exceed command line limits (use temp file if too long)
    estimated_cmd_length = len(filter_string) + 200  # Base command overhead
    max_cmd_length = 8000  # Conservative Windows command line limit

    if estimated_cmd_length > max_cmd_length:
        print(f"Filter string too long ({estimated_cmd_length} chars), using temporary filter file...")

        # Create tmp directory in same location as input video
        tmp_dir = input_path.parent / "tmp"
        tmp_dir.mkdir(exist_ok=True)

        # Write filter to temporary file to avoid command line length limits
        filter_file_path = tmp_dir / "audio_filter.txt"
        with open(filter_file_path, "w", encoding="utf-8") as f:
            f.write(f"[0:a]{filter_string}[outa]")

        try:
            # Build FFmpeg command using filter_complex_script
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-filter_complex_script",
                str(filter_file_path),
                "-map",
                "0:v",  # Map video stream
                "-map",
                "[outa]",  # Map filtered audio
                "-c:v",
                "copy",  # Stream copy video
                str(output_path),
            ]

            print("Processing entire video in single pass using filter script file...")
            run_subprocess_with_encoding(cmd, check=True)

        finally:
            # Clean up temporary filter file
            try:
                filter_file_path.unlink()
            except OSError:
                pass
    else:
        # Use direct command line approach for shorter filter strings
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-c:v",
            "copy",  # Stream copy video (no re-encoding, preserves exact duration)
            "-af",
            filter_string,  # Apply audio filters to mute specific time ranges
            str(output_path),
        ]

        print("Processing entire video in single pass (no segment extraction)...")
        run_subprocess_with_encoding(cmd, check=True)

    print(f"Successfully created output with exact duration preservation: {output_path}")


def _process_with_concat_method_optimized(input_path, output_path, mute_segments, black_segments):
    """Optimized concat method with segment merging and progress tracking."""
    from pathlib import Path
    import shutil
    import json
    from .utils import MultiStepProgressTracker

    print(f"Processing with concat method: {len(mute_segments)} mute segments, {len(black_segments)} black segments...")

    # Convert segment tuples to the format expected by merge function: (start, end, label, effect_type)
    all_segments = []
    for start, end in mute_segments:
        all_segments.append((start, end, "mute_audio", "mute"))
    for start, end in black_segments:
        all_segments.append((start, end, "black_video", "black"))

    # Apply consecutive segment merging for performance
    if len(all_segments) > 50:  # Only merge if we have many segments
        merged_segments = merge_consecutive_segments(all_segments)
        reduction = len(all_segments) - len(merged_segments)
        print(f"Merged {len(all_segments)} → {len(merged_segments)} segments (reduced by {reduction})")

        # Calculate estimated time
        estimated_minutes = len(merged_segments) * 0.5  # ~0.5s per merged segment
        if estimated_minutes > 120:
            print(
                f"Estimated processing time: {estimated_minutes / 60:.1f} hours for {len(merged_segments)} merged segments"
            )
        else:
            print(
                f"Estimated processing time: {estimated_minutes:.0f} minutes for {len(merged_segments)} merged segments"
            )
    else:
        merged_segments = [(s, e, [label], effect_type, 1) for s, e, label, effect_type in all_segments]
        print(f"Processing {len(merged_segments)} segments (no merging needed for small count)")

    # Use input seeking (-ss before -i) to prevent duration drift
    # Disable keyframe alignment approach - use traditional with precision fix
    use_keyframe_alignment = False
    print(f"Using input seeking extraction to prevent duration drift with {len(merged_segments)} segments")

    # Setup progress tracking
    progress_tracker = MultiStepProgressTracker(3, "Video Processing")
    progress_tracker.set_step_names(["Segment Extraction", "Processing", "Final Concatenation"])

    _process_segments_optimized(input_path, output_path, merged_segments, progress_tracker, use_keyframe_alignment)


def _process_segments_optimized(
    input_path, output_path, merged_segments, progress_tracker, use_keyframe_alignment=False
):
    """Process merged segments with optimization and progress tracking."""
    from pathlib import Path
    import shutil
    import json

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Create temporary directory for processing
    temp_dir = input_path.parent / "temp_optimized_processing"
    temp_dir.mkdir(exist_ok=True)

    try:
        # Get video duration
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(input_path)]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        video_info = json.loads(result.stdout)
        total_duration = float(video_info["format"]["duration"])

        print("Phase 1/3: Building extraction tasks...")
        progress_tracker.update_step(0, 0.0)

        if use_keyframe_alignment:
            # Use keyframe-aligned extraction to prevent duration drift
            extraction_tasks, concat_list = _build_keyframe_aligned_tasks(
                input_path, merged_segments, total_duration, temp_dir
            )
        else:
            # Traditional per-segment extraction
            extraction_tasks, concat_list = _build_traditional_extraction_tasks(
                input_path, merged_segments, total_duration, temp_dir
            )

        # Execute extraction tasks with progress tracking
        successful_extractions = 0

        def extract_segment_task_simple(task):
            try:
                # Use input seeking (-ss before -i) for precise timestamp handling
                # This avoids keyframe alignment drift that causes duration loss
                cmd = [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(task["start"]),  # Input seeking for precision
                    "-i",
                    task["input"],
                    "-t",
                    str(task["duration"]),
                ]
                cmd.extend(task["params"])
                cmd.append(task["output"])

                subprocess.run(cmd, check=True)
                return True
            except Exception as e:
                print(f"Failed to extract segment {task['output']}: {e}")
                return False

        for i, task in enumerate(extraction_tasks):
            # Print progress every 20 segments
            if i % 20 == 0 or i == len(extraction_tasks) - 1:
                print(f"Processing segment {i + 1}/{len(extraction_tasks)}: {task['start']:.1f}s ({task['type']})")

            if extract_segment_task_simple(task):
                successful_extractions += 1

            # Update progress
            segment_progress = (i + 1) / len(extraction_tasks)
            progress_tracker.update_step(1, segment_progress)

        progress_tracker.complete_step(1)
        print(f"Completed extractions: {successful_extractions}/{len(extraction_tasks)}")

        # Phase 3: Final concatenation
        print("Phase 3/3: Final concatenation...")
        progress_tracker.update_step(2, 0.0)

        # Create concat file
        concat_file = temp_dir / "concat_list.txt"
        with open(concat_file, "w") as f:
            for file_path in concat_list:
                f.write(f"file '{file_path}'\n")

        # Concatenate all segments
        print(f"Concatenating {len(concat_list)} segments...")
        concat_cmd = [
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
            "copy",
            str(output_path),
        ]

        subprocess.run(concat_cmd, check=True)
        progress_tracker.complete_step(2)
        progress_tracker.complete()
        print(f"Successfully created output: {output_path}")

    finally:
        # Clean up temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def _process_with_concat_method(input_path, output_path, mute_segments, black_segments):
    """LEGACY: Process media using concat method to avoid command line length limits completely."""
    from pathlib import Path
    import shutil
    import json

    print(f"Processing with concat method: {len(mute_segments)} mute segments, {len(black_segments)} black segments...")

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Create temporary directory
    temp_dir = input_path.parent / "temp_concat_processing"
    temp_dir.mkdir(exist_ok=True)

    try:
        # Get video duration
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(input_path)]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        video_info = json.loads(result.stdout)
        total_duration = float(video_info["format"]["duration"])

        # Combine all segments and sort by start time
        all_segments = []
        for start, end in mute_segments:
            all_segments.append((start, end, "mute"))
        for start, end in black_segments:
            all_segments.append((start, end, "black"))

        all_segments.sort(key=lambda x: x[0])

        # Build timeline of segments to extract
        concat_list = []
        current_time = 0.0
        segment_index = 0

        for start_time, end_time, effect_type in all_segments:
            # Add unaffected video segment before this effect
            if current_time < start_time:
                duration = start_time - current_time
                segment_path = temp_dir / f"segment_{segment_index:04d}.mp4"

                extract_cmd = [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(input_path),
                    "-ss",
                    str(current_time),
                    "-t",
                    str(duration),
                    "-c",
                    "copy",
                    str(segment_path),
                ]
                run_subprocess_with_encoding(extract_cmd, check=True)
                concat_list.append(str(segment_path.resolve()))
                segment_index += 1

            # Add affected segment with processing
            duration = end_time - start_time
            segment_path = temp_dir / f"segment_{segment_index:04d}.mp4"

            extract_cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-ss",
                str(start_time),
                "-t",
                str(duration),
            ]

            # Apply effects based on type
            if effect_type == "mute":
                extract_cmd.extend(["-c:v", "copy", "-an"])  # Copy video, remove audio
            elif effect_type == "black":
                extract_cmd.extend(["-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill", "-c:a", "copy"])

            extract_cmd.append(str(segment_path))
            run_subprocess_with_encoding(extract_cmd, check=True)
            concat_list.append(str(segment_path.resolve()))
            segment_index += 1
            current_time = end_time

        # Add final unaffected segment if needed
        if current_time < total_duration:
            duration = total_duration - current_time
            segment_path = temp_dir / f"segment_{segment_index:04d}.mp4"

            extract_cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-ss",
                str(current_time),
                "-t",
                str(duration),
                "-c",
                "copy",
                str(segment_path),
            ]
            run_subprocess_with_encoding(extract_cmd, check=True)
            concat_list.append(str(segment_path.resolve()))

        # Create concat list file
        concat_file = temp_dir / "concat_list.txt"
        with open(concat_file, "w") as f:
            for file_path in concat_list:
                f.write(f"file '{file_path}'\n")

        # Concatenate all segments
        print(f"Concatenating {len(concat_list)} processed segments...")
        concat_cmd = [
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
            "copy",
            str(output_path),
        ]
        run_subprocess_with_encoding(concat_cmd, check=True)
        print(f"Successfully created: {output_path}")

    finally:
        # Clean up temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


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


def merge_consecutive_segments(segments):
    """
    Merge ONLY consecutive segments (no gaps) with same effect type.
    Preserves exact timeline timing while maximizing FFmpeg call reduction.

    Args:
        segments: List of (start, end, label, effect_type) tuples (must be sorted by start time)

    Returns:
        List of merged segments with format (start, end, labels, effect_type, segment_count)
    """
    if not segments:
        return []

    segments = sorted(segments, key=lambda x: x[0])  # Ensure sorted by start time
    merged = []
    current_start, current_end, current_label, current_type = segments[0]
    current_labels = [current_label]  # Track all merged labels
    segment_count = 1

    for start, end, label, effect_type in segments[1:]:
        # Check if this segment is consecutive AND same effect type
        # Consecutive means: gap between segments is 0 (or very close due to floating point)
        gap = start - current_end
        is_consecutive = abs(gap) < 0.01  # Allow tiny floating point differences
        same_effect = effect_type == current_type

        if is_consecutive and same_effect:
            # Merge: extend current segment to include this one
            current_end = end
            current_labels.append(label)
            segment_count += 1
        else:
            # Can't merge: save current merged segment and start new one
            merged.append((current_start, current_end, current_labels, current_type, segment_count))
            # Start new segment group
            current_start, current_end, current_type = start, end, effect_type
            current_labels = [label]
            segment_count = 1

    # Add final merged segment
    merged.append((current_start, current_end, current_labels, current_type, segment_count))

    return merged


def create_keyframe_aligned_chunks(merged_segments, total_duration, keyframe_interval=2.0):
    """
    Create keyframe-aligned extraction chunks to prevent duration drift.
    Groups segments into larger chunks that align with keyframe boundaries.

    Args:
        merged_segments: List of (start, end, labels, effect_type, segment_count) tuples
        total_duration: Total video duration in seconds
        keyframe_interval: Assumed keyframe interval in seconds (default: 2.0)

    Returns:
        List of keyframe-aligned chunks with format (keyframe_start, keyframe_end, contained_segments)
    """
    if not merged_segments:
        return []

    # Find keyframe boundaries that encompass all segments
    min_start = min(seg[0] for seg in merged_segments)
    max_end = max(seg[1] for seg in merged_segments)

    # Align to keyframe boundaries (round down for start, up for end)
    keyframe_start = (int(min_start // keyframe_interval)) * keyframe_interval
    keyframe_end = (int(max_end // keyframe_interval) + 1) * keyframe_interval

    # Don't exceed video duration
    keyframe_start = max(0, keyframe_start)
    keyframe_end = min(total_duration, keyframe_end)

    # For now, create one large chunk containing all segments
    # Future optimization: split into multiple chunks if range is too large (>30 seconds)
    chunk_duration = keyframe_end - keyframe_start
    if chunk_duration > 30.0:
        # Split into multiple keyframe-aligned chunks
        chunks = []
        current_start = keyframe_start

        while current_start < keyframe_end:
            chunk_end = min(current_start + 30.0, keyframe_end)
            chunk_end = (int(chunk_end // keyframe_interval) + 1) * keyframe_interval
            chunk_end = min(total_duration, chunk_end)

            # Find segments that overlap with this chunk
            chunk_segments = []
            for seg in merged_segments:
                seg_start, seg_end = seg[0], seg[1]
                if seg_start < chunk_end and seg_end > current_start:
                    chunk_segments.append(seg)

            if chunk_segments:
                chunks.append((current_start, chunk_end, chunk_segments))

            current_start = chunk_end

        return chunks
    else:
        # Single chunk for all segments
        return [(keyframe_start, keyframe_end, merged_segments)]


def _build_traditional_extraction_tasks(input_path, merged_segments, total_duration, temp_dir):
    """Build traditional per-segment extraction tasks (original logic)."""
    extraction_tasks = []
    concat_list = []
    current_time = 0.0

    for i, (start_seconds, end_seconds, labels, effect_type, segment_count) in enumerate(merged_segments):
        # Add original video segment before this effect segment
        if current_time < start_seconds:
            video_duration = start_seconds - current_time
            video_segment_path = temp_dir / f"video_{current_time:.3f}_{video_duration:.3f}.mp4"

            extraction_tasks.append(
                {
                    "type": "video",
                    "input": str(input_path),
                    "output": str(video_segment_path),
                    "start": current_time,
                    "duration": video_duration,
                    "params": ["-c", "copy"],
                }
            )
            concat_list.append(str(video_segment_path.resolve()))

        # Process effect segment
        segment_duration = end_seconds - start_seconds
        segment_index = len(concat_list)

        if effect_type == "mute":
            effect_segment_path = temp_dir / f"muted_{segment_index}.mp4"
            extraction_tasks.append(
                {
                    "type": "mute",
                    "input": str(input_path),
                    "output": str(effect_segment_path),
                    "start": start_seconds,
                    "duration": segment_duration,
                    "params": ["-c:v", "copy", "-an"],
                }
            )
            concat_list.append(str(effect_segment_path.resolve()))

        elif effect_type == "black":
            effect_segment_path = temp_dir / f"black_{segment_index}.mp4"
            extraction_tasks.append(
                {
                    "type": "black",
                    "input": str(input_path),
                    "output": str(effect_segment_path),
                    "start": start_seconds,
                    "duration": segment_duration,
                    "params": ["-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill", "-c:a", "copy"],
                }
            )
            concat_list.append(str(effect_segment_path.resolve()))

        current_time = end_seconds

    # Add final video segment if needed
    if current_time < total_duration:
        final_duration = total_duration - current_time
        final_segment_path = temp_dir / f"video_{current_time:.3f}_{final_duration:.3f}.mp4"

        extraction_tasks.append(
            {
                "type": "video",
                "input": str(input_path),
                "output": str(final_segment_path),
                "start": current_time,
                "duration": final_duration,
                "params": ["-c", "copy"],
            }
        )
        concat_list.append(str(final_segment_path.resolve()))

    return extraction_tasks, concat_list


def _build_keyframe_aligned_tasks(input_path, merged_segments, total_duration, temp_dir):
    """Build keyframe-aligned extraction tasks to prevent duration drift."""
    # Create keyframe-aligned chunks
    keyframe_chunks = create_keyframe_aligned_chunks(merged_segments, total_duration)

    extraction_tasks = []
    concat_list = []
    current_time = 0.0

    print(f"Created {len(keyframe_chunks)} keyframe-aligned chunks for {len(merged_segments)} segments")

    for chunk_idx, (chunk_start, chunk_end, chunk_segments) in enumerate(keyframe_chunks):
        # Add video before this chunk if needed
        if current_time < chunk_start:
            video_duration = chunk_start - current_time
            video_segment_path = temp_dir / f"video_{current_time:.3f}_{video_duration:.3f}.mp4"

            extraction_tasks.append(
                {
                    "type": "video",
                    "input": str(input_path),
                    "output": str(video_segment_path),
                    "start": current_time,
                    "duration": video_duration,
                    "params": ["-c", "copy"],
                }
            )
            concat_list.append(str(video_segment_path.resolve()))

        # Extract the entire keyframe-aligned chunk
        chunk_duration = chunk_end - chunk_start
        chunk_path = temp_dir / f"chunk_{chunk_idx}_{chunk_start:.3f}_{chunk_duration:.3f}.mp4"

        extraction_tasks.append(
            {
                "type": "keyframe_chunk",
                "input": str(input_path),
                "output": str(chunk_path),
                "keyframe_start": chunk_start,
                "keyframe_duration": chunk_duration,
                "keyframe_aligned": True,
                "contained_segments": chunk_segments,
            }
        )
        concat_list.append(str(chunk_path.resolve()))

        current_time = chunk_end

    # Add final video segment if needed
    if current_time < total_duration:
        final_duration = total_duration - current_time
        final_segment_path = temp_dir / f"video_{current_time:.3f}_{final_duration:.3f}.mp4"

        extraction_tasks.append(
            {
                "type": "video",
                "input": str(input_path),
                "output": str(final_segment_path),
                "start": current_time,
                "duration": final_duration,
                "params": ["-c", "copy"],
            }
        )
        concat_list.append(str(final_segment_path.resolve()))

    return extraction_tasks, concat_list


def _apply_blank_video_concat_method(input_video, output_path, speech_segments, blank_video_path, timeline_path=None):
    """
    Optimized concat method that merges adjacent segments to reduce FFmpeg calls.
    Performance improvement: 7000+ segments -> ~100-500 segments via merging.
    """
    from .video_merger import create_gap_video_from_blank
    from .utils import load_timeline, MultiStepProgressTracker
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
        # Get total video duration
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(input_video)]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        import json

        video_info = json.loads(result.stdout)
        total_duration = float(video_info["format"]["duration"])

        # Extract segments that need effects applied (based on EFFECT_CONFIGS)
        privacy_segments = []
        for segment in timeline_segments:
            # Check if segment has type: "all" OR has a label that requires effects
            segment_type = segment.get("type")
            segment_label = segment.get("label", "")

            should_process = False
            effect_type = "blank"  # Default to blank replacement

            if segment_type == "all":
                should_process = True
                effect_type = "blank"
            elif segment_label in EFFECT_CONFIGS:
                effects = EFFECT_CONFIGS[segment_label]
                if effects.get("mute_audio") or effects.get("black_video"):
                    should_process = True
                    # Determine effect type based on config
                    if effects.get("mute_audio") and effects.get("black_video"):
                        effect_type = "blank"  # Full blank replacement
                    elif effects.get("mute_audio"):
                        effect_type = "mute"  # Audio removal only
                    elif effects.get("black_video"):
                        effect_type = "black"  # Video blackout only

            if should_process:
                start_seconds = hhmmss_to_seconds(segment["start"])
                end_seconds = hhmmss_to_seconds(segment["end"])
                duration = end_seconds - start_seconds

                # Skip segments that are too short
                if duration < 0.1:
                    continue  # Skip silently

                privacy_segments.append((start_seconds, end_seconds, segment_label, effect_type))

        print(f"Found {len(privacy_segments)} privacy segments before merging")

        # PERFORMANCE OPTIMIZATION: Merge consecutive segments to reduce FFmpeg calls
        # This preserves exact timeline timing while maximizing efficiency
        merged_segments = merge_consecutive_segments(privacy_segments)
        reduction = len(privacy_segments) - len(merged_segments)
        print(f"Merged {len(privacy_segments)} → {len(merged_segments)} segments (reduced by {reduction})")

        # Calculate total expected processing time for user feedback
        estimated_minutes = len(merged_segments) * 0.8  # ~0.8s per merged segment average
        if estimated_minutes > 120:
            print(
                f"Estimated processing time: {estimated_minutes / 60:.1f} hours for {len(merged_segments)} merged segments"
            )
        else:
            print(
                f"Estimated processing time: {estimated_minutes:.0f} minutes for {len(merged_segments)} merged segments"
            )

        if not merged_segments:
            print("No privacy segments to process - copying original file")
            shutil.copy(input_video, output_path)
            return

        # Setup progress tracking for 3 phases with detailed feedback
        progress_tracker = MultiStepProgressTracker(3, "Video Processing")
        progress_tracker.set_step_names(["Segment Extraction", "Blank Creation", "Final Concatenation"])

        print("Phase 1/3: Building extraction tasks...")
        progress_tracker.update_step(0, 0.0)

        # Build optimized extraction tasks for parallel processing
        extraction_tasks = []
        concat_list = []
        current_time = 0.0
        blank_index = 0

        for i, (start_seconds, end_seconds, labels, effect_type, segment_count) in enumerate(merged_segments):
            # Add original video segment before this privacy segment
            if current_time < start_seconds:
                video_duration = start_seconds - current_time
                video_segment_path = temp_dir / f"video_{current_time:.3f}_{video_duration:.3f}.mp4"

                # Add to extraction tasks for parallel processing
                extraction_tasks.append(
                    {
                        "type": "video",
                        "input": str(input_video),
                        "output": str(video_segment_path),
                        "start": current_time,
                        "duration": video_duration,
                        "params": ["-c", "copy"],  # Stream copy for speed
                    }
                )
                concat_list.append(str(video_segment_path.resolve()))

            # Process merged segment based on effect type
            segment_duration = end_seconds - start_seconds
            segment_label_str = f"{segment_count} segments: {', '.join(set(labels))}"

            if effect_type == "blank":
                # Create blank segment for full privacy removal
                blank_segment_path = temp_dir / f"blank_{blank_index}.mp4"
                print(f"Creating blank video {blank_index} for {segment_duration:.3f}s ({segment_label_str})")
                create_gap_video_from_blank(blank_video_path, blank_segment_path, segment_duration)

                if not blank_segment_path.exists() or blank_segment_path.stat().st_size == 0:
                    raise RuntimeError(f"Failed to create blank video: {blank_segment_path}")

                concat_list.append(str(blank_segment_path.resolve()))
                blank_index += 1

            elif effect_type == "mute":
                # Extract merged segment with muted audio
                muted_segment_path = temp_dir / f"muted_{blank_index}.mp4"
                print(f"Creating muted segment {blank_index} for {segment_duration:.3f}s ({segment_label_str})")

                extraction_tasks.append(
                    {
                        "type": "mute",
                        "input": str(input_video),
                        "output": str(muted_segment_path),
                        "start": start_seconds,
                        "duration": segment_duration,
                        "params": ["-c:v", "copy", "-an"],  # Keep video, remove audio
                    }
                )
                concat_list.append(str(muted_segment_path.resolve()))
                blank_index += 1

            elif effect_type == "black":
                # Extract merged segment with blacked out video
                black_segment_path = temp_dir / f"black_{blank_index}.mp4"
                print(f"Creating black video segment {blank_index} for {segment_duration:.3f}s ({segment_label_str})")

                extraction_tasks.append(
                    {
                        "type": "black",
                        "input": str(input_video),
                        "output": str(black_segment_path),
                        "start": start_seconds,
                        "duration": segment_duration,
                        "params": ["-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill", "-c:a", "copy"],
                    }
                )
                concat_list.append(str(black_segment_path.resolve()))
                blank_index += 1

            current_time = end_seconds

            # Update progress during planning phase
            progress = (i + 1) / len(merged_segments) * 0.3  # 30% of step 1 is planning
            progress_tracker.update_step(0, progress)

        # Add final video segment if needed
        if current_time < total_duration:
            final_duration = total_duration - current_time
            final_segment_path = temp_dir / f"video_{current_time:.3f}_{final_duration:.3f}.mp4"

            extraction_tasks.append(
                {
                    "type": "video",
                    "input": str(input_video),
                    "output": str(final_segment_path),
                    "start": current_time,
                    "duration": final_duration,
                    "params": ["-c", "copy"],
                }
            )
            concat_list.append(str(final_segment_path.resolve()))

        print(f"Phase 1/3: Processing {len(extraction_tasks)} extraction tasks in parallel...")

        # PERFORMANCE OPTIMIZATION: Parallel extraction with optimized FFmpeg parameters
        def extract_segment_task(task):
            """Execute a single extraction task with optimized FFmpeg parameters."""
            try:
                # Optimized FFmpeg command with better stream copy performance
                cmd = [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-avoid_negative_ts",
                    "make_zero",  # Handle timestamp issues
                    "-fflags",
                    "+genpts",  # Generate presentation timestamps
                    "-i",
                    task["input"],
                    "-ss",
                    str(task["start"]),
                    "-t",
                    str(task["duration"]),
                ]
                cmd.extend(task["params"])
                cmd.append(task["output"])

                run_subprocess_with_encoding(cmd, check=True)
                return True
            except Exception as e:
                print(f"Failed to extract segment {task['output']}: {e}")
                return False

        # SMART EXTRACTION STRATEGY: Check if we should use parallel or sequential processing
        # For fewer segments or better I/O control, we can disable parallelism
        use_parallel = len(extraction_tasks) > 10  # Only parallelize if it's worth the overhead

        if use_parallel:
            print(f"Using parallel extraction (2 workers) for {len(extraction_tasks)} tasks")
            # Sort tasks by start time to minimize seek conflicts
            extraction_tasks.sort(key=lambda task: task["start"])

            max_workers = 2  # Conservative: just 2 workers to minimize I/O conflicts
            successful_extractions = 0

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_task = {executor.submit(extract_segment_task, task): task for task in extraction_tasks}

                # Process results as they complete
                for i, future in enumerate(concurrent.futures.as_completed(future_to_task)):
                    task = future_to_task[future]
                    try:
                        success = future.result()
                        if success:
                            successful_extractions += 1

                        # Print detailed progress every 10 segments
                        if (i + 1) % 10 == 0 or i == len(extraction_tasks) - 1:
                            print(
                                f"Extraction progress: {i + 1}/{len(extraction_tasks)} segments completed ({successful_extractions} successful)"
                            )

                        # Update progress (30% planning + 60% extraction)
                        extraction_progress = 0.3 + (i + 1) / len(extraction_tasks) * 0.6
                        progress_tracker.update_step(0, extraction_progress)

                    except Exception as e:
                        print(f"Extraction task failed: {e}")
        else:
            print(f"Using sequential extraction for {len(extraction_tasks)} tasks (better I/O control)")
            # Sequential processing - no file access conflicts
            successful_extractions = 0
            extraction_tasks.sort(key=lambda task: task["start"])  # Process in chronological order

            for i, task in enumerate(extraction_tasks):
                try:
                    # Print current segment being processed
                    if i % 10 == 0 or i == len(extraction_tasks) - 1:
                        print(
                            f"Processing segment {i + 1}/{len(extraction_tasks)}: {task['start']:.1f}-{task['start'] + task['duration']:.1f}s ({task['type']})"
                        )

                    success = extract_segment_task(task)
                    if success:
                        successful_extractions += 1

                    # Update progress (30% planning + 60% extraction)
                    extraction_progress = 0.3 + (i + 1) / len(extraction_tasks) * 0.6
                    progress_tracker.update_step(0, extraction_progress)

                except Exception as e:
                    print(f"Extraction task failed: {e}")

        progress_tracker.complete_step(0)
        print(f"Completed extractions: {successful_extractions}/{len(extraction_tasks)}")

        # Phase 2: Blank creation (already done above, just update progress)
        print("Phase 2/3: Blank video creation completed")
        progress_tracker.complete_step(1)

        # Phase 3: Final concatenation with progress tracking
        print("Phase 3/3: Final concatenation...")
        progress_tracker.update_step(2, 0.0)

        # Create concat file
        concat_file = temp_dir / "concat_list.txt"
        with open(concat_file, "w") as f:
            for file_path in concat_list:
                f.write(f"file '{file_path}'\n")

        # Save concat list for debugging (move to parent dir before cleanup)
        debug_concat_file = input_video.parent / "concat_list.txt"
        shutil.copy(concat_file, debug_concat_file)
        print(f"Concat list saved for verification: {debug_concat_file}")

        # Concatenate all segments with optimized parameters
        print(f"Concatenating {len(concat_list)} segments...")
        final_concat_cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-avoid_negative_ts",
            "make_zero",
            "-fflags",
            "+genpts",
            "-max_muxing_queue_size",
            "9999",  # Prevent queue overflow
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
        progress_tracker.complete_step(2)
        progress_tracker.complete()
        print(f"Successfully created output: {output_path}")

        # Add VideoRemoved segments to timeline if path provided
        if timeline_path and timeline_path.exists():
            from .video_merger import add_video_removed_to_timeline

            # Extract segments that were actually processed
            removed_segments_with_labels = []
            for start_seconds, end_seconds, label, effect_type in privacy_segments:
                if effect_type == "blank":
                    new_label = f"Removed {label}".strip() if label else "Removed"
                elif effect_type == "mute":
                    new_label = f"Muted {label}".strip() if label else "Muted"
                elif effect_type == "black":
                    new_label = f"Blacked {label}".strip() if label else "Blacked"
                else:
                    new_label = f"Processed {label}".strip() if label else "Processed"
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
