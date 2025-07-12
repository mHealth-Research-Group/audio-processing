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
import sys

load_dotenv()

# Set device for PyTorch operations
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Ensure UTF-8 encoding on Windows
if sys.platform.startswith("win"):
    # Set console encoding to UTF-8 for Windows
    try:
        # For Python 3.7+
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        # For older Python versions
        import codecs

        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer)
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer)

# Video file extensions
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}


def run_subprocess_with_encoding(*args, **kwargs):
    """Helper function to run subprocess with proper encoding on Windows."""
    if sys.platform.startswith("win") and "encoding" not in kwargs and kwargs.get("text") is True:
        kwargs["encoding"] = "utf-8"
    return subprocess.run(*args, **kwargs)


def load_model():
    """Load the pyannote segmentation model and move to GPU if available."""
    token = os.getenv("HUGGINGFACE_ACCESS_TOKEN")
    if not token:
        raise ValueError("HUGGINGFACE_ACCESS_TOKEN environment variable is required")

    model = Model.from_pretrained("pyannote/segmentation-3.0", use_auth_token=token)
    model = model.to(DEVICE)
    model.eval()  # Set to evaluation mode for faster inference

    # Enable optimizations for inference
    if DEVICE.type == "cuda":
        torch.backends.cudnn.benchmark = True
        torch.backends.cudnn.deterministic = False

    return model


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


def detect_multiple_speakers(audio_path, model, min_duration_on=0.1, min_duration_off=0.1):
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
    overlap_percentage = (overlapped_duration / total_speech_duration * 100) if total_speech_duration > 0 else 0

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

        # Move waveform to GPU if available first
        waveform = waveform.to(DEVICE)
        
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000).to(DEVICE)
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
        powerset_decoder = Powerset(max_speakers_per_chunk, max_speakers_per_frame)
        if DEVICE.type == "cuda":
            powerset_decoder = powerset_decoder.to(DEVICE)
        to_multilabel = powerset_decoder.to_multilabel

        # Process in batches for better GPU utilization
        batch_size = 4 if DEVICE.type == "cuda" else 1
        chunks = []
        chunk_start_times = []

        for start_sample in range(0, total_samples, chunk_size):
            end_sample = min(start_sample + chunk_size, total_samples)
            chunk = waveform[:, start_sample:end_sample]

            # Pad chunk to 10 seconds if necessary
            if chunk.shape[1] < chunk_size:
                padding = chunk_size - chunk.shape[1]
                chunk = torch.nn.functional.pad(chunk, (0, padding))

            chunks.append(chunk)
            chunk_start_times.append(start_sample / sample_rate)

            # Process batch when full or at end
            if len(chunks) == batch_size or start_sample + chunk_size >= total_samples:
                # Stack chunks into batch
                batch = torch.stack(chunks, dim=0)

                # Run model inference with GPU acceleration
                with torch.no_grad():
                    if DEVICE.type == "cuda":
                        with torch.cuda.amp.autocast("cuda"):
                            powerset_output = model(batch)
                            multilabel_output = to_multilabel(powerset_output)
                    else:
                        powerset_output = model(batch)
                        multilabel_output = to_multilabel(powerset_output)

                # Process each chunk in the batch
                for i, chunk_start_time in enumerate(chunk_start_times):
                    speaker_activity = multilabel_output[i]  # Get i-th chunk from batch

                    # Count active speakers per frame
                    active_speakers_per_frame = (speaker_activity > 0.5).sum(dim=1)
                    max_speakers_in_chunk = active_speakers_per_frame.max().item()

                    # Update statistics
                    max_speakers_detected = max(max_speakers_detected, max_speakers_in_chunk)
                    total_chunks += 1

                    if max_speakers_in_chunk > 1:
                        multi_speaker_chunks += 1

                        # Find time segments with multiple speakers
                        frame_duration = chunk_duration / speaker_activity.shape[0]

                        for frame_idx, num_speakers in enumerate(active_speakers_per_frame):
                            if num_speakers > 1:
                                frame_start = chunk_start_time + frame_idx * frame_duration
                                frame_end = frame_start + frame_duration
                                speaker_segments.append({
                                    "start": frame_start,
                                    "end": frame_end,
                                    "num_speakers": num_speakers.item(),
                                })

                # Clear batch for next iteration
                chunks = []
                chunk_start_times = []

        # Calculate confidence score
        confidence_score = (multi_speaker_chunks / total_chunks) if total_chunks > 0 else 0

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


