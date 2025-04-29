import os
import sys
import time
import threading
import logging
import traceback
import webview
from pathlib import Path

# Configure logging (console only, no file)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Only keep console logging
    ]
)
logger = logging.getLogger(__name__)

# Log startup information
logger.info(f"Starting application with Python {sys.version}")
logger.info(f"Running from: {os.getcwd()}")
logger.info(f"Frozen: {getattr(sys, 'frozen', False)}")

# Helper function to get resource path for PyInstaller
def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    
    full_path = os.path.join(base_path, relative_path)
    return full_path

# Function to read HTML template
def read_index_template():
    try:
        template_path = get_resource_path(os.path.join('templates', 'index.html'))
        if os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                content = f.read()
                return content
        else:
            return "<html><body><h1>Error: Template not found</h1><p>Path: " + template_path + "</p></body></html>"
    except Exception as e:
        error_details = traceback.format_exc()
        return f"<html><body><h1>Error loading UI</h1><p>{str(e)}</p><pre>{error_details}</pre></body></html>"

# Preload the template content
HTML_CONTENT = read_index_template()

try:
    # Import app before any further imports to avoid circular imports
    from app import app, set_selected_folder, get_selected_folder
    
    # Register a new index route function without removing the old one
    @app.route('/')
    def custom_index_route():
        """Return the preloaded template content"""
        return HTML_CONTENT
        
except Exception as e:
    error_details = traceback.format_exc()
    logger.error(f"Error during initialization: {str(e)}\n{error_details}")

class Api:
    """API exposed to JavaScript in the webview"""
    
    def __init__(self):
        self.window = None
    
    def set_window(self, window):
        """Store reference to the window object"""
        self.window = window
    
    def select_folder(self):
        """Open folder selection dialog and return selected path"""
        try:
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
                selected_folder = result[0]
                # Verify the folder exists
                if os.path.exists(selected_folder) and os.path.isdir(selected_folder):
                    # Update the global selected folder in app.py
                    set_selected_folder(selected_folder)
                    return selected_folder
            return None
        except Exception as e:
            logger.error(f"Error selecting folder: {str(e)}")
            return None
    
    def get_folder(self):
        """Get the currently selected folder"""
        return get_selected_folder()
    
    def open_folder(self, path):
        """Open a folder in the file explorer"""
        try:
            if os.path.exists(path):
                if sys.platform == 'win32':
                    os.startfile(path)
                elif sys.platform == 'darwin':  # macOS
                    os.system(f'open "{path}"')
                else:  # Linux
                    os.system(f'xdg-open "{path}"')
                return True
            return False
        except Exception as e:
            logger.error(f"Error opening folder: {str(e)}")
            return False

def start_flask():
    """Start the Flask server"""
    try:
        # Use a different port to avoid conflicts
        app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Error starting Flask: {str(e)}\n{error_details}")
        sys.exit(1)

def main():
    """Main entry point for the application"""
    try:
        # Determine application paths
        if getattr(sys, 'frozen', False):
            # Running as compiled application
            application_path = os.path.dirname(sys.executable)
            # Don't change the working directory when running as a frozen app
            # This prevents creating unwanted directories
        else:
            # Running as script
            application_path = os.path.dirname(os.path.abspath(__file__))
            # Create templates directory in dev mode only
            templates_dir = os.path.join(application_path, 'templates')
            os.makedirs(templates_dir, exist_ok=True)
        
        # Create default download directory in user's home folder
        default_download_dir = os.path.join(str(Path.home()), "komoot-takeout")
        os.makedirs(default_download_dir, exist_ok=True)
        set_selected_folder(default_download_dir)
        
        # Set up API instance
        api = Api()
        
        # Start Flask in a separate thread
        flask_thread = threading.Thread(target=start_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        # Wait for Flask to start
        time.sleep(1.5)
        
        # Create window with the API exposed
        window = webview.create_window(
            "komoot-takeout", 
            "http://localhost:5001",
            width=1100, 
            height=900,
            js_api=api,
            min_size=(800, 600)
        )
        
        # Give the API access to the window
        api.set_window(window)
        
        # Start the webview event loop
        webview.start()
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"Error in main function: {str(e)}\n{error_details}")
        
        # Create an error dialog if possible
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Error Starting Application", 
                                f"An error occurred: {str(e)}")
        except:
            pass
        
        sys.exit(1)
    
if __name__ == "__main__":
    main()