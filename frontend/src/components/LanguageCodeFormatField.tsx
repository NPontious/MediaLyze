import { useTranslation } from "react-i18next";
import { FILENAME_LANGUAGE_CODE_FORMATS, streamLanguageCodeFormats, type FilenameLanguageCodeFormat, type StreamLanguageCodeFormat } from "../lib/language";
import { TooltipTrigger } from "./TooltipTrigger";

type SharedProps = {
  kind?: "filename" | "folder" | "video" | "audio" | "subtitle";
  className?: string;
  controlClassName?: string;
  ariaLabel?: string;
};

type Props = SharedProps & (
  | { container?: undefined; value: FilenameLanguageCodeFormat; onChange: (value: FilenameLanguageCodeFormat) => void }
  | { container: "source" | "mkv" | "mp4" | "webm"; value: StreamLanguageCodeFormat | "mixed"; onChange: (value: StreamLanguageCodeFormat) => void }
);

const examples: Record<FilenameLanguageCodeFormat | StreamLanguageCodeFormat, string> = {
  iso_639_1: "de",
  iso_639_2: "ger",
  iso_639_2_t: "deu",
  iso_639_3: "deu / cmn",
  bcp_47: "de-DE / zh-Hant",
  container_default: "ger (MKV) / deu (MP4)",
  iso_639_2_region: "ger-DE",
};

export function LanguageCodeFormatField({
  value, onChange, container, className = "transcode-control-field",
  controlClassName = "settings-choice-input transcode-control", ariaLabel,
}: Props) {
  const { t } = useTranslation();
  const formats = container ? streamLanguageCodeFormats(container) : FILENAME_LANGUAGE_CODE_FORMATS;
  const tooltip = (
    <div className="transcode-matrix-tooltip-content transcode-language-format-tooltip">
      <div className="transcode-matrix-tooltip-heading">
        <strong>{t("transcoding.languageCodeFormat")}</strong>
      </div>
      <div className="transcode-matrix-tooltip-workload">
        {t(container ? "transcoding.streamLanguageFormatIntro" : "transcoding.filenameLanguageFormatIntro")}
      </div>
      {container ? <div className="transcode-matrix-tooltip-workload">{t("transcoding.streamLanguageFormatSharedHint")}</div> : null}
      {formats.map((format) => <div className="transcode-matrix-tooltip-row" key={format}>
        <div className="transcode-matrix-tooltip-level-head">
          <strong>{t(`transcoding.languageCodeFormats.${format}`)}</strong>
          <code>{examples[format]}</code>
        </div>
        <div className="transcode-matrix-tooltip-summary">
          <div><span>Jellyfin</span><strong>{t(`transcoding.languageFormatSupport.${format}.jellyfin`)}</strong></div>
          <div><span>Plex</span><strong>{t(`transcoding.languageFormatSupport.${format}.plex`)}</strong></div>
        </div>
      </div>)}
      <div className="transcode-matrix-tooltip-row transcode-matrix-tooltip-workload">
        {t("transcoding.languageFormatSupportNote")}
      </div>
    </div>
  );
  return <label className={className}>
    <span className="transcode-field-label">
      <span>{t("transcoding.languageCodeFormat")}</span>
      <TooltipTrigger ariaLabel={t("transcoding.languageCodeFormatHelpAria")} content={tooltip} tooltipClassName="transcode-matrix-tooltip-portal transcode-language-format-tooltip-portal" placement="auto" pinOnClick={false} maxWidth={480} />
    </span>
    <select className={controlClassName} aria-label={ariaLabel ?? t("transcoding.languageCodeFormat")} value={value} onChange={(event) => onChange(event.target.value as never)}>
      {value === "mixed" ? <option value="mixed" disabled>{t("transcoding.mixedStreamLanguageCodeFormats")}</option> : null}
      {formats.map((format) => <option key={format} value={format}>{t(`transcoding.languageCodeFormats.${format}`)}</option>)}
    </select>
  </label>;
}
