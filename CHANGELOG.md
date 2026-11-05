# Changelog

All notable changes to YouTrax are documented here. Versions follow
MAJOR.MINOR.PATCH ([semantic versioning](https://semver.org)); the format is
based on [Keep a Changelog](https://keepachangelog.com).

`release.sh` rolls the **Unreleased** section into a numbered release at
release time — add bullets there as changes land.

## [Unreleased]

### Added
- A "Show in File Explorer when done" switch in Settings › Download Settings. Turn it off to stop a File Explorer window opening every time a download finishes. It stays on unless you change it

### Fixed
- Dragging a link from the browser's address bar onto YouTrax starts the download, even when the drag also carries a link file

## [2.0.1] - 2026-11-02

### Changed
- Settings now has two tabs, Download Settings and Normalize Settings, with the version, Check for Updates and the theme always shown above them
- Normalize mode's Output choice (Replace originals or Save copies) and its backup folder moved into Settings › Normalize Settings. The Normalize screen shows them as a summary line with a Change link, next to a cleaner loudness summary, so the track list has more room
- Clear list moved into the track list's header

### Fixed
- After an update, the app window could keep showing the previous version's screen until it was restarted twice

## [2.0.0] - 2026-11-02

### Added
- Loudness normalization: every download is set to the same loudness, −11.5 LUFS by default, so tracks play back at a matching level without riding the gain. Loud tracks are turned down; quiet tracks are turned up, with a transparent limiter catching any peaks. The gain is applied during the one MP3 encode, so there is no extra quality loss
- A Loudness section in Settings to switch normalization off or change it: target loudness (−8, −10, −11.5 recommended or −14 LUFS, or any custom value from −18 to −6), true-peak ceiling, maximum limiting on quiet tracks, and a pure-gain-only mode. Changes apply to the next download or Normalize run
- Normalize Files: YouTrax now has two modes, Download and Normalize, switched at the top of the window. Drag tracks or whole folders from File Explorer onto YouTrax (or switch to Normalize and use Add Tracks/Add Folder) to give tracks you already have the same loudness treatment as downloads. MP3, AAC/ALAC (M4A), FLAC, WAV and AIFF keep their format, quality and every tag, including artwork and DJ software cue data. By default (and as recommended) the originals are replaced so cue points stay linked, with each original backed up first (to Music\YouTrax Originals, or a folder you choose); alternatively, save normalized copies in a "Normalized" folder next to each track or in a folder you choose. Tracks already normalized to the current target are skipped, and every track is checked sample by sample before it is written: if the new file would not line up exactly with the original, so cue points would move, the track is left unchanged and flagged
- Finished downloads show their loudness before and after (for example "−7.0 → −11.5 LUFS"), and normalized files carry a `DJNORM` tag recording the target they were set to

### Changed
- A refreshed look: a darker, violet-tinted theme with the Geist typeface, a new app icon in the header, and clearer status lights on tracks and tabs. Finished downloads now show their artwork with a check mark. Light mode is still available
- Settings are reorganised into groups (Appearance, Downloads, Loudness, SoundCloud), with the version and Check for Updates at the top, a Dark/Light theme menu, and a clearer SoundCloud token field with Show/Hide
- The buttons next to the source title now say what they do on hover (copy title, find artwork on Google Images, look up the track on Beatport), as do the Original Mix, Extended Mix, ÷2 and ×2 buttons
- More genres to pick from: Drum & Bass, Dubstep, Electro, Electronica, Hard Techno, Indie Tech, Organic House and DJ Tools

### Removed
- The Platinum Notes option. YouTrax now sets loudness itself, so downloads no longer need a second pass in Platinum Notes

## [1.3.1] - 2026-10-22

### Fixed
- On smaller screens the Download MP3 button could be cut off below the window with no way to reach it. The window now opens sized to fit your screen (taskbar and display scaling included), content scrolls when it doesn't fit, and Download MP3 stays pinned to the bottom of the window

### Changed
- On shorter screens the tag editor switches to a more compact layout, with Year, BPM and Genre on one row, so it fits laptop displays without scrolling

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
