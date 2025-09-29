# Why apply-blank Can't Follow the Same Approach as process --complete

## Executive Summary

The fundamental difference between `process --complete` and `apply-blank` lies in the nature of the operations they perform:

- **process --complete**: Audio-only modifications (muting) with video stream-copy
- **apply-blank**: Video modifications (blackening) requiring re-encoding or replacement

This fundamental constraint means `apply-blank` cannot achieve the same O(T) linear performance as `process --complete`.

## How process --complete Works (Fast: O(T))

### The Secret: Stream Copy for Video

```bash
ffmpeg -i input.mp4 \
  -c:v copy \  # ← KEY: Video is never touched, just copied
  -af "volume=enable='between(t,67.852,68.763)':volume=0,volume=enable='between(t,1013.066,1017.369)':volume=0" \
  output.mp4
```

### Performance Characteristics

- **Complexity**: O(T) - Linear scan of entire file
- **Video**: Stream copy (`-c:v copy`) - no decoding/encoding
- **Audio**: Filtered in-place during the linear scan
- **I/O Pattern**: One read pass, one write pass
- **CPU**: Minimal - only audio filtering, no video processing
- **Memory**: Minimal buffering

### Why It's Fast

1. **No video re-encoding**: Video data is copied byte-for-byte without modification
2. **Single-pass**: One linear read through the file
3. **Minimal CPU**: Only audio filters are applied
4. **No seeking**: Sequential I/O is fastest for large files
5. **No temporary files**: Direct input→filter→output pipeline

## How apply-blank Works (Slow: O(M×T))

### The Problem: Video Must Be Modified

When you need to black out video segments, you have three options:

1. **Re-encode entire video** with black overlays (Mode 1)
2. **Extract → Process → Concatenate** (Mode 2)
3. **Extract → Replace → Concatenate** (Legacy)

None of these can use `-c:v copy` because the video content must change.

### Current Legacy Implementation (Worst: O(M×T))

```bash
# For each of M segments, multiple FFmpeg calls:
ffmpeg -ss START -i input.mp4 -t DURATION -c copy segment_before.mp4
ffmpeg -ss START -i input.mp4 -t DURATION [apply effects] segment_affected.mp4
ffmpeg -ss START -i input.mp4 -t DURATION -c copy segment_after.mp4
# Then concatenate hundreds/thousands of files
```

**Performance Issues:**
- **M × Decode seeks**: Each `-ss START -i input.mp4` must seek and decode
- **Seek cost**: ~T/2 on average per segment = M×T/2 total decode work
- **Process overhead**: Spawning M FFmpeg processes
- **Disk thrashing**: M separate I/O operations instead of sequential
- **Temporary files**: Thousands of intermediate segments

### Example: Why 9 Hours vs 10 Minutes

**Typical scenario:**
- Video: 1.5 hours (5,400 seconds), 12GB file
- Merged segments: 300 (after optimization)
- SSD decode speed: ~50× realtime

**process --complete math:**
- Time = (12GB read + 12GB write) / 300MB/s + audio filtering
- Time ≈ 80 seconds I/O + ~5 minutes processing = **~10 minutes**

**apply-blank legacy math:**
- Seek work = 300 segments × 2,700s average seek × (1/50× speed) = 16,200 seconds
- Process overhead = 300 × 0.15s = 45 seconds
- Time ≈ 16,200s + 45s = **~4.5 hours**
- *Plus additional overhead for file management, concatenation, etc.*

## Why apply-blank Cannot Use the Same Approach

### Fundamental Technical Constraints

1. **Stream Copy Incompatibility**
   ```bash
   # This is IMPOSSIBLE - you can't stream copy modified video
   ffmpeg -i input.mp4 \
     -c:v copy \          # ← Can't copy video that needs to be blacked out
     -vf "drawbox=..."    # ← This requires re-encoding
   ```

2. **Video Modification Requirements**
   - Black segments require visual changes to video data
   - Cannot stream-copy frames that need to be modified
   - Must either re-encode or replace affected segments

3. **Filter Timing Precision**
   ```bash
   # This works for audio (in-place filtering):
   -af "volume=enable='between(t,67.852,68.763)':volume=0"

   # This requires re-encoding for video:
   -vf "drawbox=enable='between(t,67.852,68.763)':w=3840:h=2160:color=black"
   ```

### Why Single-Pass Re-encoding Is Still "Slow"

Even our optimized Mode 1 approach:
```bash
ffmpeg -i input.mp4 \
  -filter_complex_script black_ranges.txt \
  -c:v h264_nvenc \  # ← Must re-encode entire video
  output.mp4
```

**Performance comparison:**
- **process --complete**: Copy 12GB video + filter audio = ~10 minutes
- **Mode 1**: Re-encode 12GB video + filter audio = ~60-90 minutes (6-9× slower)

The fundamental bottleneck is video re-encoding vs video stream-copy.

## Our Optimization Strategies

### Mode 1: Single-Pass GPU Filter (Current Default)
- **Approach**: Re-encode entire video with conditional black overlays
- **Complexity**: O(T) - but with video re-encoding overhead
- **Advantage**: Simple, reliable, uses GPU acceleration
- **Limitation**: Must process entire video even for small changes

### Mode 2: Incremental Stream-Copy Reuse
- **Approach**: Extract unchanged segments (stream copy), process changed segments, concatenate
- **Complexity**: O(M_changed × T_segment + concat)
- **Advantage**: Only processes what actually changed
- **Limitation**: Complex concatenation logic, stream format matching

### Smart Mode Selection
- **<20% changed, no black-only edits**: Use Mode 2 (incremental)
- **>20% changed or complex edits**: Use Mode 1 (single-pass)

## Theoretical Performance Limits

### Best Case Scenario (Mode 2, minimal changes)
- 10 changed segments out of 12,000 total
- Extract unchanged: Stream copy ≈ 5-10 minutes
- Process changed: 10 × (small re-encode) ≈ 2-5 minutes
- Concatenate: ≈ 3-5 minutes
- **Total: ~15-20 minutes** (2× process --complete)

### Worst Case Scenario (Many changes)
- Mode 1: Full video re-encode ≈ 60-90 minutes
- **Total: ~1.5 hours** (9× process --complete)

## Conclusion

**apply-blank fundamentally cannot match process --complete performance** because:

1. **Video modification requires re-encoding** (can't use `-c:v copy`)
2. **Re-encoding is inherently slower** than stream copying
3. **Visual changes require pixel-level processing**, not just metadata filtering

The optimization focuses on **minimizing the re-encoding work** rather than eliminating it entirely:

- **Mode 1**: Minimize by doing it in one pass with GPU acceleration
- **Mode 2**: Minimize by only re-encoding changed segments

The 10× performance gap between audio-only and video modification operations is a fundamental limitation of video processing, not a code optimization issue.