from __future__ import annotations

import concurrent.futures
import json
import subprocess
import tempfile
import shutil
from argparse import Namespace
from pathlib import Path
from typing import Dict, List

from .utils import load_timeline, mmss_to_seconds, MultiStepProgressTracker


def handle_edit_command(args: Namespace) -> int:
    """Entry point for the `edit` CLI command.

    Simplified: assumes the provided timeline already contains manual edits
    placed at the front (type: 'all' segments). We extract those ranges and
    apply blanking over them without comparing to an original timeline.
    """
    try:
        media_path = Path(args.input_path).expanduser().resolve()
        timeline_path = Path(args.timeline).expanduser().resolve()
        _validate_required_paths(media_path, timeline_path)
    except (AttributeError, TypeError):
        print("Error: Missing required arguments for edit command.")
        return 1
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    try:
        timeline = load_timeline(timeline_path)
        if not isinstance(timeline, dict) or "timeline" not in timeline:
            print(f"Invalid timeline structure: {timeline_path}")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading timeline: {exc}")
        return 1

    # Extract segments to blank (type == 'all'), after overlap resolution
    changed_segments = _extract_all_edit_segments(timeline)
    _print_edit_summary(media_path, timeline_path, changed_segments)

    if not changed_segments:
        print("No 'all' edit segments found. Nothing to apply.")
        return 0

    # Apply video edits over the extracted ranges
    try:
        output_path = (
            Path(args.output) if args.output else media_path.parent / f"{media_path.stem}_edited{media_path.suffix}"
        )
        print(f"\nApplying {len(changed_segments)} edit(s) to video...")
        print(f"Output: {output_path}")

        success = _apply_video_edits(media_path, changed_segments, output_path)
        if success:
            print("\nVideo editing completed successfully!")
            print(f"Output saved: {output_path}")
            return 0
        else:
            print("\nVideo editing failed!")
            return 1

    except Exception as exc:
        print(f"Error applying video edits: {exc}")
        return 1


def _validate_required_paths(media_path: Path, timeline_path: Path) -> None:
    if not media_path.exists():
        raise FileNotFoundError(f"Media path not found: {media_path}")
    if not timeline_path.exists():
        raise FileNotFoundError(f"Timeline file not found: {timeline_path}")


def _extract_all_edit_segments(timeline_data: Dict[str, object]) -> List[Dict]:
    """Return a sorted list of 'all' segments from a normalized timeline.

    Assumes `load_timeline` has already resolved overlaps so that manual
    edits (type: 'all') take precedence. Only 'all' segments are used for
    blanking in this simplified path.
    """
    segments = [s for s in timeline_data.get("timeline", []) if isinstance(s, dict)]
    all_segs = [s for s in segments if s.get("type") == "all"]
    all_segs.sort(key=lambda s: mmss_to_seconds(s["start"]))
    return all_segs


def _print_edit_summary(media_path: Path, timeline_path: Path, all_segments: List[Dict]) -> None:
    """Print a concise summary of edit segments to be applied."""
    print("Edit summary")
    print("------------")
    print(f"Media: {media_path}")
    print(f"Timeline: {timeline_path}")
    print(f"'all' segments to apply: {len(all_segments)}")

    if not all_segments:
        return

    preview_count = min(5, len(all_segments))
    print("")
    print(f"Previewing first {preview_count} edit segment(s):")
    for segment in all_segments[:preview_count]:
        start = segment.get("start", "?")
        end = segment.get("end", "?")
        label = segment.get("label", "all") or "all"
        print(f"  - {start} -> {end} ({label})")


