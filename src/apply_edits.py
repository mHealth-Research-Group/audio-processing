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
    """Apply video edits using keyframe-aligned stream copy approach."""
    try:
        # Setup progress tracking
        tracker = MultiStepProgressTracker(5, "Video Edit Processing")
        tracker.set_step_names(["Video Validation", "Extraction Planning", "Blank Pregeneration", "Segment Processing", "Final Assembly"])

        # Step 1: Video validation and optimization
        print("Step 1/5: Video analysis...")

        # Quick validation of video file
        is_video_accessible = _validate_video_quick(media_path)
        if not is_video_accessible:
            print("   Video validation failed - aborting")
            return False

        # Use optimized time-based cuts for reliable processing
        print("   Using time-based cuts for optimal performance")
        keyframes = []  # Skip keyframe detection for faster processing
        tracker.complete_step(0)

        # Step 2: Create extraction plan
        print("Step 2/5: Planning edits...")
        extraction_plan = _create_extraction_plan(media_path, changed_segments, keyframes)
        print(f"   Created plan for {len(extraction_plan['tasks'])} segments")
        tracker.complete_step(1)

        # Use local temp directory next to media to avoid system temp space issues
        base_tmp = media_path.parent / "tmp"
        base_tmp.mkdir(exist_ok=True)
        job_tmp_path = Path(tempfile.mkdtemp(prefix="video_edit_", dir=str(base_tmp)))

        try:
            # Step 3: Pregenerate all blank segments
            print("Step 3/5: Pregenerating blank segments...")
            blank_segments_map = _pregenerate_blank_segments(extraction_plan["tasks"], job_tmp_path, tracker)
            if blank_segments_map is None:
                return False
            tracker.complete_step(2)

            # Step 4: Execute extractions
            print(f"Step 4/5: Processing {len(extraction_plan['tasks'])} segments...")
            success = _extract_segments_parallel(extraction_plan["tasks"], job_tmp_path, tracker, media_path, blank_segments_map)
            if not success:
                return False

            # Step 5: Final assembly
            print("Step 5/5: Assembling final video...")
            concat_success = _concat_segments(extraction_plan["concat_list"], output_path, job_tmp_path)
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
    """Create optimized extraction plan with keyframe alignment."""
    # Get video duration
    duration = _get_video_duration(media_path)

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

    # Create extraction tasks
    tasks = []
    concat_list = []
    current_time = 0.0

    for i, edit_range in enumerate(merged_ranges):
        # Add original segment before edit
        if current_time < edit_range["start"]:
            task = {
                "type": "original",
                "start": current_time,
                "duration": edit_range["start"] - current_time,
                "output": f"original_{i}.mp4",
                "is_final": False,
            }
            tasks.append(task)
            concat_list.append(task["output"])

        # Add blank segment for edit
        edit_duration = edit_range["end"] - edit_range["start"]
        blank_task = {"type": "blank", "duration": edit_duration, "output": f"blank_{i}.mp4"}
        tasks.append(blank_task)
        concat_list.append(blank_task["output"])

        current_time = edit_range["end"]

    # Add final original segment if needed
    if current_time < duration:
        task = {
            "type": "original",
            "start": current_time,
            "duration": duration - current_time,
            "output": "original_final.mp4",
            "is_final": True,
        }
        tasks.append(task)
        concat_list.append(task["output"])

    return {"tasks": tasks, "concat_list": concat_list, "merged_ranges": merged_ranges}


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


