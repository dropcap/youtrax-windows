# Changelog

All notable changes to YouTrax are documented here. Versions follow
MAJOR.MINOR.PATCH ([semantic versioning](https://semver.org)); the format is
based on [Keep a Changelog](https://keepachangelog.com).

`release.sh` rolls the **Unreleased** section into a numbered release at
release time — add bullets there as changes land.

## [Unreleased]

## [1.3.0] - 2026-10-03

### Added
- SoundCloud support: paste or drag a SoundCloud link exactly like a YouTube one — tag editing, BPM preview, artwork, and 320 kbps MP3 conversion all work the same
- SoundCloud downloads grab the highest-quality source available: the uploader's original file (WAV/AIFF) when downloads are enabled, otherwise the best stream
- Connect your SoundCloud account from Settings — one click imports your login from Chrome, Edge, or Firefox (or paste the token manually) to unlock 256 kbps AAC streams and original-file downloads on subscriber plans
- A notice on the download screen when a SoundCloud file came from the lower-quality guest stream because no account is connected or the token expired — silence means full quality
- SoundCloud cover art loads into the artwork panel automatically (remove or replace it as usual), and artist/title come from SoundCloud's own metadata
- New genres: Jackin House, Downtempo, and DJ Sets

## [1.2.1] - 2026-09-03

### Added
- Windows build brought to feature parity with macOS 1.2.1: BPM detection
  with tempo preview, artwork normalization, refreshed tag-editor UI,
  yt-dlp self-updates, bundled Deno for the YouTube 403 fix, version and
  release notes in Settings, in-app updates from GitHub Releases, and the
  `ytmp3://` URL scheme

### Changed
- In-app updates are now live: this and later versions install updates from inside the app

## [1.2.0] - 2026-09-03

### Added
- Version number shown in Settings, with release notes ("What's New") viewable in the app
- In-app updates: YouTrax now checks for new versions and installs them itself — no more re-downloading the installer or repeating the right-click-to-open step

## [1.1.0] - 2026-08-28

### Added
- Automatic BPM detection written to the TBPM tag, with a tempo preview in the tag editor

### Fixed
- YouTube HTTP 403 download failures, by bundling the Deno JavaScript runtime
- Bundled Deno blocked by Gatekeeper on a clean Mac
- yt-dlp now keeps itself current between app releases, so YouTube changes no longer break downloads for long

### Changed
- Embedded artwork normalized to 800x800 JPEG

## [1.0.0] - 2026-04-11

Initial release.

### Added
- YouTube to 320 kbps MP3 conversion with real-time progress
- Full tag editor before download: title, artist, album, genre, year, comments
- iTunes artwork search, drag-and-drop artwork, smart title parsing
- Multi-tab UI with drag-and-drop YouTube URLs
- `ytmp3://` URL scheme for one-click sends from the browser
- Dark/light mode, Platinum Notes auto-open, configurable download folder
- Downloads renamed to "Title - Artist.mp3" automatically
- Windows EXE build support