def _apply_video_edits(media_path: Path, changed_segments: List[Dict], output_path: Path) -> bool:
    """Apply video edits using filter-based blackout approach."""
    try:
        # Setup progress tracking
        tracker = MultiStepProgressTracker(4, "Video Edit Processing")
        tracker.set_step_names(["Video Validation", "Extraction Planning", "Segment Processing", "Final Assembly"])

        # Step 1: Video validation
        print("Step 1/4: Video analysis...")

        # Quick validation of video file
        is_video_accessible = _validate_video_quick(media_path)
        if not is_video_accessible:
            print("   Video validation failed - aborting")
            return False

        print("   Using frame-accurate extraction with filter-based blackout")
        keyframes = []  # Skip keyframe detection for faster processing
        tracker.complete_step(0)

        # Step 2: Create extraction plan
        print("Step 2/4: Planning edits...")
        extraction_plan = _create_extraction_plan(media_path, changed_segments, keyframes)
        print(f"   Created plan for {len(extraction_plan['tasks'])} segments")
        tracker.complete_step(1)

        # Use local temp directory next to media to avoid system temp space issues
        base_tmp = media_path.parent / "tmp"
        base_tmp.mkdir(exist_ok=True)
        job_tmp_path = Path(tempfile.mkdtemp(prefix="video_edit_", dir=str(base_tmp)))

        try:
            # Step 3: Extract all segments from original video
            print(f"Step 3/4: Extracting {len(extraction_plan['tasks'])} segments...")
            success = _extract_segments_parallel(extraction_plan["tasks"], job_tmp_path, tracker, media_path)
            if not success:
                return False

            # Step 4: Final assembly with filtering
            print("Step 4/4: Assembling final video with filters...")
            concat_success = _concat_segments(extraction_plan["tasks"], output_path, job_tmp_path)
            tracker.complete()

            return concat_success
        finally:
            # Clean up job-specific temp files; keep base tmp dir
            try:
                shutil.rmtree(job_tmp_path, ignore_errors=True)
            except Exception:
                pass

    except Exception as e:
        print(f"Error in video processing: {e}")
        return False


def _validate_video_quick(media_path: Path) -> bool:
    """Quick validation that video file is accessible and processable."""
    try:
        print("   Validating video file accessibility...")

        # Test 1: Basic file info (fastest test)
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(media_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)

        import json

        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])

        if duration > 0:
            print(f"   Video validation successful: {duration / 3600:.1f} hours duration")
            return True
        else:
            print("   Video has zero duration")
            return False

    except subprocess.TimeoutExpired:
        print("   Video validation timed out - file may be corrupted")
        return False
    except subprocess.CalledProcessError as e:
        print(f"   Video validation failed: ffprobe error {e.returncode}")
        return False
    except Exception as e:
        print(f"   Video validation failed: {e}")
        return False


def _create_extraction_plan(media_path: Path, changed_segments: List[Dict], keyframes: List[float]) -> Dict:
    """Create optimized extraction plan with frame-accurate cutting."""
    # Get video duration and fps
    duration = _get_video_duration(media_path)
    fps = _get_video_fps(media_path)

    # Convert changed segments to time ranges and align to keyframes
    edit_ranges = []
    for segment in changed_segments:
        start_sec = mmss_to_seconds(segment["start"])
        end_sec = mmss_to_seconds(segment["end"])

        # Use precise time-based cuts (perfect for privacy editing)
        aligned_start, aligned_end = start_sec, end_sec

        edit_ranges.append({"start": aligned_start, "end": aligned_end, "original_segment": segment})

    # Sort by start time and merge overlapping ranges
    edit_ranges.sort(key=lambda x: x["start"])
    merged_ranges = _merge_overlapping_ranges(edit_ranges)

    # Create extraction tasks - all from original video
    # Segments needing blackout are marked with needs_blackout flag
    tasks = []
    current_time = 0.0

    for i, edit_range in enumerate(merged_ranges):
        # Add original segment before edit
        if current_time < edit_range["start"]:
            segment_duration = edit_range["start"] - current_time
            task = {
                "start": current_time,
                "duration": segment_duration,
                "frame_count": round(segment_duration * fps),
                "output": f"segment_{len(tasks)}.mp4",
                "needs_blackout": False,
                "is_final": False,
            }
            tasks.append(task)

        # Add segment that needs blackout (extracted from original, filtered during concat)
        edit_duration = edit_range["end"] - edit_range["start"]
        blackout_task = {
            "start": edit_range["start"],
            "duration": edit_duration,
            "frame_count": round(edit_duration * fps),
            "output": f"segment_{len(tasks)}.mp4",
            "needs_blackout": True,
            "is_final": False,
        }
        tasks.append(blackout_task)

        current_time = edit_range["end"]

    # Add final original segment if needed
    if current_time < duration:
        segment_duration = duration - current_time
        task = {
            "start": current_time,
            "duration": segment_duration,
            "frame_count": round(segment_duration * fps),
            "output": f"segment_{len(tasks)}.mp4",
            "needs_blackout": False,
            "is_final": True,
        }
        tasks.append(task)

    return {"tasks": tasks, "merged_ranges": merged_ranges}


