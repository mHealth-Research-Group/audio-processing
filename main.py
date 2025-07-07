from pyannote.audio.pipelines import VoiceActivityDetection
from pyannote.audio.pipelines import OverlappedSpeechDetection
from pyannote.audio import Model
from pyannote.audio.utils.powerset import Powerset
import os
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import tempfile
import torch
import json

load_dotenv()

# Video file extensions
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}


def load_model():
    """Load the pyannote segmentation model."""
    token = os.getenv("HUGGINGFACE_ACCESS_TOKEN")
    if not token:
        raise ValueError("HUGGINGFACE_ACCESS_TOKEN environment variable is required")

    return Model.from_pretrained("pyannote/segmentation-3.0", use_auth_token=token)


def detect_voice_segments(audio_path, model, min_duration_on=0.1, min_duration_off=0.1):
    """
    Detect voice activity segments in audio file.

    Args:
        audio_path: Path to input audio file
        model: Pyannote segmentation model
        min_duration_on: Minimum duration for speech regions (seconds)
        min_duration_off: Minimum duration for non-speech regions (seconds)

    Returns:
        List of (start_time, end_time) tuples for voice segments
    """
    pipeline = VoiceActivityDetection(segmentation=model)

    hyper_parameters = {
        "min_duration_on": min_duration_on,
        "min_duration_off": min_duration_off,
    }
    pipeline.instantiate(hyper_parameters)

    # Run voice activity detection
    vad_result = pipeline(audio_path)

    # Extract voice segments as (start, end) tuples
    voice_segments = []
    for segment in vad_result.itersegments():
        voice_segments.append((segment.start, segment.end))

    return voice_segments


def detect_multiple_speakers(
    audio_path, model, min_duration_on=0.1, min_duration_off=0.1
):
    """
    Detect if there are multiple speakers in the audio file.

    Args:
        audio_path: Path to input audio file
        model: Pyannote segmentation model
        min_duration_on: Minimum duration for speech regions (seconds)
        min_duration_off: Minimum duration for non-speech regions (seconds)

    Returns:
        dict: Contains speaker analysis results with keys:
            - 'has_multiple_speakers': bool indicating if multiple speakers detected
            - 'overlapped_speech_segments': list of overlapped speech segments
            - 'overlapped_speech_duration': total duration of overlapped speech
            - 'total_speech_duration': total duration of all speech
            - 'overlap_percentage': percentage of speech that is overlapped
    """
    # Detect overlapped speech (indicates multiple speakers talking simultaneously)
    osd_pipeline = OverlappedSpeechDetection(segmentation=model)

    hyper_parameters = {
        "min_duration_on": min_duration_on,
        "min_duration_off": min_duration_off,
    }
    osd_pipeline.instantiate(hyper_parameters)

    # Run overlapped speech detection
    overlapped_result = osd_pipeline(audio_path)

    # Extract overlapped speech segments
    overlapped_segments = []
    overlapped_duration = 0
    for segment in overlapped_result.itersegments():
        overlapped_segments.append((segment.start, segment.end))
        overlapped_duration += segment.end - segment.start

    # Also get regular voice activity detection for comparison
    vad_pipeline = VoiceActivityDetection(segmentation=model)
    vad_pipeline.instantiate(hyper_parameters)
    vad_result = vad_pipeline(audio_path)

    # Calculate total speech duration
    total_speech_duration = 0
    for segment in vad_result.itersegments():
        total_speech_duration += segment.end - segment.start

    # Calculate overlap percentage
    overlap_percentage = (
        (overlapped_duration / total_speech_duration * 100)
        if total_speech_duration > 0
        else 0
    )

    # Determine if multiple speakers are present
    # Multiple speakers are likely if:
    # 1. There are overlapped speech segments
    # 2. The overlap percentage is significant (>5% of total speech)
    has_multiple_speakers = len(overlapped_segments) > 0 and overlap_percentage > 5.0

    return {
        "has_multiple_speakers": has_multiple_speakers,
        "overlapped_speech_segments": overlapped_segments,
        "overlapped_speech_duration": overlapped_duration,
        "total_speech_duration": total_speech_duration,
        "overlap_percentage": overlap_percentage,
    }


