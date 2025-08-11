import os
import warnings
import time
import torch
from pyannote.audio import Model
from pyannote.audio.pipelines import (
    OverlappedSpeechDetection,
    VoiceActivityDetection,
)
from pyannote.audio.utils.powerset import Powerset
from .utils import mmss_to_seconds, seconds_to_mmss


# Suppress pyannote TensorFloat-32 (TF32) reproducibility warning
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.utils.reproducibility")

# Set device for PyTorch operations
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def cleanup_gpu_memory():
    """Clean up GPU memory to prevent memory leaks and system crashes."""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        # Force garbage collection to free Python objects
        import gc

        gc.collect()


def load_model():
    """Load the pyannote segmentation model and move to GPU if available."""
    token = os.getenv("HUGGINGFACE_ACCESS_TOKEN")
    if not token:
        raise ValueError("HUGGINGFACE_ACCESS_TOKEN environment variable is required")

    model = Model.from_pretrained("pyannote/segmentation-3.0", use_auth_token=token)
    model = model.to(DEVICE)
    model.eval()

    if DEVICE.type == "cuda":
        torch.backends.cudnn.benchmark = True
        torch.backends.cudnn.deterministic = False
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    return model


def detect_voice_segments(audio_path, model, min_duration_on=0.1, min_duration_off=0.1):
    """Detect voice activity segments in an audio file."""
    print(f"Starting voice activity detection on {audio_path}...")
    start_time = time.time()

    pipeline = VoiceActivityDetection(segmentation=model)
    hyper_parameters = {
        "min_duration_on": min_duration_on,
        "min_duration_off": min_duration_off,
    }
    pipeline.instantiate(hyper_parameters)

    # Time the inference
    inference_start = time.time()
    vad_result = pipeline(audio_path)
    inference_time = time.time() - inference_start

    segments = [(segment.start, segment.end) for segment in vad_result.itersegments()]
    total_time = time.time() - start_time

    print("Voice activity detection completed:")
    print(f"  Inference time: {inference_time:.2f}s")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Found {len(segments)} voice segments")
    if DEVICE.type == "cuda":
        print(f"  GPU inference speed: {inference_time:.2f}s")
    else:
        print(f"  CPU inference speed: {inference_time:.2f}s")

    # Clean up GPU memory after inference
    cleanup_gpu_memory()

    return segments


def detect_multiple_speakers(audio_path, model, min_duration_on=0.1, min_duration_off=0.1):
    """Detect if there are multiple speakers in the audio file."""
    print(f"Starting multiple speaker detection on {audio_path}...")
    start_time = time.time()

    osd_pipeline = OverlappedSpeechDetection(segmentation=model)
    hyper_parameters = {
        "min_duration_on": min_duration_on,
        "min_duration_off": min_duration_off,
    }
    osd_pipeline.instantiate(hyper_parameters)

    # Time the overlapped speech detection inference
    osd_inference_start = time.time()
    overlapped_result = osd_pipeline(audio_path)
    osd_inference_time = time.time() - osd_inference_start

    overlapped_segments = []
    overlapped_duration = 0
    for segment in overlapped_result.itersegments():
        overlapped_segments.append((segment.start, segment.end))
        overlapped_duration += segment.end - segment.start

    vad_pipeline = VoiceActivityDetection(segmentation=model)
    vad_pipeline.instantiate(hyper_parameters)

    # Time the VAD inference
    vad_inference_start = time.time()
    vad_result = vad_pipeline(audio_path)
    vad_inference_time = time.time() - vad_inference_start

    total_speech_duration = sum(segment.end - segment.start for segment in vad_result.itersegments())
    overlap_percentage = (overlapped_duration / total_speech_duration * 100) if total_speech_duration > 0 else 0
    has_multiple_speakers = len(overlapped_segments) > 0 and overlap_percentage > 5.0

    total_time = time.time() - start_time

    print("Multiple speaker detection completed:")
    print(f"  Overlapped speech inference: {osd_inference_time:.2f}s")
    print(f"  VAD inference: {vad_inference_time:.2f}s")
    print(f"  Total inference time: {osd_inference_time + vad_inference_time:.2f}s")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Multiple speakers: {'YES' if has_multiple_speakers else 'NO'} ({overlap_percentage:.1f}% overlap)")
    if DEVICE.type == "cuda":
        print(f"  GPU processing speed: {total_time:.2f}s")
    else:
        print(f"  CPU processing speed: {total_time:.2f}s")

    # Clean up GPU memory after inference
    cleanup_gpu_memory()

    return {
        "has_multiple_speakers": has_multiple_speakers,
        "overlapped_speech_segments": overlapped_segments,
        "overlapped_speech_duration": overlapped_duration,
        "total_speech_duration": total_speech_duration,
        "overlap_percentage": overlap_percentage,
    }


