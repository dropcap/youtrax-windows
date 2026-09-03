# YouTrax — Windows

A Windows desktop app that downloads YouTube audio as 320 kbps MP3 files with full metadata tagging.

Built with Python, Flask, pywebview (Edge WebView2), and yt-dlp. Feature parity with the [YouTrax macOS app](https://github.com/dropcap/youtrax).

## Features

- Paste or drag-and-drop a YouTube URL to fetch track info
- Edit tags before download: Title, Artist, Album, Genre, Year, Comments
- Automatic BPM detection written to the TBPM tag, with a tempo preview in the tag editor
- Search and select album artwork via iTunes API; drag artwork from browser or Explorer
- Embedded artwork normalized to 800x800 JPEG
- Search Google Images or Beatport directly from the tag editor
- Multi-tab — queue multiple downloads simultaneously
- Downloads renamed to "Title - Artist.mp3" automatically
- Dark / light mode toggle, Platinum Notes auto-open, configurable download folder
- `ytmp3://` URL scheme for one-click sends from the browser
- Bundled Deno JavaScript runtime, so YouTube's signed URLs work out of the box
- yt-dlp keeps itself current between app releases, so YouTube changes don't break downloads for long
- In-app updates: YouTrax checks GitHub Releases for new versions and installs them itself
- Version number and release notes ("What's New") viewable in Settings

## Requirements

- Windows 10 or 11 (Edge WebView2 is built-in on both)
- Python 3.10+
- pip

## Build

```powershell
# Install dependencies, vendor Deno, and build the single-file .exe
.\build_win.ps1
```

The output is a single file at `dist\YouTrax.exe`. CI does the same on every push, and attaches the exe to a GitHub Release when a `v*` tag is pushed.

## Development

```powershell
pip install -r requirements.txt
python main.py
```

## Notes

- Settings are saved to `%USERPROFILE%\.config\ytdl\settings.json`
- Downloads default to `%USERPROFILE%\Downloads`
- The `ytmp3://` URL scheme is registered for the current user on first launch of the packaged exe
- Downloaded yt-dlp updates and staged app updates live under `%LOCALAPPDATA%\YouTrax`
- Set `YOUTRAX_NO_APP_UPDATE=1` to disable in-app update checks, `YTDL_NO_SELF_UPDATE=1` to disable yt-dlp self-updates
