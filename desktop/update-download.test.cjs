const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { Readable, Writable } = require("node:stream");
const { pipeline } = require("node:stream/promises");

const {
  createInstallerIntegrityVerifier,
  installerFilters,
  installerTargetForRuntime,
  isAllowedInstallerDownloadUrl,
  isAllowedInstallerRedirectUrl,
  selectInstallerAsset,
  validateInstallerContentLength,
  versionedInstallerFilename,
} = require("./update-download.cjs");

test("installer targets require an exact desktop platform and architecture", () => {
  assert.equal(installerTargetForRuntime("darwin", "arm64").filename, "MediaLyze-arm64.dmg");
  assert.equal(installerTargetForRuntime("win32", "x64").filename, "MediaLyze.Setup.exe");
  assert.equal(installerTargetForRuntime("linux", "x64").filename, "MediaLyze.AppImage");
  assert.equal(installerTargetForRuntime("darwin", "x64"), null);
  assert.equal(installerTargetForRuntime("linux", "arm64"), null);
});

test("installer URLs are immutable and tied to repository, tag, and filename", () => {
  const validUrl = "https://github.com/frederikemmer/MediaLyze/releases/download/v0.18.0/MediaLyze.AppImage";
  assert.equal(isAllowedInstallerDownloadUrl(validUrl, "0.18.0", "MediaLyze.AppImage"), true);
  assert.equal(
    isAllowedInstallerDownloadUrl(
      "https://github.com/frederikemmer/MediaLyze/releases/latest/download/MediaLyze.AppImage",
      "0.18.0",
      "MediaLyze.AppImage",
    ),
    false,
  );
  assert.equal(
    isAllowedInstallerDownloadUrl(
      "https://example.test/frederikemmer/MediaLyze/releases/download/v0.18.0/MediaLyze.AppImage",
      "0.18.0",
      "MediaLyze.AppImage",
    ),
    false,
  );
});

test("redirects only allow HTTPS GitHub release asset hosts", () => {
  assert.equal(isAllowedInstallerRedirectUrl("https://release-assets.githubusercontent.com/file"), true);
  assert.equal(isAllowedInstallerRedirectUrl("https://objects.githubusercontent.com/file"), true);
  assert.equal(isAllowedInstallerRedirectUrl("http://release-assets.githubusercontent.com/file"), false);
  assert.equal(isAllowedInstallerRedirectUrl("https://example.test/file"), false);
});

test("asset selection rejects wrong architecture, size, digest, and release version", () => {
  const status = {
    latest_version: "0.18.0",
    update_available: true,
    desktop_assets: [
      {
        platform: "darwin",
        arch: "arm64",
        filename: "MediaLyze-arm64.dmg",
        download_url: "https://github.com/frederikemmer/MediaLyze/releases/download/v0.18.0/MediaLyze-arm64.dmg",
        size_bytes: 123,
        sha256: "a".repeat(64),
      },
    ],
  };

  const selected = selectInstallerAsset(status, "darwin", "arm64", "0.18.0");
  assert.equal(selected.status, "ready");
  assert.equal(selected.asset.localFilename, "MediaLyze-v0.18.0-arm64.dmg");
  assert.equal(selectInstallerAsset(status, "darwin", "x64", "0.18.0").status, "unsupported_platform");
  assert.equal(selectInstallerAsset(status, "darwin", "arm64", "0.18.1").status, "asset_unavailable");

  const invalidDigest = structuredClone(status);
  invalidDigest.desktop_assets[0].sha256 = "broken";
  assert.equal(selectInstallerAsset(invalidDigest, "darwin", "arm64", "0.18.0").status, "asset_unavailable");
});

test("save dialog metadata uses a versioned local name and matching extension", () => {
  const target = installerTargetForRuntime("win32", "x64");
  assert.equal(versionedInstallerFilename(target, "0.18.0"), "MediaLyze-v0.18.0.Setup.exe");
  assert.deepEqual(installerFilters(target), [{
    name: "MediaLyze installer",
    extensions: ["exe"],
  }]);
});

test("content length and streamed bytes must match release metadata", async () => {
  validateInstallerContentLength(undefined, 3);
  validateInstallerContentLength("3", 3);
  assert.throws(() => validateInstallerContentLength("4", 3), { status: "integrity_error" });

  const payload = Buffer.from("abc");
  const verifier = createInstallerIntegrityVerifier({ size_bytes: 3, sha256: null });
  await pipeline(Readable.from([payload]), verifier.stream, new Writable({ write(_chunk, _encoding, callback) { callback(); } }));
  assert.equal(verifier.verify().receivedBytes, 3);

  const oversized = createInstallerIntegrityVerifier({ size_bytes: 2, sha256: null });
  await assert.rejects(
    pipeline(Readable.from([payload]), oversized.stream, new Writable({ write(_chunk, _encoding, callback) { callback(); } })),
    { status: "integrity_error" },
  );
});

test("published SHA-256 digests reject damaged installer content", async () => {
  const payload = Buffer.from("verified installer");
  const expectedSha256 = crypto.createHash("sha256").update(payload).digest("hex");
  const valid = createInstallerIntegrityVerifier({ size_bytes: payload.length, sha256: expectedSha256 });
  await pipeline(Readable.from([payload]), valid.stream, new Writable({ write(_chunk, _encoding, callback) { callback(); } }));
  assert.equal(valid.verify().sha256, expectedSha256);

  const damaged = createInstallerIntegrityVerifier({ size_bytes: payload.length, sha256: "0".repeat(64) });
  await pipeline(Readable.from([payload]), damaged.stream, new Writable({ write(_chunk, _encoding, callback) { callback(); } }));
  assert.throws(() => damaged.verify(), { status: "integrity_error" });
});
