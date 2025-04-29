# 🧭 komoot-takeout
**Get your tours. They're yours.**

A simple desktop app to download your Tours as GPX tracks from Komoot.  
Additionally to the features of **komootGPX**  with **komoot-takout** you can backup all your precious Collections from **komoot**.  
Built with Flask, PyWebView, and bundled into a native app using PyInstaller.

---

## 🖥 Supported Platforms

- ✅ Windows (.exe)
- ✅ macOS (.app)

---

![windows 11 - screenshot](windows11-screenshot.png)

---

## 🔎 About This Project

This project is built on top of the excellent [komootGPX](https://github.com/timschneeb/KomootGPX/) library by [timschneeb](https://github.com/timschneeb), which provides the core functionality for downloading GPX tracks from Komoot. Our desktop app provides a user-friendly interface for downloading and managing your Komoot tracks without needing to use the command line.

The application uses:
- **Flask**: For the backend web server
- **PyWebView**: To create a native-looking desktop window
- **Beautiful Soup**: For parsing Komoot web content
- **PyInstaller**: To package everything into a standalone executable

---

## 🔍 Key Features

- **One-click GPX downloads**: Download GPX files from Komoot with a single click
- **Batch download**: Download multiple tracks at once
- **Custom download folder**: Choose where to save your GPX files
- **Native desktop experience**: Runs as a standalone desktop application
- **No login required**: Works with publicly shared Komoot routes

---

## 📁 Project Structure

```
komoot-exporter/
├── app.py                   # Main Flask application
├── pywebview_app.py         # PyWebView wrapper for desktop app
├── komoot_adapter.py        # Adapter for Komoot API integration
├── build_app.py             # Script to build the executable
├── /templates/  
│   └── index.html           # Main UI template
├── /static/                 # Static assets (optional)
├── requirements.txt         # Project dependencies
└── venv/                    # Virtual environment (not included in Git)
```

---

## 🛠 Setup Instructions

### 1. Create and activate virtual environment

#### Windows

```
python -m venv venv  
venv\Scripts\activate
```

#### macOS/Linux

```
python3 -m venv venv  
source venv/bin/activate
```

---

### 2. Install dependencies

```
pip install -r requirements.txt
```

If you don't have requirements.txt yet, create it with:

```
pip install flask pywebview requests beautifulsoup4 gpxpy komootgpx
pip freeze > requirements.txt
```

---

## 🚀 Running in Development

Make sure the virtual environment is activated.

```
python pywebview_app.py
```

To run just the Flask server without the desktop window:

```
python app.py
```

Then open in your browser:

```
http://localhost:5001/
```

---

## 📦 Packaging into a Desktop App

### Using the build script (recommended)

We provide a build script that handles all the packaging details:

```
python build_app.py
```

### Manual packaging

#### Windows (Build .exe)

```
pyinstaller --noconfirm --clean --name=komoot-takeout --onefile --windowed --add-data=templates/index.html;templates --hidden-import=flask --hidden-import=komoot_adapter --hidden-import=bs4 --hidden-import=gpxpy --hidden-import=webview --hidden-import=requests --hidden-import=zipfile --hidden-import=concurrent.futures --hidden-import=komootgpx pywebview_app.py
```

#### macOS (Build .app)

```
pyinstaller --noconfirm --clean --name=komoot-takeout --onefile --windowed --add-data=templates/index.html:templates --hidden-import=flask --hidden-import=komoot_adapter --hidden-import=bs4 --hidden-import=gpxpy --hidden-import=webview --hidden-import=requests --hidden-import=zipfile --hidden-import=concurrent.futures --hidden-import=komootgpx pywebview_app.py
```

Note: On Windows use a semicolon `;`, on macOS use a colon `:` when specifying paths with --add-data.

Output will be placed in the `/dist/` folder:

```
/dist/komoot-takeout.exe     (Windows)
/dist/komoot-takeout         (macOS)
```

---

## 📂 Folder Management

At startup, the app creates a default folder in your home directory called "komoot-takeout". You can select a different folder from within the app interface.

All GPX files downloaded from Komoot will be saved to the selected folder. The application remembers your folder choice between sessions.

---

## ⚠ Common Errors

| Problem | Solution |
|---------|----------|
| "python not found" | Install Python from https://www.python.org/ (version 3.6+) and ensure it's in your system PATH |
| "DLL load failed" on Windows | Install Visual C++ Redistributable from Microsoft's website |
| Templates not found in executable | Use the correct `--add-data` format for your OS |
| "No module named 'xyz'" | Make sure all dependencies are properly installed, or add them to `--hidden-import` |
| Internal Server Error | Check the log file for detailed error information |

---

## 🐛 Troubleshooting

### Log Files

The application creates a log file called `komoot_exporter.log` in the directory where you run the executable. This file contains detailed information if something goes wrong.

### Known Issues

- On some Windows systems, you may see a Rectangle.op_Equality error in the logs. This is a known issue with PyWebView and doesn't affect functionality.
- If you have a firewall or antivirus, it might block the app from accessing the internet. Make sure to allow the app through your firewall.

---

## 🚀 Contributing

Contributions are welcome! If you'd like to improve the project:

1. Fork the repository
2. Create a new branch (`git checkout -b feature/improvement`)
3. Make your changes
4. Commit your changes (`git commit -am 'Add new feature'`)
5. Push to the branch (`git push origin feature/improvement`)
6. Create a new Pull Request

---

## 📃 License

MIT -- free to use, modify and share.

---

## 🙏 Acknowledgements

- [komootGPX](https://github.com/jaluebbe/komootgpx) library by jaluebbe for the core Komoot API integration
- [PyWebView](https://pywebview.flowrl.com/) for the desktop window framework
- [Flask](https://flask.palletsprojects.com/) for the web framework
- [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) for HTML parsing
- [PyInstaller](https://www.pyinstaller.org/) for executable packaging

---

## 📧 Contact

If you have any questions or suggestions, please open an issue on the GitHub repository.