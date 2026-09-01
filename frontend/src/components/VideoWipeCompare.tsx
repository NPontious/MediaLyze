import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type VideoWipeSource = {
  src: string;
  label: string;
};

export function VideoWipeCompare({
  first,
  second,
}: {
  first: VideoWipeSource;
  second: VideoWipeSource;
}) {
  const { t } = useTranslation();
  const firstRef = useRef<HTMLVideoElement>(null);
  const secondRef = useRef<HTMLVideoElement>(null);
  const [wipe, setWipe] = useState(50);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [secondDuration, setSecondDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [unsupported, setUnsupported] = useState(false);

  const synchronize = useCallback(() => {
    const firstVideo = firstRef.current;
    const secondVideo = secondRef.current;
    if (!firstVideo || !secondVideo) return;
    if (Math.abs(secondVideo.currentTime - firstVideo.currentTime) > 0.12) {
      secondVideo.currentTime = firstVideo.currentTime;
    }
  }, []);

  const togglePlayback = useCallback(async () => {
    const firstVideo = firstRef.current;
    const secondVideo = secondRef.current;
    if (!firstVideo || !secondVideo) return;
    if (!firstVideo.paused) {
      firstVideo.pause();
      secondVideo.pause();
      setPlaying(false);
      return;
    }
    synchronize();
    try {
      await Promise.all([firstVideo.play(), secondVideo.play()]);
      setPlaying(true);
    } catch {
      firstVideo.pause();
      secondVideo.pause();
      setPlaying(false);
      setUnsupported(true);
    }
  }, [synchronize]);

  const seek = useCallback((nextTime: number) => {
    setCurrentTime(nextTime);
    if (firstRef.current) firstRef.current.currentTime = nextTime;
    if (secondRef.current) secondRef.current.currentTime = nextTime;
  }, []);

  useEffect(() => {
    for (const video of [firstRef.current, secondRef.current]) {
      if (!video) continue;
      video.volume = volume;
      video.muted = muted;
    }
  }, [muted, volume]);

  const durationsDiffer = duration > 0 && secondDuration > 0 && Math.abs(duration - secondDuration) > 0.5;

  return (
    <div className="video-wipe-compare">
      <div className="video-wipe-stage">
        <video
          ref={firstRef}
          src={first.src}
          preload="metadata"
          aria-label={first.label}
          onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
          onTimeUpdate={(event) => {
            setCurrentTime(event.currentTarget.currentTime);
            synchronize();
          }}
          onEnded={() => {
            secondRef.current?.pause();
            setPlaying(false);
          }}
          onError={() => setUnsupported(true)}
        />
        <div className="video-wipe-second" style={{ clipPath: `inset(0 ${100 - wipe}% 0 0)` }}>
          <video
            ref={secondRef}
            src={second.src}
            preload="metadata"
            aria-label={second.label}
            onLoadedMetadata={(event) => setSecondDuration(event.currentTarget.duration || 0)}
            onError={() => setUnsupported(true)}
          />
        </div>
        <div className="video-wipe-divider" style={{ left: `${wipe}%` }} aria-hidden="true" />
        <span className="video-wipe-label video-wipe-label-first">{first.label}</span>
        <span className="video-wipe-label video-wipe-label-second">{second.label}</span>
      </div>
      <div className="video-wipe-controls">
        <button type="button" className="secondary icon-only-button" onClick={() => void togglePlayback()} aria-label={playing ? t("videoWipe.pause") : t("videoWipe.play")}>
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        </button>
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.01}
          value={Math.min(currentTime, duration || 0)}
          onChange={(event) => seek(Number(event.target.value))}
          aria-label={t("videoWipe.seek")}
          disabled={!duration}
        />
        <button type="button" className="secondary icon-only-button" onClick={() => setMuted((current) => !current)} aria-label={muted ? t("videoWipe.unmute") : t("videoWipe.mute")}>
          {muted ? <VolumeX aria-hidden="true" /> : <Volume2 aria-hidden="true" />}
        </button>
        <input
          className="video-wipe-volume"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={volume}
          onChange={(event) => setVolume(Number(event.target.value))}
          aria-label={t("videoWipe.volume")}
        />
      </div>
      <label className="video-wipe-slider-label">
        <span>{t("videoWipe.wipe")}</span>
        <input
          type="range"
          min={0}
          max={100}
          value={wipe}
          onChange={(event) => setWipe(Number(event.target.value))}
          aria-label={t("videoWipe.wipe")}
        />
      </label>
      {durationsDiffer ? <p className="notice compact">{t("videoWipe.durationWarning")}</p> : null}
      {unsupported ? <p className="notice compact error">{t("videoWipe.unsupported")}</p> : null}
    </div>
  );
}