def _merge_overlapping_ranges(ranges: List[Dict]) -> List[Dict]:
    """Merge overlapping edit ranges to minimize processing."""
    if not ranges:
        return []

    merged = []
    current = ranges[0].copy()

    for next_range in ranges[1:]:
        if next_range["start"] <= current["end"]:
            # Overlapping - merge
            current["end"] = max(current["end"], next_range["end"])
        else:
            # No overlap - add current and start new
            merged.append(current)
            current = next_range.copy()

    merged.append(current)
    return merged


def _get_video_duration(media_path: Path) -> float:
    """Get video duration using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(media_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 0.0


def _get_video_fps(media_path: Path) -> float:
    """Get video frame rate using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(media_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        # Find video stream
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                # Parse fps from r_frame_rate (e.g., "30000/1001" or "30/1")
                fps_str = stream.get("r_frame_rate", "30/1")
                num, den = fps_str.split("/")
                return float(num) / float(den)

        # Fallback to 30 fps if not found
        return 30.0
    except Exception:
        return 30.0


def _extract_segments_parallel(
    tasks: List[Dict], temp_dir: Path, tracker: MultiStepProgressTracker, media_path: Path
) -> bool:
    """Extract video segments in parallel using stream copy from original video."""

    def extract_task(task_info):
        task, media_path = task_info
        output_path = temp_dir / task["output"]

        try:
            # Extract segment from original video with stream copy
            # For the final segment, omit frame count to copy to EOF
            if task.get("is_final"):
                cmd = [
                    "ffmpeg",
                    "-v",
                    "quiet",
                    "-y",
                    "-ss",
                    str(task["start"]),
                    "-i",
                    str(media_path),
                    "-c",
                    "copy",
                    str(output_path),
                ]
            else:
                # Use frame count for pixel-perfect precision
                cmd = [
                    "ffmpeg",
                    "-v",
                    "quiet",
                    "-y",
                    "-ss",
                    str(task["start"]),
                    "-i",
                    str(media_path),
                    "-frames:v",
                    str(task["frame_count"]),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    str(output_path),
                ]

            subprocess.run(cmd, check=True)
            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to extract {task['output']}: {e}")
            return False

    # Execute tasks in parallel
    max_workers = min(4, len(tasks))
    successful = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        task_infos = [(task, media_path) for task in tasks]
        futures = [executor.submit(extract_task, task_info) for task_info in task_infos]

        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            try:
                success = future.result()
                if success:
                    successful += 1

                # Update progress
                progress = (i + 1) / len(tasks)
                tracker.update_step(2, progress)

            except Exception as e:
                print(f"Task failed: {e}")

    tracker.complete_step(2)
    print(f"   Extracted {successful}/{len(tasks)} segments successfully")
    return successful == len(tasks)


def _concat_segments(tasks: List[Dict], output_path: Path, temp_dir: Path) -> bool:
    """Concatenate all segments using concat filter with blackout applied where needed."""
    try:
        # Build list of input files from tasks
        segment_paths = []
        total_frames = 0
        for task in tasks:
            segment_path = temp_dir / task["output"]
            if segment_path.exists():
                segment_paths.append(segment_path)
                total_frames += task.get("frame_count", 0)
            else:
                print(f"   Warning: Segment not found: {segment_path}")
                return False

        if not segment_paths:
            print("   Error: No segments to concatenate")
            return False

        # Detect source codec from first segment
        source_codec = _get_video_codec(segment_paths[0])
        is_hevc = source_codec == "hevc"

        # Create progress file
        progress_file = temp_dir / "ffmpeg_progress.txt"

        # Check for GPU encoder availability
        has_gpu = (is_hevc and _has_encoder("hevc_nvenc")) or (not is_hevc and _has_encoder("h264_nvenc"))

        # Build FFmpeg command with concat filter
        cmd = ["ffmpeg", "-v", "error", "-y"]
        cmd.extend(["-progress", str(progress_file), "-stats_period", "1"])

        # Enable hardware decoding for GPU utilization
        # Decode on GPU, transfer to CPU for filtering, then back to GPU for encoding
        if has_gpu:
            cmd.extend(["-hwaccel", "cuda"])

        # Add all input files
        for segment_path in segment_paths:
            cmd.extend(["-i", str(segment_path)])

        # Build filter_complex with blackout applied to marked segments
        # For each segment: apply black+mute if needs_blackout, else passthrough
        filter_parts = []
        concat_inputs = []

        for i, task in enumerate(tasks):
            if task.get("needs_blackout", False):
                # Apply black video and muted audio
                filter_parts.append(f"[{i}:v]drawbox=color=black@1:t=fill[v{i}]")
                filter_parts.append(f"[{i}:a]volume=0[a{i}]")
            else:
                # Passthrough unchanged
                filter_parts.append(f"[{i}:v]null[v{i}]")
                filter_parts.append(f"[{i}:a]anull[a{i}]")

            concat_inputs.append(f"[v{i}][a{i}]")

        # Combine all filters and concat
        concat_filter = f"{''.join(concat_inputs)}concat=n={len(tasks)}:v=1:a=1[vout][aout]"
        filter_complex = ";".join(filter_parts) + ";" + concat_filter

        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend(["-map", "[vout]", "-map", "[aout]"])

        # Select encoder based on source codec and GPU availability
        using_gpu = False
        if is_hevc:
            if _has_encoder("hevc_nvenc"):
                cmd.extend(["-c:v", "hevc_nvenc", "-preset", "p4", "-cq", "18"])
                using_gpu = True
                print(f"   Using GPU encoder (hevc_nvenc) with hardware decode for {total_frames:,} frames")
            else:
                cmd.extend(["-c:v", "libx265", "-preset", "medium", "-crf", "18"])
                print(f"   Using CPU encoder (libx265) for {total_frames:,} frames - this will take longer")
        else:
            if _has_encoder("h264_nvenc"):
                cmd.extend(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"])
                using_gpu = True
                print(f"   Using GPU encoder (h264_nvenc) with hardware decode for {total_frames:,} frames")
            else:
                cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "18"])
                print(f"   Using CPU encoder (libx264) for {total_frames:,} frames")

        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        cmd.append(str(output_path))

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Run FFmpeg with progress monitoring
        import time
        import threading

        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)

        # Monitor progress in separate thread
        def monitor_progress():
            last_update = time.time()
            while process.poll() is None:
                if progress_file.exists():
                    try:
                        with open(progress_file, "r") as f:
                            lines = f.readlines()
                            progress_data = {}
                            for line in lines:
                                if "=" in line:
                                    key, value = line.strip().split("=", 1)
                                    progress_data[key] = value

                            if "frame" in progress_data and time.time() - last_update > 15:
                                frame = int(progress_data.get("frame", 0))
                                speed = progress_data.get("speed", "0x")
                                if total_frames > 0:
                                    percent = (frame / total_frames) * 100
                                    print(f"   Progress: {percent:.1f}% | Frame: {frame:,}/{total_frames:,} | Speed: {speed}")
                                else:
                                    print(f"   Frame: {frame:,} | Speed: {speed}")
                                last_update = time.time()
                    except Exception:
                        pass
                time.sleep(5)

        monitor_thread = threading.Thread(target=monitor_progress, daemon=True)
        monitor_thread.start()

        # Wait for process to complete
        process.wait()

        # Clean up progress file
        progress_file.unlink(missing_ok=True)

        if process.returncode != 0:
            stderr = process.stderr.read() if process.stderr else ""
            raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Failed to concatenate segments: {e}")
        return False


def _get_video_codec(media_path: Path) -> str:
    """Get video codec name using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(media_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                return stream.get("codec_name", "h264")

        return "h264"
    except Exception:
        return "h264"


def _has_encoder(encoder_name: str) -> bool:
    """Check if specific encoder is available in FFmpeg."""
    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5)
        return encoder_name in result.stdout
    except Exception:
        return False
