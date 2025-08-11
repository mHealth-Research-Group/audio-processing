"""
Processing pipeline utilities for audio and video analysis.
Handles the core processing workflow for single files and batches.
"""

import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from .audio_analysis import load_model, cleanup_gpu_memory
from .media_processing import extract_audio_from_video
from .file_operations import setup_temp_directory, cleanup_temp_files, validate_input_file
from .utils import save_yaml


class ProcessingContext:
    """Context object to hold processing state and resources."""

    def __init__(self, input_path: Path, args):
        self.input_path = input_path
        self.args = args
        self.is_video, self.is_audio = validate_input_file(input_path)
        self.model = None
        self.temp_files = []
        self.temp_audio_path = None
        self.audio_for_analysis = None

    def load_model_if_needed(self):
        """Load ML model if not already loaded."""
        if self.model is None:
            self.model = load_model()
        return self.model

    def setup_audio_extraction(self) -> bool:
        """
        Set up audio extraction for video files.

        Returns:
            True if audio is available for analysis, False otherwise
        """
        needs_audio_analysis = not getattr(self.args, "merge_only", False)

        if not needs_audio_analysis:
            return False

        if self.is_video:
            temp_dir = setup_temp_directory(self.input_path.parent)
            self.temp_files.append(temp_dir)

            self.temp_audio_path = str(temp_dir / f"{self.input_path.stem}_audio.wav")
            self.temp_files.append(self.temp_audio_path)

            try:
                extract_audio_from_video(self.input_path, self.temp_audio_path)
                self.audio_for_analysis = self.temp_audio_path
                return True
            except subprocess.CalledProcessError:
                print(f"Warning: Could not extract audio from {self.input_path.name}")
                print("Skipping audio analysis for this file.")
                return False
        elif self.is_audio:
            self.audio_for_analysis = str(self.input_path)
            return True

        return False

    def cleanup(self):
        """Clean up temporary files and GPU memory."""
        cleanup_temp_files(self.temp_files)
        cleanup_gpu_memory()


def analyze_audio_content(context: ProcessingContext) -> Optional[Dict[str, Any]]:
    """
    Analyze audio content for voice segments and speakers.

    Args:
        context: Processing context with audio path and model

    Returns:
        Dictionary containing analysis results or None
    """
    if not context.audio_for_analysis:
        return None

    model = context.load_model_if_needed()

    # Import here to avoid circular imports
    from .audio_analysis import _handle_speaker_and_timeline_analysis

    try:
        return _handle_speaker_and_timeline_analysis(context.args, context.input_path, model)
    except Exception as e:
        print(f"Error during audio analysis: {e}")
        return None


def save_timeline_if_requested(timeline_data: Dict[str, Any], args, input_path: Path) -> bool:
    """
    Save timeline data to file if requested.

    Args:
        timeline_data: Timeline data to save
        args: Command arguments
        input_path: Input file path for generating timeline path

    Returns:
        True if timeline was saved successfully, False otherwise
    """
    if not timeline_data or not args.generate_timeline:
        return False

    try:
        if hasattr(args, "timeline_output") and args.timeline_output:
            timeline_output_path = Path(args.timeline_output)
        else:
            # Auto-generate timeline path
            base_path = Path(args.output) if args.output else input_path
            timeline_output_path = base_path.parent / f"{base_path.stem}_timeline.yaml"

        timeline_output_path.parent.mkdir(parents=True, exist_ok=True)
        save_yaml(timeline_data, timeline_output_path)
        print(f"Timeline saved: {timeline_output_path}")
        return True
    except Exception as e:
        print(f"Warning: Failed to save timeline: {e}")
        return False


def apply_media_effects(context: ProcessingContext, timeline_data: Dict[str, Any], output_path: Path) -> bool:
    """
    Apply media effects based on timeline data.

    Args:
        context: Processing context
        timeline_data: Timeline with effect information
        output_path: Output file path

    Returns:
        True if effects were applied successfully, False otherwise
    """
    from .media_processing import extract_segments_by_effects, process_media_with_effects

    try:
        # Extract effect segments from timeline
        effect_segments = extract_segments_by_effects(timeline_data)

        if effect_segments:
            # Apply effects to create processed file
            process_media_with_effects(context.input_path, output_path, effect_segments)
            print(f"Created processed file: {output_path}")
            return True
        else:
            print("No effects to apply - copying original file")
            import shutil

            shutil.copy2(context.input_path, output_path)
            return True

    except Exception as e:
        print(f"Error applying media effects: {e}")
        return False
