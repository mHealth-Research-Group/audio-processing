import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv
from .commands import (
    process_directory,
    process_single_file,
    apply_timeline_edits_command,
    apply_effects_command,
    debug_encoding_command,
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
        default=0.1,
        help="Minimum duration for speech regions in seconds.",
    )
    parser.add_argument(
        "--min-duration-off",
        type=float,
        default=0.1,
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
        "--max-gap-threshold",
        type=float,
        default=None,
        help="Maximum gap duration (seconds) to fill with black frames when merging videos.",
    )

    # H264 conversion options
    parser.add_argument(
        "--convert-h264",
        action="store_true",
        default=True,
        help="Convert final output to H264 format (default: enabled).",
    )
    parser.add_argument(
        "--no-h264",
        action="store_true",
        help="Skip H264 conversion (keeps original codec).",
    )
    parser.add_argument(
        "--h264-preset",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
        default="faster",
        help="H264 encoding preset (speed vs quality trade-off).",
    )
    parser.add_argument(
        "--h264-crf",
        type=int,
        default=28,
        help="H264 constant rate factor (0-51, lower = better quality).",
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

        effects_parser = subparsers.add_parser("apply-effects", help="Apply effects to time ranges")
        effects_parser.add_argument("input_path", help="Path to input media file")
        effects_parser.add_argument("time_ranges", nargs="+", help="Time ranges (e.g., '1:30-2:45')")
        effects_parser.add_argument("-o", "--output", help="Path for output file")
        effects_parser.add_argument("--effect", default="all", choices=["black", "mute", "all"], help="Effect to apply")

        debug_parser = subparsers.add_parser("debug-encoding", help="Debug file encoding")
        debug_parser.add_argument("file_path", help="Path to file to analyze")

        args_list = sys.argv[1:]
        if not args_list or args_list[0] not in [
            "process",
            "apply-edits",
            "apply-effects",
            "debug-encoding",
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
            print("🚀 Complete processing enabled: --merge-videos --generate-timeline --analyze-speakers")

        # Handle --no-h264 flag
        if hasattr(args, "no_h264") and args.no_h264:
            args.convert_h264 = False

        if args.mode == "process":
            if Path(args.input_path).is_dir():
                return process_directory(args)
            else:
                return process_single_file(args)
        elif args.mode == "apply-edits":
            return apply_timeline_edits_command(args)
        elif args.mode == "apply-effects":
            return apply_effects_command(args)
        elif args.mode == "debug-encoding":
            return debug_encoding_command(args)
        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