def analyze_speaker_segments_direct(audio_path, model, chunk_duration=10.0):
    """
    Analyze audio segments directly using the segmentation model to detect multiple speakers.
    This method processes the audio in chunks and analyzes the raw model output.

    Args:
        audio_path: Path to input audio file
        model: Pyannote segmentation model
        chunk_duration: Duration of each chunk to process (seconds)

    Returns:
        dict: Contains detailed speaker analysis with keys:
            - 'has_multiple_speakers': bool indicating if multiple speakers detected
            - 'max_speakers_detected': maximum number of speakers detected in any chunk
            - 'speaker_segments': list of segments with speaker information
            - 'confidence_score': confidence in multi-speaker detection
    """
    try:
        import torchaudio

        # Load audio file
        waveform, sample_rate = torchaudio.load(audio_path)

        # Ensure mono audio at 16kHz (model requirement)
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)

        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000)
            waveform = resampler(waveform)
            sample_rate = 16000

        # Calculate chunk size in samples
        chunk_size = int(chunk_duration * sample_rate)
        total_samples = waveform.shape[1]

        # Process audio in chunks
        max_speakers_detected = 0
        speaker_segments = []
        multi_speaker_chunks = 0
        total_chunks = 0

        # Initialize powerset decoder
        max_speakers_per_chunk = 3
        max_speakers_per_frame = 2
        to_multilabel = Powerset(
            max_speakers_per_chunk, max_speakers_per_frame
        ).to_multilabel

        for start_sample in range(0, total_samples, chunk_size):
            end_sample = min(start_sample + chunk_size, total_samples)
            chunk = waveform[:, start_sample:end_sample]

            # Pad chunk to 10 seconds if necessary
            if chunk.shape[1] < chunk_size:
                padding = chunk_size - chunk.shape[1]
                chunk = torch.nn.functional.pad(chunk, (0, padding))

            # Add batch dimension
            chunk = chunk.unsqueeze(0)

            # Run model inference
            with torch.no_grad():
                powerset_output = model(chunk)
                multilabel_output = to_multilabel(powerset_output)

            # Analyze the output
            # multilabel_output shape: (batch, frames, speakers)
            # Check for multiple active speakers
            speaker_activity = multilabel_output.squeeze(0)  # Remove batch dimension

            # Count active speakers per frame
            active_speakers_per_frame = (speaker_activity > 0.5).sum(dim=1)
            max_speakers_in_chunk = active_speakers_per_frame.max().item()

            # Update statistics
            max_speakers_detected = max(max_speakers_detected, max_speakers_in_chunk)
            total_chunks += 1

            if max_speakers_in_chunk > 1:
                multi_speaker_chunks += 1

                # Find time segments with multiple speakers
                chunk_start_time = start_sample / sample_rate
                frame_duration = chunk_duration / speaker_activity.shape[0]

                for frame_idx, num_speakers in enumerate(active_speakers_per_frame):
                    if num_speakers > 1:
                        frame_start = chunk_start_time + frame_idx * frame_duration
                        frame_end = frame_start + frame_duration
                        speaker_segments.append(
                            {
                                "start": frame_start,
                                "end": frame_end,
                                "num_speakers": num_speakers.item(),
                            }
                        )

        # Calculate confidence score
        confidence_score = (
            (multi_speaker_chunks / total_chunks) if total_chunks > 0 else 0
        )

        # Determine if multiple speakers are present
        has_multiple_speakers = max_speakers_detected > 1 and confidence_score > 0.1

        return {
            "has_multiple_speakers": has_multiple_speakers,
            "max_speakers_detected": max_speakers_detected,
            "speaker_segments": speaker_segments,
            "confidence_score": confidence_score,
            "multi_speaker_chunks": multi_speaker_chunks,
            "total_chunks": total_chunks,
        }

    except Exception as e:
        print(f"Warning: Direct speaker analysis failed: {e}")
        return {
            "has_multiple_speakers": False,
            "max_speakers_detected": 0,
            "speaker_segments": [],
            "confidence_score": 0,
            "multi_speaker_chunks": 0,
            "total_chunks": 0,
        }


