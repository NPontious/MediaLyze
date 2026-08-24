export type DesktopBridge = {
  isDesktop: () => boolean;
  getRuntimeInfo?: () => DesktopRuntimeInfo;
  selectLibraryPaths: () => Promise<string[]>;
  openExternalUrl?: (url: string) => Promise<boolean>;
  downloadLatestInstaller?: (version: string) => Promise<DesktopInstallerDownloadResult>;
  cancelInstallerDownload?: () => Promise<boolean>;
};

export type DesktopRuntimeInfo = {
  platform: string;
  arch: string;
};

export type DesktopInstallerDownloadResult = {
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
};

export function getDesktopBridge(): DesktopBridge | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.medialyzeDesktop ?? null;
}


export function isDesktopApp(): boolean {
  return getDesktopBridge()?.isDesktop() ?? false;
}
