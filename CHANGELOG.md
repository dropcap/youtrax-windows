# Changelog

All notable changes to YouTrax are documented here. Versions follow
MAJOR.MINOR.PATCH ([semantic versioning](https://semver.org)); the format is
based on [Keep a Changelog](https://keepachangelog.com).

`release.sh` rolls the **Unreleased** section into a numbered release at
release time — add bullets there as changes land.

## [Unreleased]

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
