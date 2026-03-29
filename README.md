# YouTrax — Windows

A Windows desktop app that downloads YouTube audio as 320 kbps MP3 files with full metadata tagging.

Built with Python, Flask, pywebview (Edge WebView2), and yt-dlp.

## Features

- Paste or drag-and-drop a YouTube URL to fetch track info
- Edit tags: Title, Artist, Album, Year, Genre
- Search and select album artwork via iTunes API
- Drag artwork from browser or file explorer onto the artwork panel
- Search Google Images or Beatport directly from the tag editor
- Multi-tab — queue multiple downloads simultaneously
- Dark / light mode toggle
- 320 kbps MP3 output with full ID3 tags

## Requirements

- Windows 10 or 11 (Edge WebView2 is built-in on both)
- Python 3.10+
- pip

## Build

```powershell
# Install dependencies and build the .exe
.\build_win.ps1
```

The output is at `dist\YouTrax\YouTrax.exe`. Distribute the entire `dist\YouTrax\` folder.

## Development

```powershell
pip install -r requirements.txt
python main.py
```

## Notes

- Settings are saved to `%USERPROFILE%\.config\ytdl\settings.json`
- Downloads default to `%USERPROFILE%\Downloads`
- The `ytmp3://` URL scheme is not supported on Windows (macOS only via the [YouTrax macOS repo](https://github.com/dropcap/youtrax))
