import argparse
import os
import sys
from pathlib import Path
import subprocess

from .audio_analysis import (
    detect_voice_segments,
    load_model,
)
from .media_processing import (
    extract_audio_from_video,
    extract_segments_by_effects,
    process_media_with_effects,
)
from .utils import (
    EFFECT_CONFIGS,
    find_media_file_for_timeline,
    find_media_files,
    find_timeline_files,
    is_audio_file,
    is_video_file,
    load_timeline,
    mmss_to_seconds,
    save_yaml,
)
from .video_merger import merge_videos


def process_single_file(args, gap_info=None):
    """Process a single media file."""
    input_path = Path(args.input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    is_video = is_video_file(input_path)
    is_audio = is_audio_file(input_path)

    if not (is_video or is_audio):
        raise ValueError(f"Unsupported file format for {input_path}")

    output_path = (
        Path(args.output)
        if args.output
        else input_path.parent / f"{input_path.stem}_no_conversations{'.mp3' if not is_video else input_path.suffix}"
    )

    try:
        print(f"Processing {input_path.name}...")
        model = load_model()
        temp_audio_path = None
        local_temp_dir = None

        # Check if we need audio analysis (not for merge-only operations)
        needs_audio_analysis = not getattr(args, "merge_only", False)
        processing_completed = False

        try:
            if is_video and needs_audio_analysis:
                # Try to extract audio for analysis
                # Create local temp directory like the video merger does
                local_temp_dir = input_path.parent / "tmp"
                local_temp_dir.mkdir(exist_ok=True)
                temp_audio_path = str(local_temp_dir / f"{input_path.stem}_audio.wav")

                try:
                    extract_audio_from_video(input_path, temp_audio_path)
                    audio_for_analysis = temp_audio_path
                except subprocess.CalledProcessError:
                    # Video might not have audio stream
                    print(f"Warning: Could not extract audio from {input_path.name} (video may have no audio track)")
                    print("Skipping audio analysis for this file.")
                    audio_for_analysis = None
            else:
                audio_for_analysis = str(input_path) if is_audio else None

            # Only perform audio analysis if we have audio and it's needed
            voice_segments = None
            timeline_data = None

            if audio_for_analysis and needs_audio_analysis:
                from .audio_analysis import _handle_speaker_and_timeline_analysis

                voice_segments, timeline_data = _handle_speaker_and_timeline_analysis(args, audio_for_analysis, model)

                if voice_segments is None and not args.speaker_analysis_only:
                    print("Detecting voice segments...")
                    voice_segments = detect_voice_segments(
                        audio_for_analysis, model, args.min_duration_on, args.min_duration_off
                    )

            # Add gap information to timeline if provided
            if gap_info and timeline_data and gap_info.get("gaps"):
                from .utils import seconds_to_mmss, mmss_to_seconds

                for gap in gap_info["gaps"]:
                    start_offset = gap["start_offset"]
                    duration = gap["duration"]
                    end_offset = start_offset + duration

                    gap_segment = {
                        "start": seconds_to_mmss(start_offset),
                        "end": seconds_to_mmss(end_offset),
                        "duration": seconds_to_mmss(duration),
                        "type": "silence",
                        "speakers": 0,
                        "label": "no_video",
                        "audio_content": "gap",
                    }

                    # Find insertion point to maintain chronological order
                    timeline_list = timeline_data.get("timeline", [])
                    insert_index = 0
                    for i, segment in enumerate(timeline_list):
                        segment_start = mmss_to_seconds(segment["start"])
                        if segment_start > start_offset:
                            insert_index = i
                            break
                        insert_index = i + 1

                    timeline_list.insert(insert_index, gap_segment)

            # Save timeline data if generated
            if timeline_data and args.generate_timeline:
                if hasattr(args, "timeline_output") and args.timeline_output:
                    timeline_output_path = Path(args.timeline_output)
                else:
                    # Auto-generate timeline path if not specified
                    base_path = Path(args.output) if args.output else input_path
                    timeline_output_path = base_path.parent / f"{base_path.stem}_timeline.yaml"
                
                timeline_output_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    save_yaml(timeline_data, timeline_output_path)
                    print(f"Timeline saved: {timeline_output_path}")
                except Exception as e:
                    print(f"Warning: Failed to save timeline to {timeline_output_path}: {e}")

            # Process media with effects only if we have voice segments and not doing analysis only
            if not args.speaker_analysis_only and not getattr(args, "merge_only", False):
                if not voice_segments:
                    print(f"No voice segments detected in {input_path.name}.")
                else:
                    effect_segments = {
                        "mute_only": voice_segments,
                        "black_only": [],
                        "mute_and_black": [],
                    }
                    process_media_with_effects(input_path, output_path, effect_segments)
                    print(f"Processing complete: {output_path}")
                    processing_completed = True

        finally:
            # Only clean up temp audio file if processing completed successfully
            # This preserves the WAV file if processing fails, avoiding re-extraction
            if temp_audio_path and os.path.exists(temp_audio_path) and processing_completed:
                os.remove(temp_audio_path)
            elif temp_audio_path and os.path.exists(temp_audio_path):
                print(f"Preserving extracted audio file for reuse: {temp_audio_path}")
            # Clean up local temp directory if it exists and is empty
            if local_temp_dir and local_temp_dir.exists():
                try:
                    local_temp_dir.rmdir()  # Only removes if empty
                except OSError:
                    pass  # Directory not empty or other files exist
        return 0
    except Exception as e:
        print(f"Error processing {args.input_path}: {e}", file=sys.stderr)
        return 1


def process_directory(args):
    """Process all media files in a directory."""
    input_dir = Path(args.input_path)
    if not input_dir.is_dir():
        print(f"Error: {input_dir} is not a directory.", file=sys.stderr)
        return 1

    # For video merging, output is a file; for other operations, it's a directory
    if getattr(args, "merge_videos", False) and args.output:
        # Output is a single merged video file
        output_path = Path(args.output)
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Output is a directory for multiple processed files
        output_dir = Path(args.output) if args.output else input_dir
        output_dir.mkdir(parents=True, exist_ok=True)

    timeline_dir = Path(args.timeline_output) if args.timeline_output else output_dir
    timeline_dir.mkdir(parents=True, exist_ok=True)

    # Check if directory contains multiple timestamped videos for merging
    all_video_files = [f for f in input_dir.iterdir() if f.is_file() and is_video_file(f)]

    # Filter out merge output files and processed files (same logic as video_merger.py)
    excluded_patterns = ["merged_video", "_merged", "_processed", "_edited", "_h264", "_timeline"]

    video_files = []
    for video_file in all_video_files:
        stem_lower = video_file.stem.lower()
        should_exclude = any(pattern in stem_lower for pattern in excluded_patterns)

        if not should_exclude:
            video_files.append(video_file)

    has_timestamped_videos = False

    if len(video_files) > 1:
        try:
            from .video_merger import extract_timestamp_from_filename

            timestamped_count = sum(1 for vf in video_files if extract_timestamp_from_filename(vf.name) is not None)
            has_timestamped_videos = timestamped_count > 1
        except ImportError:
            pass

    if has_timestamped_videos and getattr(args, "merge_videos", False):
        print("Detected multiple timestamped videos - performing merge operation")

        # Set output filename for merged video
        if args.output:
            merged_output = Path(args.output)
        else:
            merged_output = output_dir / "merged_video.mp4"

        # Check if merged video already exists and prompt user
        if merged_output.exists() and not getattr(args, "force_overwrite", False):
            print(f"Merged video already exists: {merged_output}. Use --force-overwrite to replace it.")
            return 0  # Skip merge operation

        # Check if blank video file exists
        blank_video_path = Path(args.blank_video)
        if not blank_video_path.exists():
            print(f"Error: Blank video file not found: {blank_video_path}", file=sys.stderr)
            return 1

        # Merge videos using blank video approach
        try:
            # Get gap information from video merging
            gap_info = merge_videos(
                input_dir=input_dir,
                output_path=merged_output,
                blank_video=blank_video_path,
                min_gap_threshold=getattr(args, "min_gap_threshold", 2.0),
                max_gap_threshold=getattr(args, "max_gap_threshold", None),
            )
        except Exception as e:
            print(f"Video merging failed: {e}", file=sys.stderr)
            return 1

        print(f"Video merging completed successfully: {merged_output}")

        # Process the merged video if analysis/processing options are enabled
        if (
            args.analyze_speakers
            or args.speaker_analysis_only
            or args.generate_timeline
            or not getattr(args, "merge_only", False)
        ):
            print("\nProcessing merged video for speech analysis...")
            file_args = argparse.Namespace(**vars(args))
            file_args.input_path = str(merged_output)

            # Determine the base output path for the processed file and timeline
            if args.output:
                base_output_path = Path(args.output)
                file_args.output = str(
                    base_output_path.parent / f"{base_output_path.stem}_processed{base_output_path.suffix}"
                )
            else:
                base_output_path = merged_output
                file_args.output = str(
                    base_output_path.parent / f"{base_output_path.stem}_processed{base_output_path.suffix}"
                )

            # Set timeline output path correctly
            if args.timeline_output:
                file_args.timeline_output = str(timeline_dir / f"{base_output_path.stem}_timeline.yaml")
            elif args.generate_timeline:
                file_args.timeline_output = str(timeline_dir / f"{base_output_path.stem}_timeline.yaml")

            # Process the merged video (this will mute conversations by default)
            result = process_single_file(file_args, gap_info=gap_info)

            # Add instructions for manual blank video application if timeline was generated
            if args.generate_timeline:
                timeline_path = file_args.timeline_output
                print(f"\nTimeline generated: {timeline_path}")
                print("To manually review and apply blank video to specific segments:")
                print("  1. Edit the timeline JSON file - change 'type' to 'all' for segments you want blanked")
                print(f"  2. Run: python main.py apply-blank {merged_output} {timeline_path}")

            return result

        return 0

    # Original directory processing logic for non-merging scenarios
    media_files = find_media_files(input_dir)
    if not media_files:
        print(f"No media files found in {input_dir}")
        return 0

    for media_file in media_files:
        file_args = argparse.Namespace(**vars(args))
        file_args.input_path = str(media_file)

        # Determine the base output path for the processed file and timeline
        if args.output:
            base_output_path = Path(args.output).parent / media_file.name
            file_args.output = str(output_dir / f"{media_file.stem}_processed{media_file.suffix}")
        else:
            base_output_path = media_file
            file_args.output = None

        # Set timeline output path correctly
        if args.timeline_output:
            file_args.timeline_output = str(timeline_dir / f"{base_output_path.stem}_timeline.yaml")
        elif args.generate_timeline:
            file_args.timeline_output = str(output_dir / f"{base_output_path.stem}_timeline.yaml")

        process_single_file(file_args)

    return 0


def apply_timeline_edits_command(args):
    """Apply timeline edits to all media files in a directory."""
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"Error: Directory not found: {directory}", file=sys.stderr)
        return 1

    timeline_files = find_timeline_files(directory)
    if not timeline_files:
        print(f"No timeline files found in {directory}")
        return 1

    _ = load_model()
    for timeline_path in timeline_files:
        media_path = find_media_file_for_timeline(timeline_path)
        if not media_path:
            print(f"No media file found for {timeline_path.name}")
            continue

        try:
            timeline_data = load_timeline(timeline_path)
        except Exception as e:
            print(f"Error loading timeline {timeline_path.name}: {e}")
            continue

        if args.effect_labels:
            original_configs = EFFECT_CONFIGS.copy()
            for label in EFFECT_CONFIGS:
                EFFECT_CONFIGS[label] = {"mute_audio": False, "black_video": False}
            for label in args.effect_labels:
                if label in EFFECT_CONFIGS:
                    EFFECT_CONFIGS[label] = {"mute_audio": True, "black_video": False}

        effect_segments = extract_segments_by_effects(timeline_data)
        if sum(len(v) for v in effect_segments.values()) == 0:
            if args.effect_labels:
                EFFECT_CONFIGS.clear()
                EFFECT_CONFIGS.update(original_configs)
            continue

        output_path = media_path.parent / f"{media_path.stem}{args.output_suffix}{media_path.suffix}"
        try:
            process_media_with_effects(media_path, output_path, effect_segments)
            print(f"Created edited file: {output_path.name}")
        except Exception as e:
            print(f"Error processing {media_path.name}: {e}")
        if args.effect_labels:
            EFFECT_CONFIGS.clear()
            EFFECT_CONFIGS.update(original_configs)
    return 0


