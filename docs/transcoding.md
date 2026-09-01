# Transcoding

MediaLyze can create a new video variant with FFmpeg from the `Transcoding` panel of a file detail page. The feature is intentionally limited to files with a regular video stream. It never edits or replaces the original.

## Profiles and plans

The initial profiles are editable starting points:

- **Compatibility:** MP4/H.264, up to 1080p, CRF or CQ 20, preserved frame rate, AAC at 192 kbit/s for stereo or 384 kbit/s for multichannel audio.
- **Save storage:** MKV/HEVC, CRF or CQ 22, preserved resolution, frame rate, and dynamic range, with non-video streams copied where compatible.
- **Modern:** MKV/AV1, CRF or CQ 30, with non-video streams copied where compatible.

The normalized versioned plan stores the container; `keep`, `drop`, `copy`, or `encode` for every stream; encoder and quality fields; resolution, frame rate, pixel format, profile, level, preset and GOP controls; dynamic-range handling; chapter, metadata, cover, and attachment behavior; selected sidecar subtitles; and the filename template. The API accepts no raw command or arbitrary FFmpeg argument field.

Filename tokens are `{resolution}`, `{dynRange}`, `{codec}`, `{audioLanguages}`, `{container}`, and `{videoBitrate}`. Empty values and punctuation are collapsed, names are made safe for the active operating system, and the extension always follows the selected container.

## Validation and capabilities

`GET /api/transcoding/capabilities` reads the local FFmpeg version, muxers, encoders, and each video encoder's locally reported option names. Hardware encoders are only marked available after a real one-frame test succeeds. Selecting an unavailable encoder is a validation error; MediaLyze does not silently fall back to CPU.

Validation resolves all files below their library root and returns the target, stream diff, warnings, normalized plan, detected capabilities, and complete readable command. It blocks existing targets, duplicate active targets, incompatible container/codec pairs, bitmap-to-text subtitle conversions, missing sidecars, and unsupported dynamic-range choices. Dolby Vision is never synthesized: V1 only permits verified source passthrough in a supported container with video stream copy.

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
