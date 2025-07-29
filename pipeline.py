"""
Multi-step video and audio processing pipeline.

This module implements the complete workflow:
1. Step 1: Merge videos with gap detection and missing video labels
2. Step 2: Process audio in the merged file
3. Step 3: Allow for manual adjustment of audio and video blanking
4. Step 4: Update video with adjustments and output comprehensive label file
"""

import datetime
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Import existing audio processing functionality
from main import (
    load_model,
    extract_audio_from_video,
    generate_speaker_timeline,
    load_timeline,
    extract_segments_by_effects,
    process_media_with_effects,
)

# Import new video processing functionality
from video_processing import merge_videos_with_gaps, save_gap_labels, merge_labels_with_timeline, get_video_duration


class ProcessingPipeline:
    """Multi-step video and audio processing pipeline."""

    def __init__(self, working_directory: str):
        """
        Initialize the processing pipeline.

        Args:
            working_directory: Directory to use for processing files
        """
        self.working_dir = Path(working_directory)
        self.working_dir.mkdir(exist_ok=True)

        # Pipeline state
        self.step1_complete = False
        self.step2_complete = False
        self.step3_complete = False
        self.step4_complete = False

        # File paths
        self.merged_video_path: Optional[str] = None
        self.metadata_csv_path: Optional[str] = None
        self.gap_labels: List[Dict] = []
        self.audio_timeline_path: Optional[str] = None
        self.manual_adjustments_path: Optional[str] = None
        self.final_video_path: Optional[str] = None
        self.final_labels_path: Optional[str] = None

        # State file for resuming pipeline
        self.state_file = self.working_dir / "pipeline_state.json"

    def save_state(self):
        """Save current pipeline state to disk."""
        state = {
            "step1_complete": self.step1_complete,
            "step2_complete": self.step2_complete,
            "step3_complete": self.step3_complete,
            "step4_complete": self.step4_complete,
            "merged_video_path": self.merged_video_path,
            "metadata_csv_path": self.metadata_csv_path,
            "gap_labels": self.gap_labels,
            "audio_timeline_path": self.audio_timeline_path,
            "manual_adjustments_path": self.manual_adjustments_path,
            "final_video_path": self.final_video_path,
            "final_labels_path": self.final_labels_path,
        }

        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def load_state(self) -> bool:
        """
        Load pipeline state from disk.

        Returns:
            True if state was loaded successfully, False otherwise
        """
        if not self.state_file.exists():
            return False

        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)

            self.step1_complete = state.get("step1_complete", False)
            self.step2_complete = state.get("step2_complete", False)
            self.step3_complete = state.get("step3_complete", False)
            self.step4_complete = state.get("step4_complete", False)
            self.merged_video_path = state.get("merged_video_path")
            self.metadata_csv_path = state.get("metadata_csv_path")
            self.gap_labels = state.get("gap_labels", [])
            self.audio_timeline_path = state.get("audio_timeline_path")
            self.manual_adjustments_path = state.get("manual_adjustments_path")
            self.final_video_path = state.get("final_video_path")
            self.final_labels_path = state.get("final_labels_path")

            return True
        except Exception as e:
            print(f"Warning: Could not load pipeline state: {e}")
            return False

    def step1_merge_videos(
        self, video_directory: str, output_path: Optional[str] = None
    ) -> Tuple[str, str, List[Dict]]:
        """
        Step 1: Merge videos with gap detection and missing video labels.

        Args:
            video_directory: Path to directory containing video files
            output_path: Optional output path for merged video

        Returns:
            Tuple of (merged_video_path, metadata_csv_path, gap_labels)
        """
        print("=" * 60)
        print("STEP 1: MERGING VIDEOS WITH GAP DETECTION")
        print("=" * 60)

        # Set default output path in working directory
        if output_path is None:
            output_path = str(self.working_dir / "merged_video.mp4")

        # Merge videos and detect gaps
        merged_video_path, metadata_csv_path, gap_labels = merge_videos_with_gaps(
            video_directory, output_path, create_black_videos=True
        )

        # Save gap labels
        if gap_labels:
            gap_labels_path = save_gap_labels(gap_labels, merged_video_path)
            print(f"✓ Gap labels saved to: {gap_labels_path}")

        # Update pipeline state
        self.merged_video_path = merged_video_path
        self.metadata_csv_path = metadata_csv_path
        self.gap_labels = gap_labels
        self.step1_complete = True
        self.save_state()

        print("\n✓ Step 1 complete!")
        print(f"  Merged video: {merged_video_path}")
        print(f"  Metadata CSV: {metadata_csv_path}")
        if gap_labels:
            print(f"  Found {len(gap_labels)} video gaps")

        return merged_video_path, metadata_csv_path, gap_labels

    def step2_process_audio(self, min_duration_on: float = 0.1, min_duration_off: float = 0.1) -> str:
        """
        Step 2: Process audio in the merged file.

        Args:
            min_duration_on: Minimum duration for speech regions
            min_duration_off: Minimum duration for non-speech regions

        Returns:
            Path to the audio timeline file
        """
        print("=" * 60)
        print("STEP 2: PROCESSING AUDIO IN MERGED VIDEO")
        print("=" * 60)

        if not self.step1_complete or not self.merged_video_path:
            raise RuntimeError("Step 1 must be completed before Step 2")

        # Load pyannote model
        print("Loading pyannote model...")
        model = load_model()

        # Extract audio from merged video to same directory as video
        merged_video_path = Path(self.merged_video_path)
        temp_audio_path = merged_video_path.parent / f"{merged_video_path.stem}_temp_audio.wav"

        try:
            has_audio = extract_audio_from_video(self.merged_video_path, str(temp_audio_path))
            timeline_data = {}

            if not has_audio:
                print("ℹ️  Merged video has no audio stream - creating a silent timeline.")
                duration = get_video_duration(self.merged_video_path)
                timeline_data = {
                    "timeline": [
                        {
                            "start": "0:00.000",
                            "end": f"{int(duration // 60)}:{duration % 60:06.3f}",
                            "duration": f"{int(duration // 60)}:{duration % 60:06.3f}",
                            "type": "silence",
                            "speakers": 0,
                            "label": "silence",
                        }
                    ],
                    "summary": {
                        "total_duration": f"{int(duration // 60)}:{duration % 60:06.3f}",
                        "total_speech_time": "0:00.000",
                        "total_conversation_time": "0:00.000",
                        "total_speaking_time": "0:00.000",
                        "total_silence_time": f"{int(duration // 60)}:{duration % 60:06.3f}",
                        "speech_percentage": 0,
                        "conversation_percentage": 0,
                        "has_multiple_speakers": False,
                        "num_segments": 1,
                    },
                    "has_multiple_speakers": False,
                }
            else:
                # Generate audio timeline
                print("Generating speaker timeline...")
                timeline_data = generate_speaker_timeline(
                    str(temp_audio_path), model, min_duration_on, min_duration_off
                )

            # Merge with video gap labels
            if self.gap_labels:
                print("Merging audio timeline with video gap labels...")
                timeline_data = merge_labels_with_timeline(timeline_data, self.gap_labels)

            # Save timeline
            timeline_path = Path(self.merged_video_path).parent / f"{Path(self.merged_video_path).stem}_timeline.json"
            with open(timeline_path, "w", encoding="utf-8") as f:
                json.dump(timeline_data, f, indent=2, ensure_ascii=False)

            self.audio_timeline_path = str(timeline_path)
            self.step2_complete = True
            self.save_state()

            print("\n✓ Step 2 complete!")
            print(f"  Audio timeline saved to: {timeline_path}")

            # Print summary
            summary = timeline_data.get("summary", {})
            print(f"  Total duration: {summary.get('total_duration', 'Unknown')}")
            print(f"  Speech segments: {summary.get('num_segments', 0)}")
            if self.gap_labels:
                print(f"  Video gaps: {len(self.gap_labels)}")

            return str(timeline_path)

        finally:
            # Clean up temporary audio file
            if temp_audio_path.exists():
                temp_audio_path.unlink()

    def step3_prepare_manual_adjustments(self) -> str:
        """
        Step 3: Prepare file for manual adjustment of audio and video blanking.

        Returns:
            Path to the manual adjustments file
        """
        print("=" * 60)
        print("STEP 3: PREPARING FOR MANUAL ADJUSTMENTS")
        print("=" * 60)

        if not self.step2_complete or not self.audio_timeline_path:
            raise RuntimeError("Step 2 must be completed before Step 3")

        # Load the timeline data
        timeline_data = load_timeline(self.audio_timeline_path)

        # Create manual adjustments file
        adjustments_path = (
            Path(self.audio_timeline_path).parent / f"{Path(self.audio_timeline_path).stem}_manual_adjustments.json"
        )

        # Prepare adjustments structure
        manual_adjustments = {
            "instructions": {
                "audio_labels": {
                    "speaking": "Mute audio only (single speaker)",
                    "conversation": "Mute audio only (multiple speakers/conversation)",
                    "silence": "No effects applied",
                    "mute": "Mute audio only",
                    "black": "Black out video but preserve audio",
                    "all": "Mute audio AND black out video",
                },
                "video_labels": {
                    "video_gap": "Missing video periods (auto-detected)",
                    "manual_black": "Manually black out video",
                    "manual_remove": "Manually remove both audio and video",
                },
                "custom_time_ranges": {
                    "description": "Add custom time ranges to apply effects",
                    "format": "start_time-end_time (e.g., '1:30.500-2:45.200')",
                    "example": ["1:30.500-2:45.200", "5:00.000-5:30.000"],
                },
            },
            "timeline": timeline_data.get("timeline", []),
            "custom_ranges": {"mute_only": [], "black_only": [], "mute_and_black": []},
            "manual_review_completed": False,
        }

        # Save manual adjustments file
        with open(adjustments_path, "w", encoding="utf-8") as f:
            json.dump(manual_adjustments, f, indent=2, ensure_ascii=False)

        self.manual_adjustments_path = str(adjustments_path)
        self.step3_complete = True
        self.save_state()

        print("✓ Step 3 complete!")
        print(f"  Manual adjustments file created: {adjustments_path}")
        print("\n📝 MANUAL REVIEW REQUIRED:")
        print(f"   1. Open the file: {adjustments_path}")
        print("   2. Review and modify the 'timeline' entries as needed")
        print("   3. Change 'label' values to control effects:")
        print("      - 'speaking'/'conversation': Mute audio only")
        print("      - 'black': Black out video only")
        print("      - 'all': Mute audio AND black out video")
        print("      - 'silence': No effects")
        print("   4. Add custom time ranges to 'custom_ranges' if needed")
        print("   5. Set 'manual_review_completed' to true when done")
        print("   6. Run step 4 to apply the adjustments")

        return str(adjustments_path)

    def step4_apply_final_adjustments(self, output_suffix: str = "_final") -> Tuple[str, str]:
        """
        Step 4: Apply manual adjustments and create final video with comprehensive labels.

        Args:
            output_suffix: Suffix for the final output files

        Returns:
            Tuple of (final_video_path, final_labels_path)
        """
        print("=" * 60)
        print("STEP 4: APPLYING FINAL ADJUSTMENTS")
        print("=" * 60)

        if not self.step3_complete or not self.manual_adjustments_path:
            raise RuntimeError("Step 3 must be completed before Step 4")

        # Load manual adjustments
        with open(self.manual_adjustments_path, "r", encoding="utf-8") as f:
            adjustments = json.load(f)

        # Check if manual review was completed
        if not adjustments.get("manual_review_completed", False):
            raise RuntimeError(
                "Manual review not completed. Please edit the adjustments file and "
                "set 'manual_review_completed' to true."
            )

        # Create final video path
        if self.merged_video_path is None:
            raise RuntimeError("No merged video path available")

        merged_video_path = Path(self.merged_video_path)
        final_video_path = merged_video_path.parent / f"{merged_video_path.stem}{output_suffix}.mp4"

        # Prepare timeline data for processing
        timeline_data = {"timeline": adjustments["timeline"]}

        # Extract segments by effects
        effect_segments = extract_segments_by_effects(timeline_data)

        # Add custom ranges
        custom_ranges = adjustments.get("custom_ranges", {})
        for range_str in custom_ranges.get("mute_only", []):
            start_str, end_str = range_str.split("-")
            start_seconds = self._parse_time_to_seconds(start_str)
            end_seconds = self._parse_time_to_seconds(end_str)
            effect_segments["mute_only"].append((start_seconds, end_seconds))

        for range_str in custom_ranges.get("black_only", []):
            start_str, end_str = range_str.split("-")
            start_seconds = self._parse_time_to_seconds(start_str)
            end_seconds = self._parse_time_to_seconds(end_str)
            effect_segments["black_only"].append((start_seconds, end_seconds))

        for range_str in custom_ranges.get("mute_and_black", []):
            start_str, end_str = range_str.split("-")
            start_seconds = self._parse_time_to_seconds(start_str)
            end_seconds = self._parse_time_to_seconds(end_str)
            effect_segments["mute_and_black"].append((start_seconds, end_seconds))

        # Apply effects to video
        print("Applying effects to create final video...")
        process_media_with_effects(self.merged_video_path, final_video_path, effect_segments, None)

        # Create comprehensive final labels
        final_labels = self._create_comprehensive_labels(adjustments, effect_segments)

        # Save final labels as CSV
        final_labels_path = final_video_path.parent / f"{final_video_path.stem}_comprehensive_labels.csv"
        import csv

        with open(final_labels_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(final_labels)

        # Update pipeline state
        self.final_video_path = str(final_video_path)
        self.final_labels_path = str(final_labels_path)
        self.step4_complete = True
        self.save_state()

        print("\n✓ Step 4 complete!")
        print(f"  Final video: {final_video_path}")
        print(f"  Comprehensive labels: {final_labels_path}")

        # Print summary
        total_segments = sum(len(segments) for segments in effect_segments.values())
        print(f"  Applied effects to {total_segments} segments")

        return str(final_video_path), str(final_labels_path)

    def _parse_time_to_seconds(self, time_str: str) -> float:
        """Parse time string to seconds (supports MM:SS.sss format)."""
        if ":" in time_str:
            parts = time_str.split(":")
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        else:
            return float(time_str)

    def _mmss_to_timestamp(self, mmss_time: str, base_datetime: datetime.datetime) -> str:
        """Convert MM:SS.sss time to timestamp format."""
        parts = mmss_time.split(":")
        minutes = int(parts[0])
        seconds = float(parts[1])

        total_seconds = minutes * 60 + seconds
        time_offset = datetime.timedelta(seconds=total_seconds)
        result_datetime = base_datetime + time_offset

        # Format with milliseconds
        return result_datetime.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _create_comprehensive_labels(self, adjustments: Dict, effect_segments: Dict) -> List[List[str]]:
        """Create comprehensive label file in CSV format."""
        # Use the video creation time as base timestamp
        # For now, use current time as base - in real scenario this would be video metadata
        base_datetime = datetime.datetime.now().replace(microsecond=0)

        # Create CSV rows
        csv_rows = []

        # Add header row
        csv_rows.append(["START_TIME", "STOP_TIME", "PREDICTION", "SOURCE", "LABELSET"])

        # Process timeline segments
        for segment in adjustments["timeline"]:
            start_time = self._mmss_to_timestamp(segment["start"], base_datetime)
            end_time = self._mmss_to_timestamp(segment["end"], base_datetime)

            # Map internal labels to prediction labels
            label = segment.get("label", "")
            if label == "speaking":
                prediction = "Speaking"
            elif label == "conversation":
                prediction = "Conversation"
            elif label == "silence":
                prediction = "Silence"
            elif label == "video_gap":
                prediction = "Missing_Video"
            elif label == "black":
                prediction = "Blanked_Video"
            elif label == "mute":
                prediction = "Muted_Audio"
            elif label == "all":
                prediction = "Muted_And_Blanked"
            else:
                prediction = label.replace("_", " ").title()

            csv_rows.append([start_time, end_time, prediction, "Player", "DEFAULT"])

        # Add video gap segments if any
        for gap in self.gap_labels:
            start_time = self._mmss_to_timestamp(gap["start"], base_datetime)
            end_time = self._mmss_to_timestamp(gap["end"], base_datetime)
            csv_rows.append([start_time, end_time, "Missing_Video", "Player", "DEFAULT"])

        return csv_rows

    def get_status(self) -> Dict:
        """Get current pipeline status."""
        return {
            "step1_merge_videos": "✓ Complete" if self.step1_complete else "⏳ Pending",
            "step2_process_audio": "✓ Complete" if self.step2_complete else "⏳ Pending",
            "step3_manual_adjustments": "✓ Complete" if self.step3_complete else "⏳ Pending",
            "step4_final_processing": "✓ Complete" if self.step4_complete else "⏳ Pending",
            "files": {
                "merged_video": self.merged_video_path,
                "audio_timeline": self.audio_timeline_path,
                "manual_adjustments": self.manual_adjustments_path,
                "final_video": self.final_video_path,
                "final_labels": self.final_labels_path,
            },
        }


def add_pipeline_arguments(parser):
    """Add arguments for the pipeline command."""
    subparsers = parser.add_subparsers(dest="pipeline_step", help="Pipeline step to execute")

    # Step 1: Merge videos
    step1_parser = subparsers.add_parser("step1", help="Step 1: Merge videos with gap detection")
    step1_parser.add_argument("video_directory", help="Directory containing video files to merge")
    step1_parser.add_argument(
        "-w", "--working-dir", default="./pipeline_work", help="Working directory for pipeline files"
    )
    step1_parser.add_argument("-o", "--output", help="Output path for merged video")

    # Step 2: Process audio
    step2_parser = subparsers.add_parser("step2", help="Step 2: Process audio in merged video")
    step2_parser.add_argument(
        "-w", "--working-dir", default="./pipeline_work", help="Working directory for pipeline files"
    )
    step2_parser.add_argument("--min-duration-on", type=float, default=0.1, help="Minimum duration for speech regions")
    step2_parser.add_argument(
        "--min-duration-off", type=float, default=0.1, help="Minimum duration for non-speech regions"
    )

    # Step 3: Manual adjustments
    step3_parser = subparsers.add_parser("step3", help="Step 3: Prepare manual adjustments")
    step3_parser.add_argument(
        "-w", "--working-dir", default="./pipeline_work", help="Working directory for pipeline files"
    )

    # Step 4: Final processing
    step4_parser = subparsers.add_parser("step4", help="Step 4: Apply final adjustments")
    step4_parser.add_argument(
        "-w", "--working-dir", default="./pipeline_work", help="Working directory for pipeline files"
    )
    step4_parser.add_argument("--output-suffix", default="_final", help="Suffix for final output files")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check pipeline status")
    status_parser.add_argument(
        "-w", "--working-dir", default="./pipeline_work", help="Working directory for pipeline files"
    )

    # Full pipeline command
    full_parser = subparsers.add_parser("full", help="Run complete pipeline (steps 1-2)")
    full_parser.add_argument("video_directory", help="Directory containing video files to merge")
    full_parser.add_argument(
        "-w", "--working-dir", default="./pipeline_work", help="Working directory for pipeline files"
    )
    full_parser.add_argument("-o", "--output", help="Output path for merged video")
    full_parser.add_argument("--min-duration-on", type=float, default=0.1, help="Minimum duration for speech regions")
    full_parser.add_argument(
        "--min-duration-off", type=float, default=0.1, help="Minimum duration for non-speech regions"
    )


def execute_pipeline_command(args):
    """Execute the appropriate pipeline command."""

    pipeline = ProcessingPipeline(args.working_dir)
    pipeline.load_state()

    if args.pipeline_step == "step1":
        pipeline.step1_merge_videos(args.video_directory, args.output)

    elif args.pipeline_step == "step2":
        pipeline.step2_process_audio(args.min_duration_on, args.min_duration_off)

    elif args.pipeline_step == "step3":
        pipeline.step3_prepare_manual_adjustments()

    elif args.pipeline_step == "step4":
        pipeline.step4_apply_final_adjustments(args.output_suffix)

    elif args.pipeline_step == "status":
        status = pipeline.get_status()
        print("\n" + "=" * 50)
        print("PIPELINE STATUS")
        print("=" * 50)
        for step, status_text in status.items():
            if step != "files":
                print(f"{step:25} {status_text}")

        print("\nFILES:")
        for file_type, file_path in status["files"].items():
            if file_path:
                print(f"  {file_type:20} {file_path}")
            else:
                print(f"  {file_type:20} Not created yet")

    elif args.pipeline_step == "full":
        print("Running full pipeline (Steps 1-2)...")
        pipeline.step1_merge_videos(args.video_directory, args.output)
        pipeline.step2_process_audio(args.min_duration_on, args.min_duration_off)
        pipeline.step3_prepare_manual_adjustments()

        print("\n" + "=" * 60)
        print("FULL PIPELINE COMPLETE - MANUAL REVIEW REQUIRED")
        print("=" * 60)
        print("Steps 1-3 are complete. To finish the pipeline:")
        print(f"1. Review and edit: {pipeline.manual_adjustments_path}")
        print("2. Run: uv run main.py pipeline step4")

    else:
        print("Invalid pipeline step. Use: step1, step2, step3, step4, status, or full")
        return 1

    return 0
