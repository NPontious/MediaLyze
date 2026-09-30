import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { FormattingKind } from "../lib/transcode-formatting-presets";

type TranscodeFormattingMetadataMenuProps = {
  kind: FormattingKind;
  id: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function TranscodeFormattingMetadataMenu({ kind, id, open, onToggle, children }: TranscodeFormattingMetadataMenuProps) {
  const { t } = useTranslation();
  const filename = kind === "filename";

  return (
    <div className="transcode-filename-metadata-tools">
      <button
        type="button"
        className="secondary small settings-panel-header-action transcode-filename-metadata-toggle"
        aria-expanded={open}
        aria-controls={id}
        onMouseDown={(event) => event.preventDefault()}
        onClick={onToggle}
      >
        {open ? <ChevronDown aria-hidden="true" size={14} /> : <ChevronRight aria-hidden="true" size={14} />}
        {t(filename ? "transcoding.filenameAddMetadata" : "transcoding.folderAddMetadata")}
      </button>
      {open ? (
        <div
          className="transcode-filename-token-list"
          id={id}
          role="group"
          aria-label={t(filename ? "transcoding.filenameMetadataTokens" : "transcoding.folderMetadataTokens")}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