def apply_blank_command(args):
    """Apply blank video to speech segments from timeline."""
    input_video = Path(args.input_video)
    timeline_path = Path(args.timeline)
    blank_video_path = Path(args.blank_video)

    # Validate inputs
    if not input_video.exists():
        print(f"Error: Input video not found: {input_video}", file=sys.stderr)
        return 1
    if not timeline_path.exists():
        print(f"Error: Timeline file not found: {timeline_path}", file=sys.stderr)
        return 1
    if not blank_video_path.exists():
        print(f"Error: Blank video not found: {blank_video_path}", file=sys.stderr)
        return 1

    # Set output path
    output_path = (
        Path(args.output) if args.output else input_video.parent / f"{input_video.stem}_blanked{input_video.suffix}"
    )

    try:
        # Load timeline data
        timeline_data = load_timeline(timeline_path)

        # Extract segments marked as "all" for blank video replacement
        blank_segments = []
        timeline_modified = False
        if "timeline" in timeline_data:
            for segment in timeline_data["timeline"]:
                if segment.get("type") == "all":
                    start_time = mmss_to_seconds(segment["start"])
                    end_time = mmss_to_seconds(segment["end"])
                    blank_segments.append((start_time, end_time))
                    # Keep existing label or set to "Removed" if none provided
                    if not segment.get("label"):
                        segment["label"] = "Removed"
                    timeline_modified = True

        if not blank_segments:
            print("No segments marked as 'all' found in timeline")
            print("To mark segments for blank video replacement, edit the timeline JSON and change 'type' to 'all'")
            return 0

        print(f"Found {len(blank_segments)} segments marked as 'all' to replace with blank video")

        # Apply blank video to marked segments
        from .media_processing import apply_blank_video_to_segments

        # Determine whether to trim first frame (default: True, disabled with --no-trim-first-frame)
        trim_first_frame = not getattr(args, "no_trim_first_frame", False)

        apply_blank_video_to_segments(
            input_video, output_path, blank_segments, blank_video_path, timeline_path, trim_first_frame
        )

        # Save updated timeline if segments were modified
        if timeline_modified:
            try:
                save_yaml(timeline_data, timeline_path)
                print(f"Timeline updated: {timeline_path}")
            except Exception as e:
                print(f"Warning: Failed to save updated timeline: {e}")

        print(f"Blank video applied successfully: {output_path}")
        return 0

    except Exception as e:
        print(f"Error applying blank video: {e}", file=sys.stderr)
        return 1


