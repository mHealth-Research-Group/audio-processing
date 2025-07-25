import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv
from .commands import (
    process_directory,
    process_single_file,
    apply_timeline_edits_command,
    apply_blank_command,
)
from .utils import ensure_utf8_encoding

load_dotenv()


def add_process_arguments(parser):
    """Add arguments for the 'process' command to the parser."""
    parser.add_argument("input_path", help="Path to input audio/video file or directory")
    parser.add_argument(
        "-o",
        "--output",
        help="Path for output file or directory.",
    )

    # Convenience flag for complete processing
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Enable complete processing (equivalent to --merge-videos --generate-timeline --analyze-speakers)",
    )

    parser.add_argument(
        "--min-duration-on",
        type=float,
        default=0.05,
        help="Minimum duration for speech regions in seconds.",
    )
    parser.add_argument(
        "--min-duration-off",
        type=float,
        default=0.2,
        help="Minimum duration for non-speech regions in seconds.",
    )
    parser.add_argument(
        "--analyze-speakers",
        action="store_true",
        help="Analyze and report if multiple speakers are detected.",
    )
    parser.add_argument(
        "--detailed-analysis",
        action="store_true",
        help="Use advanced speaker analysis (slower but more accurate).",
    )
    parser.add_argument(
        "--speaker-analysis-only",
        action="store_true",
        help="Only perform speaker analysis without generating output files.",
    )
    parser.add_argument(
        "--generate-timeline",
        action="store_true",
        help="Generate a timeline JSON file with detected segments.",
    )
    parser.add_argument(
        "--timeline-output",
        help="Custom path for timeline output file.",
    )

    # Video merging options
    parser.add_argument(
        "--merge-videos",
        action="store_true",
        help="Enable video merging for timestamped videos in directory.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Only merge videos without additional speech processing.",
    )
    parser.add_argument(
        "--force-overwrite",
        "-f",
        action="store_true",
        help="Overwrite existing merged videos without prompting.",
    )
    parser.add_argument(
        "--min-gap-threshold",
        type=float,
        default=2,
        help="Minimum gap duration (seconds) to fill with black frames when merging videos.",
    )
    parser.add_argument(
        "--max-gap-threshold",
        type=float,
        default=None,
        help="Maximum gap duration (seconds) to fill with black frames when merging videos.",
    )
    parser.add_argument(
        "--blank-video",
        required=False,
        help="Path to blank video file to use for gap filling (required for merging).",
    )


def main():
    """Main function to handle command-line arguments and execute commands."""
    ensure_utf8_encoding()
    try:
        parser = argparse.ArgumentParser(description="Process and edit audio/video files based on speaker analysis.")
        subparsers = parser.add_subparsers(dest="mode", help="Processing mode")

        process_parser = subparsers.add_parser("process", help="Process a file or directory")
        add_process_arguments(process_parser)

        edit_parser = subparsers.add_parser("apply-edits", help="Apply timeline-based edits")
        edit_parser.add_argument("directory", help="Directory with timelines and media")
        edit_parser.add_argument("--output-suffix", default="_edited", help="Suffix for output")
        edit_parser.add_argument(
            "--effect-labels",
            nargs="+",
            help="Labels to apply effects to (e.g., speaking)",
        )

        blank_parser = subparsers.add_parser("apply-blank", help="Replace timeline segments with blank video")
        blank_parser.add_argument("input_video", help="Path to input video file")
        blank_parser.add_argument("timeline", help="Path to timeline JSON file")
        blank_parser.add_argument("--blank-video", required=True, help="Path to blank video file")
        blank_parser.add_argument("-o", "--output", help="Path for output file")

        args_list = sys.argv[1:]
        if not args_list or args_list[0] not in [
            "process",
            "apply-edits",
            "apply-blank",
        ]:
            if args_list and (Path(args_list[0]).exists() or Path(args_list[0]).is_dir()):
                args_list.insert(0, "process")
            elif not args_list or "-h" in args_list or "--help" in args_list:
                pass
            else:
                args_list.insert(0, "process")

        args = parser.parse_args(args_list)

        # Handle --complete flag by setting individual flags
        if hasattr(args, "complete") and args.complete:
            args.merge_videos = True
            args.generate_timeline = True
            args.analyze_speakers = True
            print("Complete processing enabled: --merge-videos --generate-timeline --analyze-speakers")

        if args.mode == "process":
            if Path(args.input_path).is_dir():
                return process_directory(args)
            else:
                return process_single_file(args)
        elif args.mode == "apply-edits":
            return apply_timeline_edits_command(args)
        elif args.mode == "apply-blank":
            return apply_blank_command(args)
        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
