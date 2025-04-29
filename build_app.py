import os
import sys
import shutil
import subprocess
import platform

def main():
    """Build the application with PyInstaller"""
    print("Building komoot-takeout with PyInstaller...")
    
    # Clean previous builds
    if os.path.exists('dist'):
        print("Cleaning previous builds...")
        shutil.rmtree('dist')
    
    # Determine data separator based on OS
    separator = ';' if platform.system().lower() == 'windows' else ':'
    
    # Build command
    cmd = [
        'pyinstaller',
        '--noconfirm',
        '--clean',
        '--name=komoot-takeout',
        '--onefile', 
        '--windowed',
        f'--add-data=templates/index.html{separator}templates',
        '--hidden-import=flask',
        '--hidden-import=komoot_adapter',
        '--hidden-import=bs4',
        '--hidden-import=gpxpy',
        '--hidden-import=webview',
        '--hidden-import=requests', 
        '--hidden-import=zipfile',
        '--hidden-import=concurrent.futures',
        '--hidden-import=komootgpx',
        'pywebview_app.py'
    ]
    
    print(f"Running build command...")
    
    # Run PyInstaller
    subprocess.check_call(cmd)
    
    print("\nBuild completed successfully!")
    print(f"Executable can be found in dist/komoot-takeout{'.exe' if platform.system().lower() == 'windows' else ''}")

if __name__ == "__main__":
    main()