def create_ffmpeg_filter(voice_segments, audio_duration):
    """
    Create ffmpeg filter to zero out voice segments.

    Args:
        voice_segments: List of (start_time, end_time) tuples
        audio_duration: Total duration of audio in seconds

    Returns:
        ffmpeg filter string
    """
    if not voice_segments:
        return None

    # Create volume filter to mute voice segments
    filter_parts = []
    for start, end in voice_segments:
        # Use volume filter to set volume to 0 for voice segments
        filter_parts.append(f"volume=0:enable='between(t,{start},{end})'")

    # Chain all volume filters
    return ",".join(filter_parts)


def process_audio_with_ffmpeg(input_path, output_path, voice_segments):
    """
    Process audio file with ffmpeg to zero out voice segments.

    Args:
        input_path: Path to input audio file
        output_path: Path for output audio file
        voice_segments: List of (start_time, end_time) tuples for voice segments
    """
    if not voice_segments:
        print("No voice segments detected. Copying original file.")
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"],
            check=True,
        )
        return

    # Get audio duration using ffprobe
    duration_cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(input_path),
    ]
    duration_result = subprocess.run(
        duration_cmd, capture_output=True, text=True, check=True
    )
    audio_duration = float(duration_result.stdout.strip())

    # Create ffmpeg filter
    volume_filter = create_ffmpeg_filter(voice_segments, audio_duration)

    # Build ffmpeg command
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-af",
        volume_filter,
        "-c:a",
        "libmp3lame",  # Use MP3 codec for output
        "-b:a",
        "192k",  # Set bitrate
        str(output_path),
        "-y",
    ]

    print(f"Processing audio with {len(voice_segments)} voice segments to zero out...")
    print(f"Voice segments: {voice_segments}")

    # Run ffmpeg
    subprocess.run(ffmpeg_cmd, check=True)