def compress_command(args):
    """
    Compress video files to H.264 with smaller file sizes using GPU acceleration when available.

    This command can process either a single video file or all video files in a directory.
    It uses FFmpeg with hardware acceleration (NVENC) when available, falling back to
    software encoding (libx264) otherwise.

    Args:
        args: Namespace object containing the following attributes:
            - input_path (str): Path to input video file or directory
            - output (str, optional): Path for output file or directory. If not provided:
                - For single files: adds "_compressed" suffix to filename
                - For directories: creates "compressed" subdirectory
            - quality (int): CRF/CQ value for compression quality (default: 23)
                - Lower values = better quality, larger file size
                - Range: 0-51 for CRF, 0-51 for NVENC CQ
            - preset (str): Encoding speed preset (default: "fast")
                - Options: "ultrafast", "fast", "medium", "slow", "veryslow"
            - max_width (int): Maximum width for output video (default: 1280)
                - Set to 0 to disable scaling
                - Maintains aspect ratio when scaling

    Returns:
        int: Exit code (0 for success, 1 for error)
            - Single file: 0 if compression succeeds, 1 if it fails
            - Directory: 0 if at least one file succeeds, 1 if all fail

    Examples:
        Single file compression:
            args.input_path = "video.mp4"
            args.output = "compressed_video.mp4"
            args.quality = 23

        Directory compression:
            args.input_path = "/videos/"
            args.output = "/output/"  # Optional
            args.quality = 20  # Higher quality
            args.preset = "slow"  # Better compression

    Note:
        - Uses H.264 video codec with AAC audio for broad compatibility
        - Automatically detects and uses NVIDIA GPU acceleration when available
        - Provides compression statistics (file size reduction percentage)
        - Skips non-video files when processing directories
    """
    from pathlib import Path
    from .media_processing import compress_video
    from .utils import is_video_file, find_media_files

    input_path = Path(args.input_path)

    if not input_path.exists():
        print(f"Error: Input path not found: {input_path}", file=sys.stderr)
        return 1

    # Handle single file vs directory
    if input_path.is_file():
        if not is_video_file(input_path):
            print(f"Error: {input_path} is not a video file", file=sys.stderr)
            return 1

        output_path = args.output if args.output else None

        try:
            compress_video(
                input_path=input_path,
                output_path=output_path,
                quality=str(args.quality),
                preset=args.preset,
                max_width=args.max_width if args.max_width > 0 else None,
            )
            return 0
        except Exception as e:
            print(f"Error compressing {input_path}: {e}", file=sys.stderr)
            return 1

    elif input_path.is_dir():
        # Process all video files in directory
        video_files = [f for f in find_media_files(input_path) if is_video_file(f)]

        if not video_files:
            print(f"No video files found in {input_path}")
            return 0

        # Set up output directory
        output_dir = Path(args.output) if args.output else input_path / "compressed"
        output_dir.mkdir(parents=True, exist_ok=True)

        success_count = 0
        for video_file in video_files:
            output_path = output_dir / f"{video_file.stem}_compressed{video_file.suffix}"

            try:
                compress_video(
                    input_path=video_file,
                    output_path=output_path,
                    quality=str(args.quality),
                    preset=args.preset,
                    max_width=args.max_width if args.max_width > 0 else None,
                )
                success_count += 1
            except Exception as e:
                print(f"Error compressing {video_file.name}: {e}", file=sys.stderr)

        print(f"Compression complete: {success_count}/{len(video_files)} files processed")
        return 0 if success_count > 0 else 1

    else:
        print(f"Error: {input_path} is neither a file nor directory", file=sys.stderr)
        return 1