def _pregenerate_blank_segments(tasks: List[Dict], temp_dir: Path, tracker: MultiStepProgressTracker) -> Dict[float, Path] | None:
    """Pregenerate all unique blank segment durations needed.

    Returns a map of {duration: path} for reuse during extraction.
    """
    # Find all unique blank durations
    blank_durations = set()
    for task in tasks:
        if task["type"] == "blank":
            blank_durations.add(task["duration"])

    if not blank_durations:
        print("   No blank segments needed")
        return {}

    print(f"   Generating {len(blank_durations)} unique blank segment(s)...")

    # Find blank template
    blank_template = Path("blank_muted.MP4")
    if not blank_template.exists():
        print("   Error: blank_muted.MP4 not found")
        return None

    template_duration = _get_video_duration(blank_template)
    if template_duration <= 0:
        print("   Error: Could not get duration of blank_muted.MP4")
        return None

    blank_map = {}

    for i, duration in enumerate(sorted(blank_durations)):
        output_path = temp_dir / f"blank_pregenerated_{i}.mp4"

        # Calculate loops needed
        loops = max(0, int(duration / template_duration))

        # Generate blank segment by looping template then trimming
        # Use re-encode for precise duration trimming (fast preset for speed)
        cmd = [
            "ffmpeg", "-v", "quiet", "-y",
            "-stream_loop", str(loops),
            "-i", str(blank_template),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "copy",
            str(output_path)
        ]

        try:
            subprocess.run(cmd, check=True)
            blank_map[duration] = output_path

            # Update progress
            progress = (i + 1) / len(blank_durations)
            tracker.update_step(2, progress)

        except subprocess.CalledProcessError as e:
            print(f"   Failed to generate blank segment ({duration}s): {e}")
            return None

    print(f"   Pregenerated {len(blank_map)} blank segment(s)")
    return blank_map


def _extract_segments_parallel(
    tasks: List[Dict], temp_dir: Path, tracker: MultiStepProgressTracker, media_path: Path, blank_map: Dict[float, Path]
) -> bool:
    """Extract video segments in parallel using stream copy."""

    def extract_task(task_info):
        task, media_path, blank_map = task_info
        output_path = temp_dir / task["output"]

        try:
            if task["type"] == "original":
                # Extract original segment with stream copy
                # For the final original segment, omit -t to copy to EOF, preventing overshoot
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
                    cmd = [
                        "ffmpeg",
                        "-v",
                        "quiet",
                        "-y",
                        "-ss",
                        str(task["start"]),
                        "-i",
                        str(media_path),
                        "-t",
                        str(task["duration"]),
                        "-c",
                        "copy",
                        str(output_path),
                    ]
                subprocess.run(cmd, check=True)
            else:  # blank
                # Copy pregenerated blank segment
                pregenerated = blank_map.get(task["duration"])
                if not pregenerated or not pregenerated.exists():
                    print(f"   Error: Pregenerated blank for {task['duration']}s not found")
                    return False
                shutil.copy2(pregenerated, output_path)

            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to extract {task['output']}: {e}")
            return False

    # Execute tasks in parallel
    max_workers = min(4, len(tasks))
    successful = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        task_infos = [(task, media_path, blank_map) for task in tasks]
        futures = [executor.submit(extract_task, task_info) for task_info in task_infos]

        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            try:
                success = future.result()
                if success:
                    successful += 1

                # Update progress
                progress = (i + 1) / len(tasks)
                tracker.update_step(3, progress)

            except Exception as e:
                print(f"Task failed: {e}")

    tracker.complete_step(3)
    print(f"   Extracted {successful}/{len(tasks)} segments successfully")
    return successful == len(tasks)


def _concat_segments(concat_list: List[str], output_path: Path, temp_dir: Path) -> bool:
    """Concatenate all segments using concat demuxer."""
    try:
        # Create concat file
        concat_file = temp_dir / "concat_list.txt"
        with open(concat_file, "w") as f:
            for filename in concat_list:
                segment_path = temp_dir / filename
                if segment_path.exists():
                    f.write(f"file '{segment_path.absolute()}'\n")

        # Concatenate with stream copy
        cmd = [
            "ffmpeg",
            "-v",
            "quiet",
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

        subprocess.run(cmd, check=True)
        return True

    except subprocess.CalledProcessError as e:
        print(f"Failed to concatenate segments: {e}")
        return False