def is_video_file(file_path):
    """Check if the file is a video file based on its extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(file_path):
    """Check if the file is an audio file based on its extension."""
    return Path(file_path).suffix.lower() in AUDIO_EXTENSIONS


def extract_audio_from_video(video_path, audio_path):
    """
    Extract audio from video file using ffmpeg.

    Args:
        video_path: Path to input video file
        audio_path: Path for extracted audio file
    """
    print(f"Extracting audio from video: {video_path}")

    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vn",  # No video
        "-acodec",
        "pcm_s16le",  # Use uncompressed audio for better processing
        "-ar",
        "16000",  # Sample rate suitable for pyannote
        "-ac",
        "1",  # Mono audio
        str(audio_path),
        "-y",
    ]

    subprocess.run(ffmpeg_cmd, check=True)
    print(f"Audio extracted to: {audio_path}")


def process_video_with_ffmpeg(input_path, output_path, voice_segments):
    """
    Process video file with ffmpeg to zero out voice segments in audio track.

    Args:
        input_path: Path to input video file
        output_path: Path for output video file
        voice_segments: List of (start_time, end_time) tuples for voice segments
    """
    if not voice_segments:
        print("No voice segments detected. Copying original file.")
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"],
            check=True,
        )
        return

    # Create ffmpeg filter for audio
    volume_filter = create_ffmpeg_filter(
        voice_segments, None
    )  # Duration not needed for video processing

    # Build ffmpeg command for video processing
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-c:v",
        "copy",  # Copy video stream without re-encoding
        "-af",
        volume_filter,  # Apply audio filter
        "-c:a",
        "aac",  # Use AAC codec for audio
        "-b:a",
        "192k",  # Set audio bitrate
        str(output_path),
        "-y",
    ]

    print(f"Processing video with {len(voice_segments)} voice segments to zero out...")
    print(f"Voice segments: {voice_segments}")

    # Run ffmpeg
    subprocess.run(ffmpeg_cmd, check=True)


def generate_speaker_timeline(
    audio_path, model, min_duration_on=0.1, min_duration_off=0.1, frame_duration=0.1
):
    """
    Generate a detailed timeline of speaker activity for JSON output.

    Args:
        audio_path: Path to input audio file
        model: Pyannote segmentation model
        min_duration_on: Minimum duration for speech regions (seconds)
        min_duration_off: Minimum duration for non-speech regions (seconds)
        frame_duration: Duration of each analysis frame (seconds)

    Returns:
        dict: Contains timeline analysis with keys:
            - 'timeline': list of segments with timestamps and speaker info
            - 'summary': overall statistics
            - 'has_multiple_speakers': boolean indicating if multiple speakers detected
    """
    try:
        import torchaudio

        # Get voice activity detection
        vad_pipeline = VoiceActivityDetection(segmentation=model)
        hyper_parameters = {
            "min_duration_on": min_duration_on,
            "min_duration_off": min_duration_off,
        }
        vad_pipeline.instantiate(hyper_parameters)
        vad_result = vad_pipeline(audio_path)

        # Get overlapped speech detection
        osd_pipeline = OverlappedSpeechDetection(segmentation=model)
        osd_pipeline.instantiate(hyper_parameters)
        overlapped_result = osd_pipeline(audio_path)

        # Convert results to lists for easier processing
        voice_segments = [
            (segment.start, segment.end) for segment in vad_result.itersegments()
        ]
        overlapped_segments = [
            (segment.start, segment.end) for segment in overlapped_result.itersegments()
        ]

        # Debug output
        print(f"   Voice segments detected: {len(voice_segments)}")
        print(f"   Overlapped segments detected: {len(overlapped_segments)}")
        if voice_segments:
            print(f"   First voice segment: {voice_segments[0]}")
        if overlapped_segments:
            print(f"   First overlap segment: {overlapped_segments[0]}")

        # Load audio for detailed analysis
        waveform, sample_rate = torchaudio.load(audio_path)

        # Ensure mono audio at 16kHz
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)

        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000)
            waveform = resampler(waveform)
            sample_rate = 16000

        # Get total duration
        total_duration = waveform.shape[1] / sample_rate
        print(f"   Total audio duration: {total_duration:.3f} seconds")

        # Initialize powerset decoder for detailed analysis
        max_speakers_per_chunk = 3
        max_speakers_per_frame = 2
        to_multilabel = Powerset(
            max_speakers_per_chunk, max_speakers_per_frame
        ).to_multilabel

        # Process audio in chunks for speaker counting
        chunk_duration = 10.0
        chunk_size = int(chunk_duration * sample_rate)
        speaker_counts_timeline = []

        for start_sample in range(0, waveform.shape[1], chunk_size):
            end_sample = min(start_sample + chunk_size, waveform.shape[1])
            chunk = waveform[:, start_sample:end_sample]

            # Pad chunk if necessary
            if chunk.shape[1] < chunk_size:
                padding = chunk_size - chunk.shape[1]
                chunk = torch.nn.functional.pad(chunk, (0, padding))

            chunk = chunk.unsqueeze(0)  # Add batch dimension

            # Run model inference
            with torch.no_grad():
                powerset_output = model(chunk)
                multilabel_output = to_multilabel(powerset_output)

            speaker_activity = multilabel_output.squeeze(0)
            active_speakers_per_frame = (speaker_activity > 0.5).sum(dim=1)

            # Map frame-level speaker counts to timeline
            chunk_start_time = start_sample / sample_rate
            frames_per_chunk = speaker_activity.shape[0]

            for frame_idx, num_speakers in enumerate(active_speakers_per_frame):
                frame_start = chunk_start_time + (
                    frame_idx * chunk_duration / frames_per_chunk
                )
                frame_end = min(
                    frame_start + (chunk_duration / frames_per_chunk), total_duration
                )
                speaker_counts_timeline.append(
                    {
                        "start": frame_start,
                        "end": frame_end,
                        "speaker_count": num_speakers.item(),
                    }
                )

        # Generate timeline segments
        timeline = []

        # Create a comprehensive timeline by merging all analysis
        all_events = []

        # Add voice activity events
        for start, end in voice_segments:
            all_events.append({"time": start, "type": "voice_start"})
            all_events.append({"time": end, "type": "voice_end"})

        # Add overlapped speech events
        for start, end in overlapped_segments:
            all_events.append({"time": start, "type": "overlap_start"})
            all_events.append({"time": end, "type": "overlap_end"})

        # Sort events by time
        all_events.sort(key=lambda x: x["time"])

        print(f"   Total events to process: {len(all_events)}")

        # Track current state
        is_voice_active = False
        is_overlapped = False
        segment_start = 0.0

        def get_speaker_count_at_time(time):
            """Get estimated speaker count at specific time."""
            for segment in speaker_counts_timeline:
                if segment["start"] <= time < segment["end"]:
                    return segment["speaker_count"]
            return 0

        def seconds_to_mmss(seconds):
            """Convert seconds to MM:SS format."""
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}:{secs:06.3f}"

        def create_segment(start, end, voice_active, overlapped):
            """Create a timeline segment."""
            duration = end - start

            base_segment = {
                "start": seconds_to_mmss(start),
                "end": seconds_to_mmss(end),
                "duration": seconds_to_mmss(duration),
                "duration_seconds": duration,
                "start_seconds": start,
                "end_seconds": end,
            }

            if not voice_active:
                base_segment.update(
                    {
                        "type": "silence",
                        "speakers": 0,
                        "label": "silence",
                    }
                )
                return base_segment

            speaker_count = max(
                get_speaker_count_at_time(start), get_speaker_count_at_time(end)
            )

            if overlapped or speaker_count > 1:
                base_segment.update(
                    {
                        "type": "speech",
                        "speakers": max(speaker_count, 2),
                        "label": "conversation",
                    }
                )
            else:
                base_segment.update(
                    {
                        "type": "speech",
                        "speakers": max(speaker_count, 1),
                        "label": "speaking",
                    }
                )
            return base_segment

        # Process events to create timeline
        for event in all_events:
            event_time = event["time"]

            # Create segment for the period before this event
            if event_time > segment_start:
                segment = create_segment(
                    segment_start, event_time, is_voice_active, is_overlapped
                )
                if (
                    segment["duration_seconds"] > 0.05
                ):  # Only include segments longer than 50ms
                    timeline.append(segment)

            # Update state based on event
            if event["type"] == "voice_start":
                is_voice_active = True
            elif event["type"] == "voice_end":
                is_voice_active = False
            elif event["type"] == "overlap_start":
                is_overlapped = True
            elif event["type"] == "overlap_end":
                is_overlapped = False

            segment_start = event_time

        # Add final segment if needed
        if segment_start < total_duration:
            segment = create_segment(
                segment_start, total_duration, is_voice_active, is_overlapped
            )
            if segment["duration_seconds"] > 0.05:
                timeline.append(segment)

        # If no timeline was created (no voice activity detected), create a single silence segment
        if not timeline and total_duration > 0:
            timeline.append(create_segment(0.0, total_duration, False, False))

        print(f"   Timeline segments created: {len(timeline)}")

        # Calculate summary statistics (use raw seconds for calculations)
        total_speech_time = sum(
            seg["duration_seconds"] for seg in timeline if seg["type"] == "speech"
        )
        total_conversation_time = sum(
            seg["duration_seconds"]
            for seg in timeline
            if seg["label"] == "conversation"
        )
        total_speaking_time = sum(
            seg["duration_seconds"] for seg in timeline if seg["label"] == "speaking"
        )
        total_silence_time = sum(
            seg["duration_seconds"] for seg in timeline if seg["type"] == "silence"
        )

        has_multiple_speakers = any(seg["speakers"] > 1 for seg in timeline)

        # Clean up timeline segments (remove internal duration_seconds field)
        clean_timeline = []
        for segment in timeline:
            clean_segment = segment.copy()
            for key in ["duration_seconds", "start_seconds", "end_seconds"]:
                if key in clean_segment:
                    del clean_segment[key]
            clean_timeline.append(clean_segment)

        summary = {
            "total_duration": seconds_to_mmss(total_duration),
            "total_speech_time": seconds_to_mmss(total_speech_time),
            "total_conversation_time": seconds_to_mmss(total_conversation_time),
            "total_speaking_time": seconds_to_mmss(total_speaking_time),
            "total_silence_time": seconds_to_mmss(total_silence_time),
            "speech_percentage": round((total_speech_time / total_duration) * 100, 1)
            if total_duration > 0
            else 0,
            "conversation_percentage": round(
                (total_conversation_time / total_duration) * 100, 1
            )
            if total_duration > 0
            else 0,
            "has_multiple_speakers": has_multiple_speakers,
            "num_segments": len(timeline),
        }

        return {
            "timeline": clean_timeline,
            "summary": summary,
            "has_multiple_speakers": has_multiple_speakers,
            "internal_timeline": timeline,  # Keep for internal use
        }

    except Exception as e:
        print(f"Warning: Timeline generation failed: {e}")
        return {
            "timeline": [],
            "summary": {
                "total_duration": "0:00.000",
                "total_speech_time": "0:00.000",
                "total_conversation_time": "0:00.000",
                "total_speaking_time": "0:00.000",
                "total_silence_time": "0:00.000",
                "speech_percentage": 0,
                "conversation_percentage": 0,
                "has_multiple_speakers": False,
                "num_segments": 0,
            },
            "has_multiple_speakers": False,
            "internal_timeline": [],
        }


def main():
    """Main function to process audio/video file and zero out voice segments."""
    parser = argparse.ArgumentParser(
        description="Remove voice segments from audio or video files using pyannote.audio and ffmpeg"
    )
    parser.add_argument("input_file", help="Path to input audio or video file")
    parser.add_argument(
        "-o",
        "--output",
        help="Path for output file (default: input_filename_no_conversations.ext)",
    )
    parser.add_argument(
        "--min-duration-on",
        type=float,
        default=0.1,
        help="Minimum duration for speech regions in seconds (default: 0.1)",
    )
    parser.add_argument(
        "--min-duration-off",
        type=float,
        default=0.1,
        help="Minimum duration for non-speech regions in seconds (default: 0.1)",
    )
    parser.add_argument(
        "--analyze-speakers",
        action="store_true",
        help="Analyze and report if multiple speakers are detected",
    )
    parser.add_argument(
        "--speaker-analysis-only",
        action="store_true",
        help="Only analyze speakers without processing the file",
    )
    parser.add_argument(
        "--detailed-analysis",
        action="store_true",
        help="Use detailed direct model analysis for speaker detection",
    )
    parser.add_argument(
        "--generate-timeline",
        action="store_true",
        help="Generate JSON timeline with speaker analysis",
    )
    parser.add_argument(
        "--timeline-output",
        help="Path for timeline JSON file (default: input_filename_timeline.json)",
    )

    args = parser.parse_args()

    # Validate input file
    input_path = Path(args.input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Check if input is video or audio
    is_video = is_video_file(input_path)
    is_audio = is_audio_file(input_path)

    if not (is_video or is_audio):
        raise ValueError(
            f"Unsupported file format. Supported formats: {VIDEO_EXTENSIONS | AUDIO_EXTENSIONS}"
        )

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        if is_video:
            output_path = (
                input_path.parent
                / f"{input_path.stem}_no_conversations{input_path.suffix}"
            )
        else:
            output_path = input_path.parent / f"{input_path.stem}_no_conversations.mp3"

    try:
        print("Loading pyannote model...")
        model = load_model()

        # Handle video files
        if is_video:
            # Create temporary file for extracted audio
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio_path = temp_audio.name

            try:
                # Extract audio from video
                extract_audio_from_video(input_path, temp_audio_path)

                # Perform speaker analysis if requested
                if args.analyze_speakers or args.speaker_analysis_only:
                    print(f"\n🔍 Analyzing speakers in video: {input_path}")

                    if args.detailed_analysis:
                        speaker_analysis = analyze_speaker_segments_direct(
                            temp_audio_path, model
                        )
                        print("📊 Detailed Speaker Analysis Results:")
                        print(
                            f"   Multiple speakers detected: {'✓ YES' if speaker_analysis['has_multiple_speakers'] else '✗ NO'}"
                        )
                        print(
                            f"   Maximum speakers detected: {speaker_analysis['max_speakers_detected']}"
                        )
                        print(
                            f"   Confidence score: {speaker_analysis['confidence_score']:.2f}"
                        )
                        print(
                            f"   Multi-speaker chunks: {speaker_analysis['multi_speaker_chunks']}/{speaker_analysis['total_chunks']}"
                        )
                        if speaker_analysis["speaker_segments"]:
                            print(
                                f"   Multi-speaker segments: {len(speaker_analysis['speaker_segments'])} segments"
                            )
                    else:
                        speaker_analysis = detect_multiple_speakers(
                            temp_audio_path,
                            model,
                            args.min_duration_on,
                            args.min_duration_off,
                        )
                        print("📊 Speaker Analysis Results:")
                        print(
                            f"   Multiple speakers detected: {'✓ YES' if speaker_analysis['has_multiple_speakers'] else '✗ NO'}"
                        )
                        print(
                            f"   Total speech duration: {speaker_analysis['total_speech_duration']:.2f} seconds"
                        )
                        print(
                            f"   Overlapped speech duration: {speaker_analysis['overlapped_speech_duration']:.2f} seconds"
                        )
                        print(
                            f"   Overlap percentage: {speaker_analysis['overlap_percentage']:.1f}%"
                        )
                        if speaker_analysis["overlapped_speech_segments"]:
                            print(
                                f"   Overlapped speech segments: {len(speaker_analysis['overlapped_speech_segments'])} segments"
                            )

                    print()  # Add blank line for readability

                # Generate timeline if requested
                timeline_data = None
                voice_segments = None
                if args.generate_timeline:
                    print("🕒 Generating speaker timeline...")
                    timeline_data = generate_speaker_timeline(
                        temp_audio_path,
                        model,
                        args.min_duration_on,
                        args.min_duration_off,
                    )

                    # Extract voice segments from the detailed timeline
                    if timeline_data and "internal_timeline" in timeline_data:
                        voice_segments = [
                            (s["start_seconds"], s["end_seconds"])
                            for s in timeline_data["internal_timeline"]
                            if s["type"] == "speech"
                        ]

                    # Determine timeline output path
                    if args.timeline_output:
                        timeline_path = Path(args.timeline_output)
                    else:
                        timeline_path = (
                            input_path.parent / f"{input_path.stem}_timeline.json"
                        )

                    # Save timeline to JSON
                    with open(timeline_path, "w") as f:
                        # Prepare data for JSON output (without internal fields)
                        json_output = {
                            "timeline": timeline_data["timeline"],
                            "summary": timeline_data["summary"],
                        }
                        json.dump(json_output, f, indent=2)

                    print(f"📄 Timeline saved to: {timeline_path}")
                    print(
                        f"   Total segments: {timeline_data['summary']['num_segments']}"
                    )
                    print(
                        f"   Speech time: {timeline_data['summary']['total_speech_time']}"
                    )
                    print(
                        f"   Conversation time: {timeline_data['summary']['total_conversation_time']}"
                    )
                    print()

                # Exit early if only analyzing speakers
                if args.speaker_analysis_only:
                    print("Speaker analysis completed. Exiting without processing.")
                    return 0

                if voice_segments is None:
                    print(f"Detecting voice segments in video: {input_path}")
                    voice_segments = detect_voice_segments(
                        temp_audio_path,
                        model,
                        args.min_duration_on,
                        args.min_duration_off,
                    )

                print(f"Found {len(voice_segments)} voice segments")

                print("Processing video to remove voice segments...")
                process_video_with_ffmpeg(input_path, output_path, voice_segments)

                print(
                    f"✓ Successfully created video with zeroed voice segments: {output_path}"
                )
                if timeline_data:
                    print(f"✓ Timeline data saved to: {timeline_path}")

            finally:
                # Clean up temporary audio file
                if os.path.exists(temp_audio_path):
                    os.unlink(temp_audio_path)

        # Handle audio files
        else:
            # Perform speaker analysis if requested
            if args.analyze_speakers or args.speaker_analysis_only:
                print(f"\n🔍 Analyzing speakers in audio: {input_path}")

                if args.detailed_analysis:
                    speaker_analysis = analyze_speaker_segments_direct(
                        str(input_path), model
                    )
                    print("📊 Detailed Speaker Analysis Results:")
                    print(
                        f"   Multiple speakers detected: {'✓ YES' if speaker_analysis['has_multiple_speakers'] else '✗ NO'}"
                    )
                    print(
                        f"   Maximum speakers detected: {speaker_analysis['max_speakers_detected']}"
                    )
                    print(
                        f"   Confidence score: {speaker_analysis['confidence_score']:.2f}"
                    )
                    print(
                        f"   Multi-speaker chunks: {speaker_analysis['multi_speaker_chunks']}/{speaker_analysis['total_chunks']}"
                    )
                    if speaker_analysis["speaker_segments"]:
                        print(
                            f"   Multi-speaker segments: {len(speaker_analysis['speaker_segments'])} segments"
                        )
                else:
                    speaker_analysis = detect_multiple_speakers(
                        str(input_path),
                        model,
                        args.min_duration_on,
                        args.min_duration_off,
                    )
                    print("📊 Speaker Analysis Results:")
                    print(
                        f"   Multiple speakers detected: {'✓ YES' if speaker_analysis['has_multiple_speakers'] else '✗ NO'}"
                    )
                    print(
                        f"   Total speech duration: {speaker_analysis['total_speech_duration']:.2f} seconds"
                    )
                    print(
                        f"   Overlapped speech duration: {speaker_analysis['overlapped_speech_duration']:.2f} seconds"
                    )
                    print(
                        f"   Overlap percentage: {speaker_analysis['overlap_percentage']:.1f}%"
                    )
                    if speaker_analysis["overlapped_speech_segments"]:
                        print(
                            f"   Overlapped speech segments: {len(speaker_analysis['overlapped_speech_segments'])} segments"
                        )

                print()  # Add blank line for readability

            # Generate timeline if requested
            timeline_data = None
            voice_segments = None
            if args.generate_timeline:
                print("🕒 Generating speaker timeline...")
                timeline_data = generate_speaker_timeline(
                    str(input_path), model, args.min_duration_on, args.min_duration_off
                )

                # Extract voice segments from the detailed timeline
                if timeline_data and "internal_timeline" in timeline_data:
                    voice_segments = [
                        (s["start_seconds"], s["end_seconds"])
                        for s in timeline_data["internal_timeline"]
                        if s["type"] == "speech"
                    ]

                # Determine timeline output path
                if args.timeline_output:
                    timeline_path = Path(args.timeline_output)
                else:
                    timeline_path = (
                        input_path.parent / f"{input_path.stem}_timeline.json"
                    )

                # Save timeline to JSON
                with open(timeline_path, "w") as f:
                    json_output = {
                        "timeline": timeline_data["timeline"],
                        "summary": timeline_data["summary"],
                    }
                    json.dump(json_output, f, indent=2)

                print(f"📄 Timeline saved to: {timeline_path}")
                print(f"   Total segments: {timeline_data['summary']['num_segments']}")
                print(
                    f"   Speech time: {timeline_data['summary']['total_speech_time']}"
                )
                print(
                    f"   Conversation time: {timeline_data['summary']['total_conversation_time']}"
                )
                print()

            # Exit early if only analyzing speakers
            if args.speaker_analysis_only:
                print("Speaker analysis completed. Exiting without processing.")
                return 0

            if voice_segments is None:
                print(f"Detecting voice segments in audio: {input_path}")
                voice_segments = detect_voice_segments(
                    str(input_path), model, args.min_duration_on, args.min_duration_off
                )

            print(f"Found {len(voice_segments)} voice segments")

            print("Processing audio to remove voice segments...")
            process_audio_with_ffmpeg(input_path, output_path, voice_segments)

            print(
                f"✓ Successfully created audio with zeroed voice segments: {output_path}"
            )
            if timeline_data:
                print(f"✓ Timeline data saved to: {timeline_path}")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
