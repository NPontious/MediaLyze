const path = require("node:path");
const crypto = require("node:crypto");
const { Transform } = require("node:stream");


const STABLE_VERSION_PATTERN = /^\d+\.\d+\.\d+$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const GITHUB_DOWNLOAD_HOST = "github.com";
const ALLOWED_REDIRECT_HOSTS = new Set([
  "github.com",
  "objects.githubusercontent.com",
  "release-assets.githubusercontent.com",
]);

const INSTALLER_TARGETS = [
  { platform: "darwin", arch: "arm64", filename: "MediaLyze-arm64.dmg", extension: "-arm64.dmg" },
  { platform: "win32", arch: "x64", filename: "MediaLyze.Setup.exe", extension: ".Setup.exe" },
  { platform: "linux", arch: "x64", filename: "MediaLyze.AppImage", extension: ".AppImage" },
];

function installerTargetForRuntime(platform, arch) {
  return INSTALLER_TARGETS.find((target) => target.platform === platform && target.arch === arch) ?? null;
}

function versionedInstallerFilename(target, version) {
  if (!target || !STABLE_VERSION_PATTERN.test(version)) {
    return null;
  }
  return `MediaLyze-v${version}${target.extension}`;
}

function parseSafeHttpsUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
    return null;
  }
  return parsed;
}

function isAllowedInstallerDownloadUrl(url, version, filename) {
  const parsed = parseSafeHttpsUrl(url);
  if (!parsed || parsed.host !== GITHUB_DOWNLOAD_HOST || parsed.search || parsed.hash) {
    return false;
  }
  return parsed.pathname === `/NPontious/MediaLyze/releases/download/v${version}/${filename}`;
}

function isAllowedInstallerRedirectUrl(url) {
  const parsed = parseSafeHttpsUrl(url);
  return Boolean(parsed && ALLOWED_REDIRECT_HOSTS.has(parsed.host));
}

function selectInstallerAsset(updateStatus, platform, arch, version) {
  const target = installerTargetForRuntime(platform, arch);
  if (!target || !STABLE_VERSION_PATTERN.test(version)) {
    return { status: "unsupported_platform", target, asset: null };
  }
  if (
    !updateStatus
    || updateStatus.latest_version !== version
    || updateStatus.update_available !== true
    || !Array.isArray(updateStatus.desktop_assets)
  ) {
    return { status: "asset_unavailable", target, asset: null };
  }
  const asset = updateStatus.desktop_assets.find(
    (candidate) =>
      candidate
      && candidate.platform === platform
      && candidate.arch === arch
      && candidate.filename === target.filename
  );
  if (
    !asset
    || !Number.isSafeInteger(asset.size_bytes)
    || asset.size_bytes <= 0
    || !isAllowedInstallerDownloadUrl(asset.download_url, version, target.filename)
    || (asset.sha256 !== null && asset.sha256 !== undefined
      && (typeof asset.sha256 !== "string" || !SHA256_PATTERN.test(asset.sha256)))
  ) {
    return { status: "asset_unavailable", target, asset: null };
  }
  return {
    status: "ready",
    target,
    asset: {
      ...asset,
      sha256: asset.sha256 ?? null,
      localFilename: versionedInstallerFilename(target, version),
    },
  };
}

function installerFilters(target) {
  if (!target) {
    return [];
  }
  return [{
    name: "MediaLyze installer",
    extensions: [path.extname(target.filename).replace(/^\./, "")],
  }];
}

function integrityError(message) {
  const error = new Error(message);
  error.status = "integrity_error";
  return error;
}

function validateInstallerContentLength(contentLength, expectedSize) {
  if (contentLength === undefined) {
    return;
  }
  if (!/^\d+$/.test(String(contentLength)) || Number(contentLength) !== expectedSize) {
    throw integrityError("Installer content length does not match release metadata");
  }
}

function createInstallerIntegrityVerifier(asset) {
  const digest = crypto.createHash("sha256");
  let receivedBytes = 0;
  const stream = new Transform({
    transform(chunk, _encoding, callback) {
      receivedBytes += chunk.length;
      if (receivedBytes > asset.size_bytes) {
        callback(integrityError("Installer is larger than release metadata"));
        return;
      }
      digest.update(chunk);
      callback(null, chunk);
    },
  });
  return {
    stream,
    verify() {
      if (receivedBytes !== asset.size_bytes) {
        throw integrityError("Installer size does not match release metadata");
      }
      const actualSha256 = digest.digest("hex");
      if (asset.sha256 && actualSha256 !== asset.sha256) {
        throw integrityError("Installer checksum does not match release metadata");
      }
      return { receivedBytes, sha256: actualSha256 };
    },
  };
}

module.exports = {
  ALLOWED_REDIRECT_HOSTS,
  INSTALLER_TARGETS,
  createInstallerIntegrityVerifier,
  installerFilters,
  installerTargetForRuntime,
  isAllowedInstallerDownloadUrl,
  isAllowedInstallerRedirectUrl,
  selectInstallerAsset,
  validateInstallerContentLength,
  versionedInstallerFilename,
};