def analyze_speaker_segments_direct(audio_path, model, chunk_duration=10.0):
    """Analyze audio segments directly to detect multiple speakers."""
    try:
        import torchaudio

        waveform, sample_rate = torchaudio.load(audio_path)
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)
        waveform = waveform.to(DEVICE)
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000).to(DEVICE)
            waveform = resampler(waveform)
            sample_rate = 16000

        chunk_size = int(chunk_duration * sample_rate)
        total_samples = waveform.shape[1]
        max_speakers_detected = 0
        speaker_segments = []
        multi_speaker_chunks = 0
        total_chunks = 0

        powerset_decoder = Powerset(3, 2)
        if DEVICE.type == "cuda":
            powerset_decoder = powerset_decoder.to(DEVICE)
        to_multilabel = powerset_decoder.to_multilabel

        batch_size = 4 if DEVICE.type == "cuda" else 1
        chunks = []
        chunk_start_times = []

        for start_sample in range(0, total_samples, chunk_size):
            end_sample = min(start_sample + chunk_size, total_samples)
            chunk = waveform[:, start_sample:end_sample]
            if chunk.shape[1] < chunk_size:
                padding = chunk_size - chunk.shape[1]
                chunk = torch.nn.functional.pad(chunk, (0, padding))
            chunks.append(chunk)
            chunk_start_times.append(start_sample / sample_rate)

            if len(chunks) == batch_size or start_sample + chunk_size >= total_samples:
                batch = torch.stack(chunks, dim=0)
                with torch.no_grad():
                    if DEVICE.type == "cuda":
                        with torch.amp.autocast("cuda"):
                            powerset_output = model(batch)
                            multilabel_output = to_multilabel(powerset_output)
                    else:
                        powerset_output = model(batch)
                        multilabel_output = to_multilabel(powerset_output)

                for i, chunk_start_time in enumerate(chunk_start_times):
                    speaker_activity = multilabel_output[i]
                    active_speakers_per_frame = (speaker_activity > 0.5).sum(dim=1)
                    max_speakers_in_chunk = active_speakers_per_frame.max().item()
                    max_speakers_detected = max(max_speakers_detected, max_speakers_in_chunk)
                    total_chunks += 1

                    if max_speakers_in_chunk > 1:
                        multi_speaker_chunks += 1
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

                # Critical: Clean up tensors explicitly to prevent memory leaks
                chunks.clear()
                chunk_start_times.clear()
                del batch, powerset_output, multilabel_output

                # Clean GPU memory periodically during processing
                if total_chunks % (batch_size * 4) == 0:  # Every 16 chunks for GPU, 4 for CPU
                    cleanup_gpu_memory()

        confidence_score = (multi_speaker_chunks / total_chunks) if total_chunks > 0 else 0
        has_multiple_speakers = max_speakers_detected > 1 and confidence_score > 0.1

        # Final cleanup of GPU memory
        cleanup_gpu_memory()

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
    """Generate a detailed timeline of speaker activity."""
    try:
        import torchaudio

        vad_pipeline = VoiceActivityDetection(segmentation=model)
        hyper_parameters = {
            "min_duration_on": min_duration_on,
            "min_duration_off": min_duration_off,
        }
        vad_pipeline.instantiate(hyper_parameters)
        vad_result = vad_pipeline(audio_path)

        osd_pipeline = OverlappedSpeechDetection(segmentation=model)
        osd_pipeline.instantiate(hyper_parameters)
        overlapped_result = osd_pipeline(audio_path)

        voice_segments = [(s.start, s.end) for s in vad_result.itersegments()]
        overlapped_segments = [(s.start, s.end) for s in overlapped_result.itersegments()]

        waveform, sample_rate = torchaudio.load(audio_path)
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)
        waveform = waveform.to(DEVICE)
        if sample_rate != 16000:
            resampler = torchaudio.transforms.Resample(sample_rate, 16000).to(DEVICE)
            waveform = resampler(waveform)
            sample_rate = 16000

        total_duration = waveform.shape[1] / sample_rate
        powerset_decoder = Powerset(3, 2)
        if DEVICE.type == "cuda":
            powerset_decoder = powerset_decoder.to(DEVICE)
        to_multilabel = powerset_decoder.to_multilabel

        chunk_duration = 10.0
        chunk_size = int(chunk_duration * sample_rate)
        speaker_counts_timeline = []
        batch_size = 4 if DEVICE.type == "cuda" else 1
        chunks = []
        chunk_start_times = []

        for start_sample in range(0, waveform.shape[1], chunk_size):
            end_sample = min(start_sample + chunk_size, waveform.shape[1])
            chunk = waveform[:, start_sample:end_sample]
            if chunk.shape[1] < chunk_size:
                padding = chunk_size - chunk.shape[1]
                chunk = torch.nn.functional.pad(chunk, (0, padding))
            chunks.append(chunk)
            chunk_start_times.append(start_sample / sample_rate)

            if len(chunks) == batch_size or start_sample + chunk_size >= waveform.shape[1]:
                batch = torch.stack(chunks, dim=0)
                with torch.no_grad():
                    if DEVICE.type == "cuda":
                        with torch.amp.autocast("cuda"):
                            powerset_output = model(batch)
                            multilabel_output = to_multilabel(powerset_output)
                    else:
                        powerset_output = model(batch)
                        multilabel_output = to_multilabel(powerset_output)

                for i, chunk_start_time in enumerate(chunk_start_times):
                    speaker_activity = multilabel_output[i]
                    active_speakers_per_frame = (speaker_activity > 0.5).sum(dim=1)
                    frames_per_chunk = speaker_activity.shape[0]
                    for frame_idx, num_speakers in enumerate(active_speakers_per_frame):
                        frame_start = chunk_start_time + (frame_idx * chunk_duration / frames_per_chunk)
                        frame_end = min(frame_start + (chunk_duration / frames_per_chunk), total_duration)
                        speaker_counts_timeline.append(
                            {
                                "start": frame_start,
                                "end": frame_end,
                                "speaker_count": num_speakers.item(),
                            }
                        )

                # Critical: Clean up tensors explicitly to prevent memory leaks
                chunks.clear()
                chunk_start_times.clear()
                del batch, powerset_output, multilabel_output

                # Clean GPU memory periodically during processing
                if len(speaker_counts_timeline) % 100 == 0:  # Every 100 frames processed
                    cleanup_gpu_memory()

        timeline = []
        all_events = []
        for start, end in voice_segments:
            all_events.extend([{"time": start, "type": "voice_start"}, {"time": end, "type": "voice_end"}])
        for start, end in overlapped_segments:
            all_events.extend([{"time": start, "type": "overlap_start"}, {"time": end, "type": "overlap_end"}])
        all_events.sort(key=lambda x: x["time"])

        is_voice_active = False
        is_overlapped = False
        segment_start = 0.0

        def get_speaker_count_at_time(time):
            for segment in speaker_counts_timeline:
                if segment["start"] <= time < segment["end"]:
                    return segment["speaker_count"]
            return 0

        def seconds_to_mmss(seconds):
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}:{secs:06.3f}"

        def create_segment(start, end, voice_active, overlapped):
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
                    {"type": "silence", "speakers": 0, "label": "silence", "audio_content": "no_speech"}
                )
                return base_segment

            speaker_count = max(get_speaker_count_at_time(start), get_speaker_count_at_time(end))
            if overlapped or speaker_count > 1:
                base_segment.update(
                    {
                        "type": "speech",
                        "speakers": max(speaker_count, 2),
                        "label": "conversation",
                        "audio_content": "multiple_speakers",
                    }
                )
            else:
                base_segment.update(
                    {
                        "type": "speech",
                        "speakers": max(speaker_count, 1),
                        "label": "speaking",
                        "audio_content": "single_speaker",
                    }
                )
            return base_segment

        for event in all_events:
            event_time = event["time"]
            if event_time > segment_start:
                segment = create_segment(segment_start, event_time, is_voice_active, is_overlapped)
                if segment["duration_seconds"] > 0.05:
                    timeline.append(segment)
            if event["type"] == "voice_start":
                is_voice_active = True
            elif event["type"] == "voice_end":
                is_voice_active = False
            elif event["type"] == "overlap_start":
                is_overlapped = True
            elif event["type"] == "overlap_end":
                is_overlapped = False
            segment_start = event_time

        if segment_start < total_duration:
            segment = create_segment(segment_start, total_duration, is_voice_active, is_overlapped)
            if segment["duration_seconds"] > 0.05:
                timeline.append(segment)
        if not timeline and total_duration > 0:
            timeline.append(create_segment(0.0, total_duration, False, False))

        total_speech_time = sum(s["duration_seconds"] for s in timeline if s["type"] == "speech")
        total_conversation_time = sum(s["duration_seconds"] for s in timeline if s["label"] == "conversation")
        has_multiple_speakers = any(s["speakers"] > 1 for s in timeline)

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
            "total_speaking_time": seconds_to_mmss(total_speech_time - total_conversation_time),
            "total_silence_time": seconds_to_mmss(total_duration - total_speech_time),
            "speech_percentage": round((total_speech_time / total_duration) * 100, 1) if total_duration > 0 else 0,
            "conversation_percentage": round((total_conversation_time / total_duration) * 100, 1)
            if total_duration > 0
            else 0,
            "has_multiple_speakers": has_multiple_speakers,
            "num_segments": len(timeline),
        }

        # Final cleanup of GPU memory after timeline generation
        cleanup_gpu_memory()

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


