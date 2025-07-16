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

## 🛠 Setup Instructions

### 0. Install Python (Windows)

#### 1. Check if Python is installed
Open a new terminal and try:

```
python --version
```

```
python3 --version
```

If both fail, you need to install Python first.

#### 2. Install Python correctly
Download Python from:
👉 https://www.python.org/downloads/windows/

Important when installing: ✅ During installation, check the box that says "Add Python to PATH" at the beginning of the installer!

Without this, Windows can't find python in the terminal.


### 1. Create and activate virtual environment

#### Windows

```
python -m venv venv 
```

If you are using PowerShell on Windows you need to use this...
```
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

``` 
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

## ⚠ Common Errors

| Problem | Solution |
|---------|----------|
| "python not found" | Install Python from https://www.python.org/ (version 3.6+) and ensure it's in your system PATH |
| "DLL load failed" on Windows | Install Visual C++ Redistributable from Microsoft's website |
| Templates not found in executable | Use the correct `--add-data` format for your OS |
| "No module named 'xyz'" | Make sure all dependencies are properly installed, or add them to `--hidden-import` |
| Internal Server Error | Check the log file for detailed error information |

---