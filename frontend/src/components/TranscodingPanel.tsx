import { Check, CircleAlert, ExternalLink, Film, LoaderCircle, Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import {
  api,
  type FileTranscode,
  type MediaFileDetail,
  type TranscodeCapabilities,
  type TranscodeJob,
  type TranscodePlan,
  type TranscodeStreamAction,
  type TranscodeValidation,
} from "../lib/api";
import { formatBytes, formatCodecLabel, formatDuration } from "../lib/format";
import { VideoWipeCompare } from "./VideoWipeCompare";

const PROFILE_KEYS = ["compatibility", "storage", "modern"] as const;
const STREAM_ACTIONS: TranscodeStreamAction[] = ["keep", "copy", "encode", "drop"];

function clonePlan(plan: TranscodePlan): TranscodePlan {
  return JSON.parse(JSON.stringify(plan)) as TranscodePlan;
}

function jobIsActive(job: TranscodeJob | null): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function updateStreamPlan(
  plan: TranscodePlan,
  kind: "video_streams" | "audio_streams" | "subtitle_streams",
  streamIndex: number,
  patch: Record<string, unknown>,
): TranscodePlan {
  return {
    ...plan,
    profile: "expert",
    [kind]: plan[kind].map((stream) => stream.stream_index === streamIndex ? { ...stream, ...patch } : stream),
  };
}

function TranscodeJobHistory({ jobs }: { jobs: TranscodeJob[] }) {
  const { t } = useTranslation();
  if (!jobs.length) {
    return <p className="field-hint">{t("transcoding.history.empty")}</p>;
  }
  return (
    <div className="transcode-history-list">
      {jobs.map((job) => (
        <details key={job.id} className="file-history-entry">
          <summary className="file-history-entry-head">
            <strong>{t(`transcoding.profiles.${job.profile}`, { defaultValue: job.profile })}</strong>
            <span className={`badge transcode-status-${job.status}`}>{t(`transcoding.status.${job.status}`)}</span>
            <span>{job.output_relative_path}</span>
          </summary>
          <div className="transcode-job-detail">
            <dl>
              <div><dt>{t("transcoding.sourcePath")}</dt><dd><code>{job.source_path_snapshot}</code></dd></div>
              <div><dt>{t("transcoding.outputPath")}</dt><dd><code>{job.output_path_snapshot}</code></dd></div>
            </dl>
            <code>{job.ffmpeg_command}</code>
            {job.warnings.length ? <ul>{job.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
            {job.error ? <p className="notice error">{job.error}</p> : null}
            <details>
              <summary>{t("transcoding.history.plan")}</summary>
              <pre className="json-preview">{JSON.stringify(job.plan, null, 2)}</pre>
            </details>
            {job.result_file_id ? (
              <div className="transcode-job-links">
                <Link to={`/files/${job.result_file_id}`}>{t("transcoding.openVariant")}</Link>
                {job.source_file_id ? <Link to={`/files/compare?left=${job.source_file_id}&right=${job.result_file_id}`}>{t("transcoding.openComparison")}</Link> : null}
              </div>
            ) : null}
          </div>
        </details>
      ))}
    </div>
  );
}

export function FileTranscodeHistory({ fileId }: { fileId: string | number }) {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<TranscodeJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.fileTranscode(fileId, controller.signal)
      .then((payload) => {
        setJobs(payload.jobs);
        setError(null);
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      });
    return () => controller.abort();
  }, [fileId]);
  return (
    <section className="transcode-history-section">
      <h3>{t("transcoding.history.title")}</h3>
      {error ? <p className="notice error">{error}</p> : null}
      {jobs ? <TranscodeJobHistory jobs={jobs} /> : <p className="field-hint">{t("panel.loading")}</p>}
    </section>
  );
}

export function TranscodingPanel({ file }: { file: MediaFileDetail }) {
  const { t } = useTranslation();
  const [data, setData] = useState<FileTranscode | null>(null);
  const [capabilities, setCapabilities] = useState<TranscodeCapabilities | null>(null);
  const [plan, setPlan] = useState<TranscodePlan | null>(null);
  const [validation, setValidation] = useState<TranscodeValidation | null>(null);
  const [job, setJob] = useState<TranscodeJob | null>(null);
  const [selectedVariantId, setSelectedVariantId] = useState<number | null>(null);
  const [expertOpen, setExpertOpen] = useState(false);
  const [rawProbe, setRawProbe] = useState<Record<string, unknown> | null>(file.raw_ffprobe_json);
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [nextData, nextCapabilities] = await Promise.all([
      api.fileTranscode(file.id),
      api.transcodeCapabilities(),
    ]);
    setData(nextData);
    setCapabilities(nextCapabilities);
    setPlan((current) => current ?? clonePlan(nextData.profiles.compatibility));
    setJob(nextData.jobs.find(jobIsActive) ?? null);
    setSelectedVariantId((current) => current ?? nextData.variants.find((variant) => variant.output_file_id)?.id ?? null);
    setError(null);
  }, [file.id]);

  useEffect(() => {
    setData(null);
    setCapabilities(null);
    setPlan(null);
    setValidation(null);
    setJob(null);
    setSelectedVariantId(null);
    setExpertOpen(false);
    setError(null);
    setLoading(true);
    void refresh().catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => setRawProbe(file.raw_ffprobe_json), [file.id, file.raw_ffprobe_json]);

  useEffect(() => {
    if (!job || !jobIsActive(job)) return;
    const activeJobId = job.id;
    const intervalId = window.setInterval(() => {
      void api.transcodeJob(activeJobId).then((nextJob) => {
        setJob(nextJob);
        if (!jobIsActive(nextJob)) void refresh();
      }).catch((reason: Error) => setError(reason.message));
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [job, refresh]);

  const availableVideoEncoders = useMemo(
    () => capabilities?.encoders.filter((encoder) => (
      encoder.available && (encoder.hardware || ["h264", "hevc", "av1", "vp8", "vp9"].includes(encoder.codec))
    )) ?? [],
    [capabilities],
  );

  const selectProfile = useCallback((profile: typeof PROFILE_KEYS[number]) => {
    if (!data) return;
    setPlan(clonePlan(data.profiles[profile]));
    setValidation(null);
  }, [data]);

  const validate = useCallback(async (): Promise<TranscodeValidation | null> => {
    if (!plan) return null;
    setValidating(true);
    setError(null);
    try {
      const result = await api.validateFileTranscode(file.id, plan);
      setValidation(result);
      setPlan(result.normalized_plan);
      return result;
    } catch (reason) {
      setError((reason as Error).message);
      return null;
    } finally {
      setValidating(false);
    }
  }, [file.id, plan]);

  const start = useCallback(async () => {
    const result = await validate();
    if (!result?.valid || !plan) return;
    setStarting(true);
    try {
      const nextJob = await api.startFileTranscode(file.id, result.normalized_plan);
      setJob(nextJob);
      setValidation(result);
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setStarting(false);
    }
  }, [file.id, plan, validate]);

  if (loading) return <div className="panel-loader"><LoaderCircle className="spin" aria-hidden="true" />{t("panel.loading")}</div>;
  if (!data || !plan || !capabilities) return <p className="notice error">{error ?? t("transcoding.unavailable")}</p>;

  const selectedVariant = data.variants.find((variant) => variant.id === selectedVariantId && variant.output_file_id);
  const activeJob = jobIsActive(job) ? job : null;
  const setGlobalVideoEncoder = (encoderName: string) => {
    const capability = capabilities.encoders.find((entry) => entry.name === encoderName);
    setPlan((current) => current ? {
      ...current,
      profile: "expert",
      video_streams: current.video_streams.map((stream) => stream.action === "encode" ? {
        ...stream,
        encoder: encoderName,
        codec: capability?.codec ?? stream.codec,
        crf: capability?.hardware ? null : (stream.crf ?? stream.cq),
        cq: capability?.hardware ? (stream.cq ?? stream.crf) : null,
      } : stream),
    } : current);
    setValidation(null);
  };

  return (
    <div className="transcoding-panel">
      {error ? <p className="notice error">{error}</p> : null}
      {!capabilities.ffmpeg_available ? <p className="notice error">{capabilities.error ?? t("transcoding.ffmpegUnavailable")}</p> : null}

      <section className="transcode-original-card">
        <div><Film aria-hidden="true" /><strong>{data.original.filename}</strong></div>
        <dl>
          <div><dt>{t("fileTable.size")}</dt><dd>{formatBytes(data.original.size_bytes ?? 0)}</dd></div>
          <div><dt>{t("fileTable.duration")}</dt><dd>{formatDuration(data.original.duration_seconds ?? 0)}</dd></div>
          <div><dt>{t("fileTable.resolution")}</dt><dd>{data.original.width && data.original.height ? `${data.original.width}x${data.original.height}` : "n/a"}</dd></div>
          <div><dt>{t("fileTable.codec")}</dt><dd>{formatCodecLabel(data.original.video_codec, "video")}</dd></div>
          <div><dt>{t("fileTable.hdr")}</dt><dd>{data.original.dynamic_range ?? "SDR"}</dd></div>
        </dl>
        <details onToggle={(event) => {
          if (event.currentTarget.open && rawProbe === null) {
            void api.fileRawFfprobe(file.id).then((payload) => setRawProbe(payload.raw_ffprobe_json ?? {})).catch((reason: Error) => setError(reason.message));
          }
        }}>
          <summary>{t("fileDetail.rawJson")}</summary>
          <pre className="json-preview">{JSON.stringify(rawProbe ?? {}, null, 2)}</pre>
        </details>
      </section>

      <div className="transcode-configuration-grid">
        <label>
          <span>{t("transcoding.profile")}</span>
          <select value={PROFILE_KEYS.includes(plan.profile as typeof PROFILE_KEYS[number]) ? plan.profile : "expert"} onChange={(event) => {
            const profile = event.target.value;
            if (profile !== "expert") selectProfile(profile as typeof PROFILE_KEYS[number]);
          }}>
            {PROFILE_KEYS.map((profile) => <option key={profile} value={profile}>{t(`transcoding.profiles.${profile}`)}</option>)}
            <option value="expert">{t("transcoding.profiles.expert")}</option>
          </select>
        </label>
        <label>
          <span>{t("transcoding.container")}</span>
          <select value={plan.container} onChange={(event) => {
            setPlan({ ...plan, profile: "expert", container: event.target.value as TranscodePlan["container"] });
            setValidation(null);
          }}>
            {capabilities.containers.map((container) => <option key={container} value={container}>{container.toUpperCase()}</option>)}
          </select>
        </label>
        <label>
          <span>{t("transcoding.encoder")}</span>
          <select value={plan.video_streams.find((stream) => stream.action === "encode")?.encoder ?? ""} onChange={(event) => setGlobalVideoEncoder(event.target.value)}>
            {availableVideoEncoders.map((encoder) => (
              <option key={encoder.name} value={encoder.name}>{encoder.name} · {encoder.hardware ? t("transcoding.hardware") : t("transcoding.cpu")}</option>
            ))}
          </select>
          <small>{t("transcoding.encoderOptions")}: {capabilities.encoders.find((entry) => entry.name === plan.video_streams.find((stream) => stream.action === "encode")?.encoder)?.options.join(", ") || "—"}</small>
        </label>
        <label>
          <span>{t("transcoding.dynamicRange")}</span>
          <select value={plan.dynamic_range} onChange={(event) => {
            setPlan({ ...plan, profile: "expert", dynamic_range: event.target.value as TranscodePlan["dynamic_range"] });
            setValidation(null);
          }}>
            {([
              "preserve",
              "sdr",
              "hdr10",
              "hlg",
              ...(capabilities.dolby_vision_passthrough
                && data.original.dynamic_range?.toLowerCase().includes("dolby")
                && ["mkv", "mp4"].includes(plan.container)
                && ["hevc", "h265"].includes(data.original.video_codec?.toLowerCase() ?? "")
                && plan.video_streams.every((stream) => stream.action === "keep" || stream.action === "copy")
                ? ["dolby_vision" as const]
                : []),
            ] as const).map((value) => <option key={value} value={value}>{t(`transcoding.dynamicRanges.${value}`)}</option>)}
          </select>
        </label>
      </div>

      <section className="transcode-streams">
        {(["video_streams", "audio_streams", "subtitle_streams"] as const).map((kind) => (
          <div key={kind} className="transcode-stream-group">
            <h3>{t(`transcoding.streamGroups.${kind}`)}</h3>
            {plan[kind].map((stream) => {
              const source = kind === "video_streams"
                ? file.video_streams.find((entry) => entry.stream_index === stream.stream_index)
                : kind === "audio_streams"
                  ? file.audio_streams.find((entry) => entry.stream_index === stream.stream_index)
                  : file.subtitle_streams.find((entry) => entry.stream_index === stream.stream_index);
              const language = source && "language" in source ? source.language : null;
              const codecKind = kind === "video_streams" ? "video" : kind === "audio_streams" ? "audio" : "subtitle";
              return (
                <article key={stream.stream_index} className="transcode-stream-row">
                  <div><strong>#{stream.stream_index}</strong><span>{formatCodecLabel(source?.codec, codecKind)}</span>{language ? <span>{language}</span> : null}</div>
                  <select aria-label={t("transcoding.streamAction", { index: stream.stream_index })} value={stream.action} onChange={(event) => {
                    setPlan(updateStreamPlan(plan, kind, stream.stream_index, { action: event.target.value as TranscodeStreamAction }));
                    setValidation(null);
                  }}>
                    {STREAM_ACTIONS.map((action) => <option key={action} value={action}>{t(`transcoding.actions.${action}`)}</option>)}
                  </select>
                  {stream.action === "encode" ? (
                    <div className="transcode-stream-encode-fields">
                      <input aria-label={`${kind} ${stream.stream_index} codec`} value={stream.codec ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { codec: event.target.value || null }))} placeholder="Codec" />
                      <input aria-label={`${kind} ${stream.stream_index} encoder`} value={stream.encoder ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { encoder: event.target.value || null }))} placeholder={t("transcoding.encoder")} />
                      <input aria-label={`${kind} ${stream.stream_index} bitrate`} type="number" value={stream.bitrate ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { bitrate: event.target.value ? Number(event.target.value) : null }))} placeholder={t("transcoding.bitrate")} />
                      <input aria-label={`${kind} ${stream.stream_index} CRF`} type="number" value={stream.crf ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { crf: event.target.value ? Number(event.target.value) : null }))} placeholder="CRF" />
                      <input aria-label={`${kind} ${stream.stream_index} CQ`} type="number" value={stream.cq ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { cq: event.target.value ? Number(event.target.value) : null }))} placeholder="CQ" />
                      {kind === "video_streams" ? <>
                        <input aria-label={`video ${stream.stream_index} width`} type="number" value={stream.width ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { width: event.target.value ? Number(event.target.value) : null }))} placeholder="Width" />
                        <input aria-label={`video ${stream.stream_index} height`} type="number" value={stream.height ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { height: event.target.value ? Number(event.target.value) : null }))} placeholder="Height" />
                        <input aria-label={`video ${stream.stream_index} frame rate`} type="number" step="0.001" value={stream.frame_rate ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { frame_rate: event.target.value ? Number(event.target.value) : null }))} placeholder="Frame rate" />
                        <input aria-label={`video ${stream.stream_index} pixel format`} value={stream.pixel_format ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { pixel_format: event.target.value || null }))} placeholder="Pixel format" />
                        <input aria-label={`video ${stream.stream_index} profile`} value={stream.profile ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { profile: event.target.value || null }))} placeholder="Profile" />
                        <input aria-label={`video ${stream.stream_index} level`} value={stream.level ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { level: event.target.value || null }))} placeholder="Level" />
                        <input aria-label={`video ${stream.stream_index} preset`} value={stream.preset ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { preset: event.target.value || null }))} placeholder="Preset" />
                        <input aria-label={`video ${stream.stream_index} GOP`} type="number" value={stream.gop_size ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { gop_size: event.target.value ? Number(event.target.value) : null }))} placeholder="GOP" />
                      </> : null}
                      <input aria-label={`${kind} ${stream.stream_index} language`} value={stream.language ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { language: event.target.value || null }))} placeholder="Language" />
                      <input aria-label={`${kind} ${stream.stream_index} title`} value={stream.title ?? ""} onChange={(event) => setPlan(updateStreamPlan(plan, kind, stream.stream_index, { title: event.target.value || null }))} placeholder="Title" />
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ))}
        {file.external_subtitles.length ? (
          <div className="transcode-stream-group">
            <h3>{t("transcoding.externalSubtitles")}</h3>
            {file.external_subtitles.map((subtitle) => {
              const selected = plan.external_subtitles.find((entry) => entry.subtitle_id === subtitle.id);
              return (
                <label key={subtitle.id} className="transcode-external-subtitle">
                  <input type="checkbox" checked={Boolean(selected && selected.action !== "drop")} onChange={(event) => {
                    setPlan({
                      ...plan,
                      profile: "expert",
                      external_subtitles: event.target.checked
                        ? [...plan.external_subtitles.filter((entry) => entry.subtitle_id !== subtitle.id), { subtitle_id: subtitle.id, action: "encode", codec: plan.container === "mp4" ? "mov_text" : plan.container === "webm" ? "webvtt" : "srt", language: subtitle.language }]
                        : plan.external_subtitles.filter((entry) => entry.subtitle_id !== subtitle.id),
                    });
                  }} />
                  <span>{subtitle.path} · {subtitle.language ?? "und"} · {subtitle.format ?? "n/a"}</span>
                </label>
              );
            })}
          </div>
        ) : null}
      </section>

      <section className="transcode-stream-section">
        <h3>{t("transcoding.attachments")}</h3>
        {!data.attachments.length ? <p className="field-hint">{t("transcoding.noAttachments")}</p> : (
          <div className="transcode-attachment-list">
            {data.attachments.map((attachment) => (
              <article key={attachment.stream_index}>
                <strong>#{attachment.stream_index} · {attachment.filename ?? attachment.title ?? t("transcoding.attachment")}</strong>
                <span>{[attachment.codec, attachment.mimetype, attachment.title].filter(Boolean).join(" · ") || "—"}</span>
              </article>
            ))}
          </div>
        )}
      </section>

      <div className="transcode-global-options">
        {(["chapters", "metadata", "cover", "attachments"] as const).map((option) => (
          <label key={option}>
            <input type="checkbox" checked={plan[option] === "keep"} onChange={(event) => setPlan({ ...plan, profile: "expert", [option]: event.target.checked ? "keep" : "drop" })} />
            <span>{t(`transcoding.options.${option}`)}</span>
          </label>
        ))}
      </div>

      <label className="transcode-filename-template">
        <span>{t("transcoding.filenameTemplate")}</span>
        <input value={plan.filename_template} onChange={(event) => {
          setPlan({ ...plan, profile: "expert", filename_template: event.target.value });
          setValidation(null);
        }} />
        <small>{t("transcoding.filenameTokens")}</small>
      </label>

      <button type="button" className="secondary" onClick={() => setExpertOpen((current) => !current)}>{t("transcoding.expertMode")}</button>
      {expertOpen ? <pre className="json-preview transcode-expert-plan">{JSON.stringify(plan, null, 2)}</pre> : null}

      <div className="transcode-actions">
        <button type="button" className="secondary" onClick={() => void validate()} disabled={validating || Boolean(activeJob)}>
          {validating ? <LoaderCircle className="spin" aria-hidden="true" /> : <Check aria-hidden="true" />}{t("transcoding.validate")}
        </button>
        <button type="button" onClick={() => void start()} disabled={starting || Boolean(activeJob) || !capabilities.ffmpeg_available}>
          {starting ? <LoaderCircle className="spin" aria-hidden="true" /> : <Play aria-hidden="true" />}{t("transcoding.start")}
        </button>
      </div>

      {validation ? (
        <section className={`transcode-validation ${validation.valid ? "is-valid" : "is-invalid"}`}>
          <h3>{validation.valid ? <Check aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}{t("transcoding.validation.title")}</h3>
          <strong>{validation.output_filename}</strong>
          <code>{validation.output_path}</code>
          <div className="transcode-diff-grid">
            {(["kept_streams", "changed_streams", "removed_streams", "added_streams"] as const).map((key) => (
              <div key={key}><strong>{t(`transcoding.validation.${key}`)}</strong><span>{validation[key].join(", ") || "—"}</span></div>
            ))}
          </div>
          {validation.warnings.map((warning) => <p className="notice compact" key={warning}>{warning}</p>)}
          {validation.errors.map((validationError) => <p className="notice compact error" key={validationError}>{validationError}</p>)}
          <details><summary>{t("transcoding.command")}</summary><code className="transcode-command">{validation.ffmpeg_command}</code></details>
        </section>
      ) : null}

      {activeJob ? (
        <section className="transcode-progress" aria-live="polite">
          <div><strong>{t(`transcoding.status.${activeJob.status}`)}</strong><span>{Math.round(activeJob.progress_percent)}%</span></div>
          <progress max={100} value={activeJob.progress_percent} />
          <p>{activeJob.speed ?? "—"} · {activeJob.eta_seconds != null ? t("transcoding.eta", { seconds: Math.ceil(activeJob.eta_seconds) }) : "—"}</p>
          <button type="button" className="secondary danger" onClick={() => void api.cancelTranscodeJob(activeJob.id).then(setJob)}><Square aria-hidden="true" />{t("common.cancel")}</button>
        </section>
      ) : null}

      <section className="transcode-variants">
        <h3>{t("transcoding.variants")}</h3>
        {!data.variants.length ? <p className="field-hint">{t("transcoding.noVariants")}</p> : null}
        {data.variants.map((variant) => (
          <article key={variant.id} className={variant.id === selectedVariantId ? "is-selected" : ""}>
            <button type="button" onClick={() => setSelectedVariantId(variant.id)}>
              <strong>{variant.output_filename}</strong>
              <span>{t(`transcoding.analysisStatus.${variant.analysis_status}`, { defaultValue: variant.analysis_status })}</span>
              {variant.file ? <span>
                {formatBytes(variant.file.size_bytes ?? 0)} · {variant.file.width}x{variant.file.height} · {variant.file.dynamic_range ?? "SDR"} · {formatCodecLabel(variant.file.video_codec, "video")}
                {variant.file.audio_codecs.length ? ` · ${variant.file.audio_codecs.map((codec) => formatCodecLabel(codec, "audio")).join(", ")}` : ""}
                {variant.file.audio_languages.length ? ` · ${variant.file.audio_languages.join(", ")}` : ""}
              </span> : null}
            </button>
            {variant.output_file_id ? <Link to={`/files/${variant.output_file_id}`} aria-label={t("transcoding.openVariant")}><ExternalLink aria-hidden="true" /></Link> : null}
          </article>
        ))}
      </section>

      {selectedVariant?.output_file_id ? (
        <section className="transcode-wipe-section">
          <h3>{t("transcoding.wipeComparison")}</h3>
          <VideoWipeCompare
            first={{ src: api.fileMediaUrl(data.original.id ?? file.id), label: data.original.filename }}
            second={{ src: api.fileMediaUrl(selectedVariant.output_file_id), label: selectedVariant.output_filename }}
          />
          <Link className="secondary" to={`/files/compare?left=${data.original.id ?? file.id}&right=${selectedVariant.output_file_id}`}>{t("transcoding.openComparison")}</Link>
        </section>
      ) : null}

      <section className="transcode-history-section">
        <h3>{t("transcoding.history.title")}</h3>
        <TranscodeJobHistory jobs={data.jobs} />
      </section>
    </div>
  );
}
