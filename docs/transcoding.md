# Transcoding

MediaLyze can create a new video variant with FFmpeg from the `Transcoding` panel of a file detail page. The feature is intentionally limited to files with a regular video stream. It never edits or replaces the original.

## Profiles and plans

The initial profiles are editable starting points:

- **Original / copy:** uses the source container when it can carry the existing streams and copies every internal stream unchanged. This is the default and keeps the source codec, quality, language, HDR signaling, and stream metadata intact.
- **Save storage:** MKV/HEVC, CRF or CQ 22, preserved resolution, frame rate, and dynamic range, with non-video streams copied where compatible.
- **Modern:** MKV/AV1, CRF or CQ 30, with non-video streams copied where compatible.

The normalized versioned plan stores the container; `copy`, `drop`, or `encode` for every stream (the legacy `keep` value remains accepted for old plans); encoder and quality fields; resolution, frame rate, pixel format, profile, level, preset and GOP controls; dynamic-range handling; chapter, metadata, cover, and attachment behavior; selected sidecar subtitles; and the filename template. The normal UI exposes only the three safe stream actions. The API accepts no raw command or arbitrary FFmpeg argument field.

Filename tokens are `{resolution}`, `{dynRange}`, `{codec}`, `{audioLanguages}`, `{container}`, and `{videoBitrate}`. Empty values and punctuation are collapsed, names are made safe for the active operating system, and the extension always follows the selected container.

## Validation and capabilities

`GET /api/transcoding/capabilities` reads the local FFmpeg version, muxers, encoders, and each video encoder's locally reported option names. Intel QSV and VAAPI probes initialize the selected DRM render node, upload a 128×128 test frame, and pass the encoder's native quality option (ICQ/global quality for QSV, QP for H.264/HEVC VAAPI, and global quality for AV1/VP8/VP9/MPEG-2/MJPEG VAAPI). Hardware encoders are only marked available after a real one-frame test succeeds. Selecting an unavailable encoder is a validation error; MediaLyze does not silently fall back to CPU.

Validation resolves all files below their library root and returns the target, stream diff, warnings, normalized plan, detected capabilities, and complete readable command. It blocks existing targets, duplicate active targets, incompatible container/codec pairs, bitmap-to-text subtitle conversions, missing sidecars, invalid BCP 47 language tags, video upscaling or aspect-ratio changes, and unsupported dynamic-range choices. Dolby Vision is never synthesized: V1 only permits verified source passthrough in a supported container with video stream copy.

Video encode controls use encoder-specific constant-quality ranges (CRF, CQ, QP, or ICQ as required by the selected backend) and offer only even-pixel 360p–2160p presets that are no larger than the source. Audio encode controls use fixed, codec-appropriate bitrate presets. Stream languages are normalized to BCP 47 while retaining regional subtags and the original code in the localized label; `und` and unknown codes remain explicit.

## Execution safety

Jobs use the existing `parallel_scan_jobs` executor capacity and one slot each. They do not use per-file `scan_worker_count` workers. Execution uses an argument array with `shell=False`, explicit `-map` entries, and FFmpeg progress output.

The output is first written as a hidden temporary file in the target directory. Before publication MediaLyze verifies that the source size and modification time still match the queued snapshot. Publication refuses an existing target and uses a no-replace same-filesystem operation; unsupported filesystems fail safely. Failure, cancellation, startup recovery, or source changes remove the temporary file.

After publication, an incremental scan with trigger `transcode` analyzes the output. The scan attaches the analyzed file to a persistent variant group containing immutable source and output path snapshots. A group remains useful while analysis is pending and survives job-history pruning.

## API

- `GET /api/transcoding/capabilities`
- `GET /api/files/{file_id}/transcode`
- `POST /api/files/{file_id}/transcode/validate`
- `POST /api/files/{file_id}/transcode`
- `GET /api/transcode-jobs/active`
- `GET /api/transcode-jobs?library_id=&status=&started_after=&started_before=&limit=&offset=`
- `GET /api/transcode-jobs/{job_id}`
- `POST /api/transcode-jobs/{job_id}/cancel`

The file endpoint returns the original summary, FFprobe attachment breakdown, curated plans, jobs, and linked variants. The global history supports library, status, and time filters. Active and queued jobs are never pruned. The independent `transcode_history` retention bucket defaults to 90 days; `0` days or `0 GB` means unlimited. Pruning removes job records only, never output media or variant groups.

## Comparison

Analyzed variants link to their ordinary file detail and metadata comparison pages. `VideoWipeCompare` is shared by the Transcoding panel and the two-video metadata comparison route. It synchronizes playback, seeking, volume and mute state, exposes an accessible keyboard-operable wipe slider, warns when durations differ, and reports browser playback failures.

## Runtime paths

Docker installs FFmpeg in the runtime image and uses `FFMPEG_PATH=ffmpeg` by default. Desktop sidecars receive the packaged FFmpeg path from Electron. `MEDIALYZE_FFMPEG_DIR` selects a packaging input; `FFMPEG_PATH` is the runtime override. Release packaging verifies the binary and performs a one-frame encode on Windows, macOS, and Linux.

## Intel GPU on Linux containers

The AMD64 runtime image includes Intel's `intel-media-driver` (VAAPI) and
`onevpl-intel-gpu` (QSV) packages. ARM64 images skip these x86-only packages.
MediaLyze discovers the first `/dev/dri/renderD*` node on Linux by default; set
`MEDIALYZE_HW_RENDER_NODE` when a host exposes more than one GPU. The selected
node is used for both the capability smoke test and actual jobs, so an encoder
is shown in the UI only when the driver and device really work.

The container must expose the DRM devices and the host's `video` and `render`
groups. A minimal Compose service looks like this (use the numeric `render` GID
reported by the host):

```yaml
devices:
  - /dev/dri:/dev/dri
group_add:
  - "44"   # video (example)
  - "105"  # render (example)
environment:
  LIBVA_DRIVER_NAME: iHD
  MEDIALYZE_HW_RENDER_NODE: /dev/dri/renderD128
```

VAAPI jobs initialize the render node and upload frames with `format=nv12` (or
`p010le` for 10-bit input). QSV-only jobs initialize a named QSV device with
the selected DRM node as its `child_device`; plans that mix QSV and VAAPI derive
the QSV device from the same named VAAPI device. No host driver installation or
media-file modification is performed by MediaLyze.
