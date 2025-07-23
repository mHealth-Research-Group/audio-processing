import argparse
import os
import sys
from pathlib import Path
import subprocess

from .audio_analysis import (
    analyze_speaker_segments_direct,
    detect_multiple_speakers,
    detect_voice_segments,
    generate_speaker_timeline,
    load_model,
)
from .media_processing import (
    apply_effects_to_time_ranges,
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
)
from .video_merger import (
    merge_directory_videos,
)


def _handle_speaker_and_timeline_analysis(args, audio_path, model):
    """Helper to handle speaker analysis and timeline generation."""
    if args.analyze_speakers or args.speaker_analysis_only:
        print(f"Analyzing speakers in: {audio_path}")
        if args.detailed_analysis:
            speaker_analysis = analyze_speaker_segments_direct(audio_path, model)
            print("Detailed Speaker Analysis Results:")
            print(
                "   Multiple speakers detected:",
                "✓ YES" if speaker_analysis["has_multiple_speakers"] else "✗ NO",
            )
            print(f"   Maximum speakers detected: {speaker_analysis['max_speakers_detected']}")
            print(f"   Confidence score: {speaker_analysis['confidence_score']:.2f}")
        else:
            speaker_analysis = detect_multiple_speakers(audio_path, model, args.min_duration_on, args.min_duration_off)
            print("Speaker Analysis Results:")
            print(
                "   Multiple speakers detected:",
                "✓ YES" if speaker_analysis["has_multiple_speakers"] else "✗ NO",
            )
            print(f"   Overlap percentage: {speaker_analysis['overlap_percentage']:.1f}%")

    timeline_data = None
    voice_segments = None
    if args.generate_timeline:
        print("Generating speaker timeline...")
        timeline_data = generate_speaker_timeline(audio_path, model, args.min_duration_on, args.min_duration_off)
        if timeline_data and "timeline" in timeline_data:
            voice_segments = [
                (mmss_to_seconds(s["start"]), mmss_to_seconds(s["end"]))
                for s in timeline_data["timeline"]
                if s["type"] == "speech"
            ]
    return voice_segments, timeline_data


def process_single_file(args):
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
                voice_segments, timeline_data = _handle_speaker_and_timeline_analysis(args, audio_for_analysis, model)

                if voice_segments is None and not args.speaker_analysis_only:
                    print("Detecting voice segments...")
                    voice_segments = detect_voice_segments(
                        audio_for_analysis, model, args.min_duration_on, args.min_duration_off
                    )

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

        finally:
            if temp_audio_path and os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
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

    output_dir = Path(args.output) if args.output else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    timeline_dir = Path(args.timeline_output) if args.timeline_output else output_dir
    timeline_dir.mkdir(parents=True, exist_ok=True)

    # Check if directory contains multiple timestamped videos for merging
    video_files = [f for f in input_dir.iterdir() if f.is_file() and is_video_file(f)]
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
            merged_output = output_dir / f"merged_video{'_h264' if getattr(args, 'convert_h264', True) else ''}.mp4"

        # Merge videos with optional H264 conversion
        success = merge_directory_videos(
            input_dir=input_dir,
            output_path=merged_output,
            max_gap_threshold=getattr(args, "max_gap_threshold", 300.0),
            convert_to_h264=getattr(args, "convert_h264", True),
            h264_preset=getattr(args, "h264_preset", "medium"),
            h264_crf=getattr(args, "h264_crf", 23),
        )

        if not success:
            print("Video merging failed", file=sys.stderr)
            return 1

        print(f"Video merging completed successfully: {merged_output}")

        # Process the merged video if other analysis options are enabled
        if args.analyze_speakers or args.speaker_analysis_only or args.generate_timeline or not args.merge_only:
            print("\nProcessing merged video for speech analysis...")
            file_args = argparse.Namespace(**vars(args))
            file_args.input_path = str(merged_output)
            file_args.output = str(merged_output.parent / f"{merged_output.stem}_processed{merged_output.suffix}")
            if args.timeline_output:
                file_args.timeline_output = str(timeline_dir / f"{merged_output.stem}_timeline.json")
            elif args.generate_timeline:
                file_args.timeline_output = str(merged_output.parent / f"{merged_output.stem}_timeline.json")

            return process_single_file(file_args)

        return 0

    # Original directory processing logic for non-merging scenarios
    media_files = find_media_files(input_dir)
    if not media_files:
        print(f"No media files found in {input_dir}")
        return 0

    for media_file in media_files:
        file_args = argparse.Namespace(**vars(args))
        file_args.input_path = str(media_file)
        if args.output:
            file_args.output = str(output_dir / f"{media_file.stem}_processed{media_file.suffix}")
        else:
            file_args.output = None
        if args.timeline_output:
            file_args.timeline_output = str(timeline_dir / f"{media_file.stem}_timeline.json")
        elif args.generate_timeline:
            file_args.timeline_output = str(output_dir / f"{media_file.stem}_timeline.json")
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


def apply_effects_command(args):
    """Apply effects to specific time ranges."""
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1
    if not (is_video_file(input_path) or is_audio_file(input_path)):
        print(f"Error: Unsupported file format for {input_path}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output) if args.output else input_path.parent / f"{input_path.stem}_effects{input_path.suffix}"
    )
    try:
        apply_effects_to_time_ranges(input_path, output_path, args.time_ranges, args.effect)
        print(f"Effects applied successfully: {output_path}")
        return 0
    except Exception as e:
        print(f"Error applying effects: {e}", file=sys.stderr)
        return 1


def debug_encoding_command(args):
    """Debug file encoding issues."""
    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        return 1
    from .utils import detect_file_encoding

    detected_encoding = detect_file_encoding(file_path)
    print(f"Suggested encoding: {detected_encoding}")
    return 0