def analyze_audio_with_timeline(args, input_path, model):
    """
    Analyze audio for speaker detection and generate timeline data.

    This function handles both speaker analysis and timeline generation based
    on the provided arguments.

    Args:
        args: Command arguments containing analysis options
        input_path: Path to the audio file to analyze
        model: Pre-loaded PyAnnote model for analysis

    Returns:
        Tuple of (voice_segments, timeline_data) where:
        - voice_segments: List of (start, end) tuples for detected voice segments
        - timeline_data: Dictionary containing timeline structure with metadata
    """
    if args.analyze_speakers or args.speaker_analysis_only:
        print(f"\nAnalyzing speakers in: {input_path}")
        if args.detailed_analysis:
            speaker_analysis = analyze_speaker_segments_direct(input_path, model)
            print("Detailed Speaker Analysis Results:")
            print(f"   Multiple speakers detected: {'YES' if speaker_analysis['has_multiple_speakers'] else 'NO'}")
            print(f"   Maximum speakers detected: {speaker_analysis['max_speakers_detected']}")
            print(f"   Confidence score: {speaker_analysis['confidence_score']:.2f}")
        else:
            speaker_analysis = detect_multiple_speakers(input_path, model, args.min_duration_on, args.min_duration_off)
            print("Speaker Analysis Results:")
            print(f"   Multiple speakers detected: {'YES' if speaker_analysis['has_multiple_speakers'] else 'NO'}")
            print(f"   Overlap percentage: {speaker_analysis['overlap_percentage']:.1f}%")

    timeline_data = None
    voice_segments = None
    if args.generate_timeline:
        print("Generating speaker timeline...")
        timeline_data = generate_speaker_timeline(input_path, model, args.min_duration_on, args.min_duration_off)
        if timeline_data and "timeline" in timeline_data:
            voice_segments = [
                (mmss_to_seconds(s["start"]), mmss_to_seconds(s["end"]))
                for s in timeline_data["timeline"]
                if s["type"] == "speech"
            ]
    return voice_segments, timeline_data