def generate_speaker_timeline(audio_path, model, min_duration_on=0.1, min_duration_off=0.1):
    """
    Generate a detailed timeline of speaker activity for JSON output.

    Args:
        audio_path: Path to input audio file
        model: Pyannote segmentation model
        min_duration_on: Minimum duration for speech regions (seconds)
        min_duration_off: Minimum duration for non-speech regions (seconds)

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
        voice_segments = [(segment.start, segment.end) for segment in vad_result.itersegments()]
        overlapped_segments = [(segment.start, segment.end) for segment in overlapped_result.itersegments()]

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

        # Move waveform to GPU if available first
        waveform = waveform.to(DEVICE)
        
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000).to(DEVICE)
            waveform = resampler(waveform)
            sample_rate = 16000

        # Get total duration
        total_duration = waveform.shape[1] / sample_rate
        print(f"   Total audio duration: {total_duration:.3f} seconds")

        # Initialize powerset decoder for detailed analysis
        max_speakers_per_chunk = 3
        max_speakers_per_frame = 2
        powerset_decoder = Powerset(max_speakers_per_chunk, max_speakers_per_frame)
        if DEVICE.type == "cuda":
            powerset_decoder = powerset_decoder.to(DEVICE)
        to_multilabel = powerset_decoder.to_multilabel

        # Process audio in chunks for speaker counting
        chunk_duration = 10.0
        chunk_size = int(chunk_duration * sample_rate)
        speaker_counts_timeline = []

        # Process in batches for better GPU utilization
        batch_size = 4 if DEVICE.type == "cuda" else 1
        chunks = []
        chunk_start_times = []

        for start_sample in range(0, waveform.shape[1], chunk_size):
            end_sample = min(start_sample + chunk_size, waveform.shape[1])
            chunk = waveform[:, start_sample:end_sample]

            # Pad chunk if necessary
            if chunk.shape[1] < chunk_size:
                padding = chunk_size - chunk.shape[1]
                chunk = torch.nn.functional.pad(chunk, (0, padding))

            chunks.append(chunk)
            chunk_start_times.append(start_sample / sample_rate)

            # Process batch when full or at end
            if len(chunks) == batch_size or start_sample + chunk_size >= waveform.shape[1]:
                # Stack chunks into batch
                batch = torch.stack(chunks, dim=0)

                # Run model inference with GPU acceleration
                with torch.no_grad():
                    if DEVICE.type == "cuda":
                        with torch.cuda.amp.autocast("cuda"):
                            powerset_output = model(batch)
                            multilabel_output = to_multilabel(powerset_output)
                    else:
                        powerset_output = model(batch)
                        multilabel_output = to_multilabel(powerset_output)

                # Process each chunk in the batch
                for i, chunk_start_time in enumerate(chunk_start_times):
                    speaker_activity = multilabel_output[i]
                    active_speakers_per_frame = (speaker_activity > 0.5).sum(dim=1)

                    # Map frame-level speaker counts to timeline
                    frames_per_chunk = speaker_activity.shape[0]

                    for frame_idx, num_speakers in enumerate(active_speakers_per_frame):
                        frame_start = chunk_start_time + (frame_idx * chunk_duration / frames_per_chunk)
                        frame_end = min(frame_start + (chunk_duration / frames_per_chunk), total_duration)
                        speaker_counts_timeline.append({
                            "start": frame_start,
                            "end": frame_end,
                            "speaker_count": num_speakers.item(),
                        })

                # Clear batch for next iteration
                chunks = []
                chunk_start_times = []

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
                base_segment.update({
                    "type": "silence",
                    "speakers": 0,
                    "label": "silence",
                })
                return base_segment

            speaker_count = max(get_speaker_count_at_time(start), get_speaker_count_at_time(end))

            if overlapped or speaker_count > 1:
                base_segment.update({
                    "type": "speech",
                    "speakers": max(speaker_count, 2),
                    "label": "conversation",
                })
            else:
                base_segment.update({
                    "type": "speech",
                    "speakers": max(speaker_count, 1),
                    "label": "speaking",
                })
            return base_segment

        # Process events to create timeline
        for event in all_events:
            event_time = event["time"]

            # Create segment for the period before this event
            if event_time > segment_start:
                segment = create_segment(segment_start, event_time, is_voice_active, is_overlapped)
                if segment["duration_seconds"] > 0.05:  # Only include segments longer than 50ms
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
            segment = create_segment(segment_start, total_duration, is_voice_active, is_overlapped)
            if segment["duration_seconds"] > 0.05:
                timeline.append(segment)

        # If no timeline was created (no voice activity detected), create a single silence segment
        if not timeline and total_duration > 0:
            timeline.append(create_segment(0.0, total_duration, False, False))

        print(f"   Timeline segments created: {len(timeline)}")

        # Calculate summary statistics (use raw seconds for calculations)
        total_speech_time = sum(seg["duration_seconds"] for seg in timeline if seg["type"] == "speech")
        total_conversation_time = sum(seg["duration_seconds"] for seg in timeline if seg["label"] == "conversation")
        total_speaking_time = sum(seg["duration_seconds"] for seg in timeline if seg["label"] == "speaking")
        total_silence_time = sum(seg["duration_seconds"] for seg in timeline if seg["type"] == "silence")

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
            "speech_percentage": round((total_speech_time / total_duration) * 100, 1) if total_duration > 0 else 0,
            "conversation_percentage": round((total_conversation_time / total_duration) * 100, 1) if total_duration > 0 else 0,
            "has_multiple_speakers": has_multiple_speakers,
            "num_segments": len(timeline),
        }

        return {
            "timeline": clean_timeline,
            "summary": summary,
            "has_multiple_speakers": has_multiple_speakers,
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
        }


def detect_file_encoding(file_path):
    """Debug function to detect file encoding and BOM."""
    with open(file_path, "rb") as f:
        first_bytes = f.read(10)

    print(f"First 10 bytes of {file_path}: {first_bytes}")
    print(f"Hex representation: {first_bytes.hex()}")

    if first_bytes.startswith(b"\xff\xfe"):
        print("Detected: UTF-16 LE BOM")
        return "utf-16-le"
    elif first_bytes.startswith(b"\xfe\xff"):
        print("Detected: UTF-16 BE BOM")
        return "utf-16-be"
    elif first_bytes.startswith(b"\xef\xbb\xbf"):
        print("Detected: UTF-8 BOM")
        return "utf-8-sig"
    else:
        print("No BOM detected, assuming UTF-8")
        return "utf-8"


def load_timeline(timeline_path):
    """Load timeline from JSON file."""
    # Try multiple encodings to handle Windows encoding issues
    encodings_to_try = ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin1"]

    for encoding in encodings_to_try:
        try:
            with open(timeline_path, "r", encoding=encoding) as f:
                return json.load(f)
        except (UnicodeDecodeError, UnicodeError, json.JSONDecodeError):
            continue

    # If all encodings fail, try reading as binary and decoding manually
    try:
        with open(timeline_path, "rb") as f:
            raw_data = f.read()

            # Remove BOM if present
            if raw_data.startswith(b"\xff\xfe"):
                # UTF-16 LE BOM
                text_data = raw_data[2:].decode("utf-16-le")
            elif raw_data.startswith(b"\xfe\xff"):
                # UTF-16 BE BOM
                text_data = raw_data[2:].decode("utf-16-be")
            elif raw_data.startswith(b"\xef\xbb\xbf"):
                # UTF-8 BOM
                text_data = raw_data[3:].decode("utf-8")
            else:
                # Try UTF-8 with error replacement
                text_data = raw_data.decode("utf-8", errors="replace")

            return json.loads(text_data)
    except Exception as e:
        raise ValueError(f"Could not decode timeline file {timeline_path}. Error: {e}")


def mmss_to_seconds(mmss_str):
    """Convert MM:SS.sss format to seconds."""
    parts = mmss_str.split(":")
    minutes = int(parts[0])
    seconds = float(parts[1])
    return minutes * 60 + seconds


def parse_time_range(time_str):
    """
    Parse time range string into start and end seconds.

    Supports formats:
    - "MM:SS.sss-MM:SS.sss" (range)
    - "MM:SS.sss" (single time point)
    - "SS.sss" (seconds only)
    - "SS" (integer seconds)

    Returns:
        tuple: (start_seconds, end_seconds) or (time_seconds, None) for single time
    """
    if "-" in time_str:
        # Range format
        start_str, end_str = time_str.split("-", 1)
        start_time = parse_single_time(start_str.strip())
        end_time = parse_single_time(end_str.strip())
        return (start_time, end_time)
    else:
        # Single time point
        time_seconds = parse_single_time(time_str.strip())
        return (time_seconds, None)


def parse_single_time(time_str):
    """
    Parse a single time string into seconds.

    Supports formats:
    - "MM:SS.sss"
    - "MM:SS"
    - "SS.sss"
    - "SS" (integer)
    """
    time_str = time_str.strip()

    if ":" in time_str:
        # MM:SS or MM:SS.sss format
        parts = time_str.split(":")
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    else:
        # Just seconds
        return float(time_str)


def apply_effects_to_time_ranges(input_path, output_path, time_ranges, effect_type="all"):
    """
    Apply effects to specific time ranges without needing a timeline file.

    Args:
        input_path: Path to input media file
        output_path: Path for output media file
        time_ranges: List of time range strings (e.g., ["1:30-2:45", "5:00-5:30"])
        effect_type: Type of effect to apply ("black", "mute", "all", "hide")
    """
    # Parse time ranges into segments
    segments = []
    for time_range in time_ranges:
        start_time, end_time = parse_time_range(time_range)
        if end_time is None:
            raise ValueError(f"Time range '{time_range}' must specify both start and end times (format: start-end)")
        segments.append((start_time, end_time))

    # Get effect configuration
    effects = EFFECT_CONFIGS.get(effect_type, {"mute_audio": False, "black_video": False})

    # Convert to effect_segments format
    effect_segments = {"mute_only": [], "black_only": [], "mute_and_black": []}

    if effects["mute_audio"] and effects["black_video"]:
        effect_segments["mute_and_black"] = segments
    elif effects["mute_audio"]:
        effect_segments["mute_only"] = segments
    elif effects["black_video"]:
        effect_segments["black_only"] = segments
    else:
        print(f"No effects configured for effect type '{effect_type}'. No processing will be done.")
        # Just copy the file
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"],
            check=True,
        )
        return

    # Apply the effects
    process_media_with_effects(input_path, output_path, effect_segments)


# Effect configuration for different labels
EFFECT_CONFIGS = {
    "speaking": {"mute_audio": True, "black_video": False},  # Mute single speaker segments
    "conversation": {"mute_audio": True, "black_video": False},  # Mute conversation segments
    "silence": {"mute_audio": False, "black_video": False},  # No effects for silence
    "black": {"mute_audio": False, "black_video": True},  # Black video but preserve audio
    "mute": {"mute_audio": True, "black_video": False},  # Mute audio only
    "all": {"mute_audio": True, "black_video": True},  # Remove both voice and video
}


def extract_segments_by_effects(timeline_data, target_effects=None):
    """
    Extract segments that should have specific effects applied based on their labels.

    Args:
        timeline_data: Timeline data with segments
        target_effects: Dict specifying which effects to extract for, e.g.:
                       {"mute_audio": True, "black_video": False} - extract segments to mute
                       {"mute_audio": True, "black_video": True} - extract segments to mute AND black
                       If None, extracts all segments with any effects

    Returns:
        Dict with keys for different effect combinations:
        {
            "mute_only": [(start, end), ...],    # Segments to mute audio only
            "black_only": [(start, end), ...],   # Segments to black video only
            "mute_and_black": [(start, end), ...] # Segments to both mute and black
        }
    """
    segments = {"mute_only": [], "black_only": [], "mute_and_black": []}

    for segment in timeline_data["timeline"]:
        label = segment.get("label", "")

        # Get effect configuration for this label
        effects = EFFECT_CONFIGS.get(label, {"mute_audio": False, "black_video": False})

        # Skip if no effects should be applied
        if not effects["mute_audio"] and not effects["black_video"]:
            continue

        # Skip if target_effects specified and this segment doesn't match
        if target_effects is not None:
            if effects["mute_audio"] != target_effects.get("mute_audio", False) or effects["black_video"] != target_effects.get("black_video", False):
                continue

        start_seconds = mmss_to_seconds(segment["start"])
        end_seconds = mmss_to_seconds(segment["end"])

        # Categorize based on effect combination
        if effects["mute_audio"] and effects["black_video"]:
            segments["mute_and_black"].append((start_seconds, end_seconds))
        elif effects["mute_audio"]:
            segments["mute_only"].append((start_seconds, end_seconds))
        elif effects["black_video"]:
            segments["black_only"].append((start_seconds, end_seconds))

    return segments


def create_audio_filter(mute_segments):
    """
    Create ffmpeg audio filter to mute specific segments.

    Args:
        mute_segments: List of (start_time, end_time) tuples to mute

    Returns:
        ffmpeg audio filter string or None if no segments
    """
    if not mute_segments:
        return None

    filter_parts = []
    for start, end in mute_segments:
        filter_parts.append(f"volume=0:enable='between(t,{start},{end})'")

    return ",".join(filter_parts)


def create_video_filter(black_segments):
    """
    Create ffmpeg video filter to black out specific segments.

    Args:
        black_segments: List of (start_time, end_time) tuples to black out

    Returns:
        ffmpeg video filter string or None if no segments
    """
    if not black_segments:
        return None

    filter_parts = []
    for start, end in black_segments:
        # Use drawbox filter to draw a black box over the entire video
        filter_parts.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{start},{end})'")

    return ",".join(filter_parts)


def process_media_with_effects(input_path, output_path, effect_segments):
    """
    Process media file with flexible effects (audio muting, video blacking, or both).

    Args:
        input_path: Path to input media file
        output_path: Path for output media file
        effect_segments: Dict with effect segments from extract_segments_by_effects()
    """
    # Combine all segments that need effects
    all_mute_segments = effect_segments["mute_only"] + effect_segments["mute_and_black"]
    all_black_segments = effect_segments["black_only"] + effect_segments["mute_and_black"]

    # Check if any effects need to be applied
    if not all_mute_segments and not all_black_segments:
        print("No effects to apply. Copying original file.")
        subprocess.run(
            ["ffmpeg", "-i", str(input_path), "-c", "copy", str(output_path), "-y"],
            check=True,
        )
        return

    # Create filters
    audio_filter = create_audio_filter(all_mute_segments)
    video_filter = create_video_filter(all_black_segments)

    # Build ffmpeg command
    ffmpeg_cmd = ["ffmpeg", "-i", str(input_path)]

    # Add video filter if needed
    if video_filter:
        ffmpeg_cmd.extend(["-vf", video_filter])
        ffmpeg_cmd.extend(["-c:v", "libx264", "-preset", "fast"])  # Re-encode video when filtering
    else:
        ffmpeg_cmd.extend(["-c:v", "copy"])  # Copy video stream if no video effects

    # Add audio filter if needed
    if audio_filter:
        ffmpeg_cmd.extend(["-af", audio_filter])
        ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", "192k"])  # Re-encode audio when filtering
    else:
        ffmpeg_cmd.extend(["-c:a", "copy"])  # Copy audio stream if no audio effects

    ffmpeg_cmd.extend([str(output_path), "-y"])

    # Print processing info
    effects_applied = []
    if all_mute_segments:
        effects_applied.append(f"mute {len(all_mute_segments)} segments")
    if all_black_segments:
        effects_applied.append(f"black {len(all_black_segments)} segments")

    print(f"Processing media with effects: {', '.join(effects_applied)}")
    if all_mute_segments:
        print(f"Mute segments: {all_mute_segments}")
    if all_black_segments:
        print(f"Black segments: {all_black_segments}")

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


def find_timeline_files(directory):
    """Find all timeline JSON files in a directory."""
    timeline_files = []
    directory = Path(directory)

    for file_path in directory.iterdir():
        if file_path.is_file() and file_path.name.endswith("_timeline.json"):
            timeline_files.append(file_path)

    return sorted(timeline_files)


def find_media_file_for_timeline(timeline_path):
    """Find the corresponding media file for a timeline file."""
    # Remove '_timeline.json' suffix to get base name
    base_name = timeline_path.stem.replace("_timeline", "")
    directory = timeline_path.parent

    # Look for video files first, then audio files
    for ext in VIDEO_EXTENSIONS:
        media_path = directory / f"{base_name}{ext}"
        if media_path.exists():
            return media_path

    for ext in AUDIO_EXTENSIONS:
        media_path = directory / f"{base_name}{ext}"
        if media_path.exists():
            return media_path

    return None


def apply_timeline_edits(directory, output_suffix="_edited", effect_labels=None):
    """
    Apply timeline edits to all media files in a directory based on their timeline JSON files.

    Args:
        directory: Directory containing timeline JSON files and media files
        output_suffix: Suffix for output files (default: "_edited")
        effect_labels: List of labels to apply effects to. If None, uses labels with configured effects.
                      For backward compatibility, ["speaking", "conversation"] will mute audio only.
    """
    # Find all timeline files
    timeline_files = find_timeline_files(directory)

    if not timeline_files:
        print(f"No timeline files (*_timeline.json) found in {directory}")
        return 1

    print(f"Found {len(timeline_files)} timeline files:")
    for file_path in timeline_files:
        print(f"  - {file_path.name}")
    print()

    # Load model once for all files
    print("Loading pyannote model...")
    _ = load_model()

    # Process each timeline file
    for timeline_path in timeline_files:
        print(f"Processing {timeline_path.name}...")

        # Find corresponding media file
        media_path = find_media_file_for_timeline(timeline_path)
        if not media_path:
            print(f"✗ No media file found for {timeline_path.name}")
            continue

        print(f"  Found media file: {media_path.name}")

        # Load timeline data
        try:
            timeline_data = load_timeline(timeline_path)
        except Exception as e:
            print(f"✗ Error loading timeline {timeline_path.name}: {e}")
            continue

        # Handle backward compatibility - if effect_labels specified, override config temporarily
        if effect_labels:
            # Temporarily modify effect config for backward compatibility
            original_configs = EFFECT_CONFIGS.copy()
            for label in EFFECT_CONFIGS:
                EFFECT_CONFIGS[label] = {"mute_audio": False, "black_video": False}
            for label in effect_labels:
                if label in EFFECT_CONFIGS:
                    EFFECT_CONFIGS[label] = {"mute_audio": True, "black_video": False}

        # Extract segments that need effects
        effect_segments = extract_segments_by_effects(timeline_data)

        # Count total segments with effects
        total_effect_segments = len(effect_segments["mute_only"]) + len(effect_segments["black_only"]) + len(effect_segments["mute_and_black"])

        print(f"  Found {total_effect_segments} segments with effects to apply")

        if total_effect_segments == 0:
            print("  No segments with effects found, skipping...")
            if effect_labels:
                # Restore original config
                EFFECT_CONFIGS.clear()
                EFFECT_CONFIGS.update(original_configs)
            continue

        # Create output path
        output_path = media_path.parent / f"{media_path.stem}{output_suffix}{media_path.suffix}"

        try:
            print("  Processing media file...")
            process_media_with_effects(media_path, output_path, effect_segments)
            print(f"✓ Created edited file: {output_path.name}")

        except Exception as e:
            print(f"✗ Error processing {media_path.name}: {e}")

        # Restore original config if modified for backward compatibility
        if effect_labels:
            EFFECT_CONFIGS.clear()
            EFFECT_CONFIGS.update(original_configs)

        print()

    print("✓ All files processed!")
    return 0


def add_process_arguments(parser):
    """Add arguments for the 'process' command to the parser."""
    parser.add_argument("input_path", help="Path to input audio/video file or directory")
    parser.add_argument(
        "-o",
        "--output",
        help="Path for output file (default: input_filename_no_conversations.ext). For directory processing, this is an output directory.",
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
        help="Path for timeline JSON file (default: input_filename_timeline.json). For directory processing, this is a directory.",
    )


def find_media_files(directory: Path):
    """Find all audio and video files in a directory."""
    media_files = []
    for file_path in directory.iterdir():
        if file_path.is_file() and (is_video_file(file_path) or is_audio_file(file_path)):
            media_files.append(file_path)
    return sorted(media_files)


def _handle_speaker_and_timeline_analysis(args, input_path, model):
    """Helper function to handle speaker analysis and timeline generation."""
    # Perform speaker analysis if requested
    if args.analyze_speakers or args.speaker_analysis_only:
        print(f"\n🔍 Analyzing speakers in: {input_path}")

        if args.detailed_analysis:
            speaker_analysis = analyze_speaker_segments_direct(input_path, model)
            print("📊 Detailed Speaker Analysis Results:")
            print(f"   Multiple speakers detected: {'✓ YES' if speaker_analysis['has_multiple_speakers'] else '✗ NO'}")
            print(f"   Maximum speakers detected: {speaker_analysis['max_speakers_detected']}")
            print(f"   Confidence score: {speaker_analysis['confidence_score']:.2f}")
            print(f"   Multi-speaker chunks: {speaker_analysis['multi_speaker_chunks']}/{speaker_analysis['total_chunks']}")
            if speaker_analysis["speaker_segments"]:
                print(f"   Multi-speaker segments: {len(speaker_analysis['speaker_segments'])} segments")
        else:
            speaker_analysis = detect_multiple_speakers(
                input_path,
                model,
                args.min_duration_on,
                args.min_duration_off,
            )
            print("📊 Speaker Analysis Results:")
            print(f"   Multiple speakers detected: {'✓ YES' if speaker_analysis['has_multiple_speakers'] else '✗ NO'}")
            print(f"   Total speech duration: {speaker_analysis['total_speech_duration']:.2f} seconds")
            print(f"   Overlapped speech duration: {speaker_analysis['overlapped_speech_duration']:.2f} seconds")
            print(f"   Overlap percentage: {speaker_analysis['overlap_percentage']:.1f}%")
            if speaker_analysis["overlapped_speech_segments"]:
                print(f"   Overlapped speech segments: {len(speaker_analysis['overlapped_speech_segments'])} segments")

        print()  # Add blank line for readability

    # Generate timeline if requested
    timeline_data = None
    voice_segments = None
    if args.generate_timeline:
        print("🕒 Generating speaker timeline...")
        timeline_data = generate_speaker_timeline(
            input_path,
            model,
            args.min_duration_on,
            args.min_duration_off,
        )

        timeline_output_path = None
        # Note: input_path in this context is the temporary audio file for videos
        original_input_path = Path(args.input_path)
        if args.timeline_output:
            timeline_output_path = Path(args.timeline_output)
        else:
            timeline_output_path = original_input_path.parent / f"{original_input_path.stem}_timeline.json"

        # In directory mode, adjust the timeline path
        if Path(args.input_path).is_dir():
            timeline_output_path = timeline_output_path.parent / f"{original_input_path.stem}_timeline.json"

        # Save the timeline data
        with open(timeline_output_path, "w", encoding="utf-8") as f:
            json.dump(timeline_data, f, indent=2, ensure_ascii=False)
        print(f"✓ Timeline saved to {timeline_output_path}")

        # Use timeline to extract conversation segments if not processing
        if timeline_data and "timeline" in timeline_data:
            # Reconstruct voice segments from the public timeline data
            voice_segments = []
            for segment in timeline_data["timeline"]:
                if segment["type"] == "speech":
                    start_time = mmss_to_seconds(segment["start"])
                    end_time = mmss_to_seconds(segment["end"])
                    voice_segments.append((start_time, end_time))

    return voice_segments, timeline_data


def process_single_file(args):
    """Process a single media file."""
    import sys

    input_path = Path(args.input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Check if input is video or audio
    is_video = is_video_file(input_path)
    is_audio = is_audio_file(input_path)

    if not (is_video or is_audio):
        raise ValueError(f"Unsupported file format for {input_path}. Supported formats: {VIDEO_EXTENSIONS | AUDIO_EXTENSIONS}")

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        if is_video:
            output_path = input_path.parent / f"{input_path.stem}_no_conversations{input_path.suffix}"
        else:
            output_path = input_path.parent / f"{input_path.stem}_no_conversations.mp3"

    try:
        print(f"Processing {input_path.name}...")
        print("Loading pyannote model...")
        model = load_model()

        # Set the audio path for analysis (original or temp)
        audio_for_analysis = None
        temp_audio_path = None

        try:
            # Handle video files: extract audio first
            if is_video:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                    temp_audio_path = temp_audio.name
                extract_audio_from_video(input_path, temp_audio_path)
                audio_for_analysis = temp_audio_path
            else:
                # For audio files, use the original path
                audio_for_analysis = str(input_path)

            # --- Unified Analysis and Timeline Generation ---
            voice_segments, timeline_data = _handle_speaker_and_timeline_analysis(args, audio_for_analysis, model)

            # If not generating a timeline, detect voice segments directly
            if voice_segments is None:
                print("🗣️ Detecting voice segments...")
                voice_segments = detect_voice_segments(
                    audio_for_analysis,
                    model,
                    args.min_duration_on,
                    args.min_duration_off,
                )

            # --- Processing ---
            if not args.speaker_analysis_only:
                if not voice_segments:
                    print(f"No voice segments detected in {input_path.name}. File not modified.")
                else:
                    effect_segments = {"mute_only": voice_segments, "black_only": [], "mute_and_black": []}
                    if is_video:
                        print(f"🎬 Processing video: {input_path.name}...")
                        process_media_with_effects(input_path, output_path, effect_segments)
                        print(f"✓ Video processing complete: {output_path}")
                    else:
                        print(f"🎵 Processing audio: {input_path.name}...")
                        process_media_with_effects(input_path, output_path, effect_segments)
                        print(f"✓ Audio processing complete: {output_path}")

        finally:
            # Clean up temporary audio file if it was created
            if temp_audio_path and os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)

        print("✨ Done!")
        return 0

    except Exception as e:
        print(f"An error occurred while processing {args.input_path}: {e}", file=sys.stderr)
        return 1


def process_directory(args):
    """Process all media files in a directory."""
    import sys

    input_dir = Path(args.input_path)
    if not input_dir.is_dir():
        print(f"Error: Input path {input_dir} is not a directory.", file=sys.stderr)
        return 1

    output_dir = Path(args.output) if args.output else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    timeline_dir = Path(args.timeline_output) if args.timeline_output else output_dir
    timeline_dir.mkdir(parents=True, exist_ok=True)

    media_files = find_media_files(input_dir)
    if not media_files:
        print(f"No media files found in {input_dir}")
        return 0

    print(f"Found {len(media_files)} media files in {input_dir}. Starting batch processing...")

    for media_file in media_files:
        print("-" * 50)
        file_args = argparse.Namespace(**vars(args))
        file_args.input_path = str(media_file)

        if args.output:
            file_args.output = str(output_dir / f"{media_file.stem}_processed{media_file.suffix}")
        else:
            file_args.output = None  # Let process_single_file handle default naming

        if args.timeline_output:
            file_args.timeline_output = str(timeline_dir / f"{media_file.stem}_timeline.json")
        elif args.generate_timeline:
            file_args.timeline_output = str(output_dir / f"{media_file.stem}_timeline.json")

        process_single_file(file_args)

    print("-" * 50)
    print("✓ Batch processing complete.")
    return 0


def main():
    """Main function to process audio/video file and zero out voice segments."""
    try:
        parser = argparse.ArgumentParser(description="Remove voice segments from audio or video files using pyannote.audio and ffmpeg")

        subparsers = parser.add_subparsers(dest="mode", help="Processing mode")

        # --- Process command ---
        process_parser = subparsers.add_parser("process", help="Process a single file or a directory")
        add_process_arguments(process_parser)

        # --- Apply-edits command ---
        edit_parser = subparsers.add_parser("apply-edits", help="Apply timeline-based edits to multiple files")
        edit_parser.add_argument("directory", help="Directory containing timeline JSON files and media files")
        edit_parser.add_argument(
            "--output-suffix",
            default="_edited",
            help="Suffix for output files (default: _edited)",
        )
        edit_parser.add_argument(
            "--effect-labels",
            nargs="+",
            help="Labels to apply effects to (e.g., speaking conversation). For backward compatibility, use 'speaking' and 'conversation' to mute audio only.",
        )

        # --- Apply-effects command ---
        effects_parser = subparsers.add_parser("apply-effects", help="Apply effects to specific time ranges")
        effects_parser.add_argument("input_path", help="Path to input media file")
        effects_parser.add_argument("time_ranges", nargs="+", help="Time ranges to apply effects to (e.g., '1:30-2:45' '5:00-5:30')")
        effects_parser.add_argument("-o", "--output", help="Path for output file (default: input_filename_effects.ext)")
        effects_parser.add_argument("--effect", default="all", choices=["black", "mute", "all"], help="Type of effect to apply (default: all)")

        # --- Debug command ---
        debug_parser = subparsers.add_parser("debug-encoding", help="Debug file encoding issues")
        debug_parser.add_argument("file_path", help="Path to file to analyze encoding")

        # --- Backward compatibility: if no subcommand, assume 'process' ---
        args_list = sys.argv[1:]
        if not args_list or args_list[0] not in ["process", "apply-edits", "apply-effects", "debug-encoding"]:
            # If input looks like a file/dir path, prepend 'process'
            if args_list and (Path(args_list[0]).exists() or Path(args_list[0]).is_dir()):
                args_list.insert(0, "process")
            # Handle --help for backward compatibility
            elif not args_list or "-h" in args_list or "--help" in args_list:
                # Show top-level help
                pass
            else:
                # default to process
                args_list.insert(0, "process")

        args = parser.parse_args(args_list)

        if args.mode == "process":
            input_path = Path(args.input_path)
            if input_path.is_dir():
                return process_directory(args)
            else:
                return process_single_file(args)

        elif args.mode == "apply-edits":
            directory = Path(args.directory)
            if not directory.exists() or not directory.is_dir():
                print(f"Error: Directory not found: {directory}", file=sys.stderr)
                return 1
            return apply_timeline_edits(directory, args.output_suffix, args.effect_labels)

        elif args.mode == "apply-effects":
            input_path = Path(args.input_path)
            if not input_path.exists():
                print(f"Error: Input file not found: {input_path}", file=sys.stderr)
                return 1

            if not (is_video_file(input_path) or is_audio_file(input_path)):
                print(f"Error: Unsupported file format for {input_path}", file=sys.stderr)
                return 1

            # Determine output path
            if args.output:
                output_path = Path(args.output)
            else:
                output_path = input_path.parent / f"{input_path.stem}_effects{input_path.suffix}"

            try:
                print(f"Applying {args.effect} effects to {len(args.time_ranges)} time ranges...")
                print(f"Time ranges: {', '.join(args.time_ranges)}")
                apply_effects_to_time_ranges(input_path, output_path, args.time_ranges, args.effect)
                print(f"✓ Effects applied successfully: {output_path}")
                return 0
            except Exception as e:
                print(f"Error applying effects: {e}", file=sys.stderr)
                return 1

        elif args.mode == "debug-encoding":
            file_path = Path(args.file_path)
            if not file_path.exists():
                print(f"Error: File not found: {file_path}", file=sys.stderr)
                return 1
            detected_encoding = detect_file_encoding(file_path)
            print(f"Suggested encoding: {detected_encoding}")
            return 0

        else:
            parser.print_help()
            return 1

    except UnicodeDecodeError as e:
        print(f"Unicode encoding error: {e}", file=sys.stderr)
        print("Try setting your system locale to UTF-8 or run with PYTHONIOENCODING=utf-8", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit(main())
