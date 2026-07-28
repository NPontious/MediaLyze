const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("medialyzeDesktop", {
  isDesktop: () => true,
  getRuntimeInfo: () => ({ platform: process.platform, arch: process.arch }),
  selectLibraryPaths: () => ipcRenderer.invoke("medialyze:select-library-paths"),
  openExternalUrl: (url) => ipcRenderer.invoke("medialyze:open-external-url", url),
  downloadLatestInstaller: (version) => ipcRenderer.invoke("medialyze:download-latest-installer", version),
  cancelInstallerDownload: () => ipcRenderer.invoke("medialyze:cancel-installer-download"),
});