# Enhanced segment factory and utilities


def generate_segment_label(segment_type, segment_context):
    """Generate appropriate label based on segment type and context."""
    if segment_type == "Gap":
        return "Gap"
    elif segment_type == "Removed":
        return ""  # Manual population required
    elif segment_type == "Audio":
        if segment_context.get("is_silence"):
            return "silence"
        elif segment_context.get("speaker_count", 1) > 1:
            return f"conversation {segment_context['speaker_count']}"
        else:
            return f"speaking {segment_context.get('speaker_count', 1)}"
    elif segment_type == "Video N/A":
        # Include audio content description based on detected audio
        if segment_context.get("is_silence"):
            return "silence (video covered)"
        elif segment_context.get("speaker_count", 1) > 1:
            return f"conversation {segment_context['speaker_count']} (video covered)"
        else:
            return f"speaking {segment_context.get('speaker_count', 1)} (video covered)"
    else:
        return ""  # Default empty for unknown types


def classify_segment_type(creation_context):
    """Classify segment type based on creation context."""
    context_type = creation_context.get("context_type", "audio")

    if context_type == "gap":
        return "Gap"
    elif context_type == "removed":
        return "Removed"
    elif context_type == "video_na":
        return "Video N/A"
    else:
        return "Audio"  # Default for audio analysis segments


def create_enhanced_segment(start_time, end_time, segment_context):
    """Create a segment with the new enhanced format.

    Args:
        start_time: Start time in seconds
        end_time: End time in seconds
        segment_context: Dictionary containing:
            - context_type: "audio", "gap", "removed", or "video_na"
            - is_silence: Boolean indicating if segment is silence
            - speaker_count: Number of speakers detected
            - annotator: Who created the segment (defaults to "Model")

    Returns:
        Dictionary with enhanced segment format
    """
    segment_type = classify_segment_type(segment_context)

    return {
        "Start": seconds_to_mmss(start_time),
        "Real start": "",  # Empty for manual population
        "End": seconds_to_mmss(end_time),
        "Real end": "",  # Empty for manual population
        "Type": segment_type,
        "Label": generate_segment_label(segment_type, segment_context),
        "Note": "",  # Empty for manual annotation
        "Confidence": "",  # Empty for manual population
        "Annotator": segment_context.get("annotator", "Model"),
    }
