declare const __APP_VERSION__: string;

type MediaLyzeDesktopBridge = {
  isDesktop: () => boolean;
  getRuntimeInfo?: () => {
    platform: string;
    arch: string;
  };
  selectLibraryPaths: () => Promise<string[]>;
  openExternalUrl?: (url: string) => Promise<boolean>;
  downloadLatestInstaller?: (version: string) => Promise<{
    ok: boolean;
    status?:
      | "success"
      | "canceled"
      | "asset_unavailable"
      | "unsupported_platform"
      | "integrity_error"
      | "network_error"
      | "save_error";
    path?: string;
    filename?: string;
    error?: string;
  }>;
  cancelInstallerDownload?: () => Promise<boolean>;
};

interface Window {
  medialyzeDesktop?: MediaLyzeDesktopBridge;
}
