from io import BytesIO
from datetime import datetime
import logging
import threading
import os
import re
import time
import json
import csv
import sys
import importlib.util
import subprocess
import concurrent.futures
import requests
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template
from flask_cors import CORS
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variable for selected download folder
SELECTED_FOLDER = None

# Try to import Beautiful Soup for HTML parsing
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logger.warning("BeautifulSoup4 not available. Some functionality will be limited.")

# Check if KomootGPX is installed and its version
try:
    import komootgpx
    KOMOOTGPX_AVAILABLE = True
    KOMOOTGPX_VERSION = getattr(komootgpx, '__version__', 'unknown')
    logger.info(f"KomootGPX library found. Version: {KOMOOTGPX_VERSION}")
except ImportError:
    KOMOOTGPX_AVAILABLE = False
    logger.warning("KomootGPX library not found. Some features will be limited.")
    # Try to install it
    try:
        logger.info("Attempting to install KomootGPX...")
        subprocess.check_call(["pip", "install", "komootgpx"])
        import komootgpx
        KOMOOTGPX_AVAILABLE = True 
        KOMOOTGPX_VERSION = getattr(komootgpx, '__version__', 'unknown')
        logger.info(f"Successfully installed KomootGPX. Version: {KOMOOTGPX_VERSION}")
    except Exception as e:
        logger.error(f"Failed to install KomootGPX: {str(e)}")

# Import Komoot adapter
from komoot_adapter import KomootAdapter

# Create Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Status tracking for processing
processing_status = {
    'status': 'idle',
    'progress': 0.0,
    'tours_found': 0,
    'tours_completed': 0,
    'error': None,
    'log': [],
    'results': [],
    'next_chunk': 0
}

# Status tracking for collections
collections_status = {
    'status': 'idle',
    'progress': 0.0,
    'collections_found': 0,
    'collections_completed': 0,
    'error': None,
    'log': [],
    'results': []
}

# Lock for thread synchronization
processing_lock = threading.Lock()
collections_lock = threading.Lock()


def set_selected_folder(folder_path):
    """Set the selected download folder"""
    global SELECTED_FOLDER
    SELECTED_FOLDER = folder_path


def get_selected_folder():
    """Get the currently selected download folder"""
    global SELECTED_FOLDER
    
    if not SELECTED_FOLDER:
        # Default to user's home directory if not set
        default_folder = Path.home() / "komoot-takeout"
        default_folder.mkdir(parents=True, exist_ok=True)
        SELECTED_FOLDER = str(default_folder)
    
    return SELECTED_FOLDER


def get_default_output_dir(subdirectory=''):
    """Get the default output directory, using the selected folder if available"""
    base_dir = Path(get_selected_folder())
        
    # Create the full path including the subdirectory if provided
    if subdirectory:
        output_dir = base_dir / subdirectory
    else:
        output_dir = base_dir
        
    # Ensure the directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    return str(output_dir)


def reset_status(status_dict):
    """Reset the status dictionary to initial values"""
    status_dict['status'] = 'idle'
    status_dict['progress'] = 0.0
    status_dict['tours_found'] = 0
    status_dict['tours_completed'] = 0
    status_dict['collections_found'] = 0 if 'collections_found' in status_dict else 0
    status_dict['collections_completed'] = 0 if 'collections_completed' in status_dict else 0
    status_dict['error'] = None
    status_dict['log'] = []
    status_dict['results'] = []
    status_dict['next_chunk'] = 0 if 'next_chunk' in status_dict else 0


def add_log_entry(message, status_dict):
    """Add a timestamped log entry to the status dictionary"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    logger.info(message)
    
    if 'log' in status_dict:
        status_dict['log'].append(entry)


def sanitize_filename(name):
    """Basic function to sanitize filenames"""
    if not name:
        return "unnamed"
    # Remove characters not allowed in filenames
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Trim whitespace
    name = name.strip()
    # Limit length
    if len(name) > 100:
        name = name[:100]
    return name


def extract_user_id_from_url(url):
    """Extract user ID from a Komoot URL"""
    if not url:
        return None
        
    # Try to extract from /user/{user_id} pattern
    match = re.search(r'/user/([^/]+)', url)
    if match:
        return match.group(1)
    
    return None


def extract_collection_id_from_url(url):
    """Extract collection ID from a Komoot URL"""
    if not url:
        return None
        
    # Try to extract from /collection/{collection_id} pattern
    match = re.search(r'/collection/(\d+)', url)
    if match:
        return match.group(1)
    
    return None


def get_collection_slug(collection_url, collection_name, max_slug_length=50):
    """Get a slug for a collection, based on URL or name"""
    if not collection_url and not collection_name:
        return "unnamed-collection"
    
    # First try to extract slug from URL if it has one
    if collection_url:
        match = re.search(r'/collection/\d+/?-?([a-zA-Z0-9-]+)?', collection_url)
        if match and match.group(1):
            slug = match.group(1)
            # Limit length if needed
            if max_slug_length > 0 and len(slug) > max_slug_length:
                slug = slug[:max_slug_length]
            return slug
    
    # If no slug in URL or no URL, create one from name
    if collection_name:
        # Convert to lowercase
        slug = collection_name.lower()
        # Replace non-alphanumeric characters with hyphens
        slug = re.sub(r'[^a-z0-9]', '-', slug)
        # Replace multiple hyphens with a single one
        slug = re.sub(r'-+', '-', slug)
        # Remove leading and trailing hyphens
        slug = slug.strip('-')
        # Limit length
        if max_slug_length > 0 and len(slug) > max_slug_length:
            slug = slug[:max_slug_length]
        # If empty after processing, use a generic name
        if not slug:
            slug = "unnamed-collection"
        return slug
    
    # Fallback to collection ID
    if collection_url:
        match = re.search(r'/collection/(\d+)', collection_url)
        if match:
            return f"collection-{match.group(1)}"
    
    return "unnamed-collection"


def make_request_with_retry(url, headers, max_retries=3, timeout=30):
    """Make a request with retry logic for better reliability"""
    import requests
    import time
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            return response
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < max_retries - 1:
                sleep_time = 2 ** attempt  # Exponential backoff
                time.sleep(sleep_time)
            else:
                raise e


# Collection Manager class for handling collections
class CollectionManager:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir or get_default_output_dir('collections')
        self.user_id = None
        logger.info(f"Collection Manager initialized with output_dir: {self.output_dir}")
    
    def set_user_id(self, user_id):
        """Set the user ID for organization"""
        self.user_id = user_id
    
    def download_collection_tours(self, collections, output_dir, include_metadata=True, 
                                 output_dir_structure='collection', download_images=False, 
                                 gpx_options=None, user_id=None):
        """
        Download tours from the given collections as GPX files
        
        Args:
            collections: List of collections containing tours to download
            output_dir: Base directory to save GPX files
            include_metadata: Whether to save collection metadata
            output_dir_structure: Directory structure for organizing downloads
            download_images: Whether to download tour images
            gpx_options: Options for GPX file generation
            user_id: User ID for organization
            
        Returns:
            Dict with download results information
        """
        if not gpx_options:
            gpx_options = {}
            
        # If no user_id was provided, try to extract it from collections
        if not user_id:
            for collection in collections:
                if 'creator' in collection and 'id' in collection['creator']:
                    user_id = collection['creator']['id']
                    logger.info(f"Using creator ID from collection as user ID: {user_id}")
                    break
                    
                # If still no user ID, try to extract from URL
                if collection.get('url'):
                    extracted_id = extract_user_id_from_url(collection.get('url'))
                    if extracted_id:
                        user_id = extracted_id
                        logger.info(f"Extracted user ID from collection URL: {user_id}")
                        break
        
        # Set user ID for organization if found
        if user_id:
            self.set_user_id(user_id)
            
        # Start the background thread for downloading
        thread = threading.Thread(
            target=collections_module.download_collection_tours_thread,
            args=(collections, output_dir, include_metadata, output_dir_structure, 
                 download_images, gpx_options, user_id)
        )
        thread.start()
        
        return {
            'status': 'started',
            'message': f'Started downloading tours from {len(collections)} collections'
        }
    
    def save_collections_data(self, collections, user_id=None, enhance_tours=False):
        """
        Save collections data to files with user ID if available
        
        Args:
            collections: List of collection objects to save
            user_id: Optional user ID for organization
            enhance_tours: Whether these collections have enhanced tour data
            
        Returns:
            Dict with output paths information
            
        Raises:
            Exception: If saving collections data fails
        """
        try:
            if user_id:
                self.user_id = user_id
            
            if not self.user_id and not user_id:
                # Try to extract user ID from the first collection
                for collection in collections:
                    if 'creator' in collection and 'id' in collection['creator']:
                        self.user_id = collection['creator']['id']
                        break
            
            # If we have a user ID, create a user-specific directory
            if self.user_id:
                self.output_dir = str(Path(get_default_output_dir('collections')) / f"user-{self.user_id}")
            
            # Ensure the output directory exists
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            
            # Create a timestamp for filenames
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Ensure all collections have a tours array if not present
            for collection in collections:
                if 'tours' not in collection:
                    collection['tours'] = []
                    logger.warning(f"Collection {collection.get('id', 'unknown')} had no tours array, adding empty one")
            
            # Save collections to JSON files
            status_suffix = "_enhanced" if enhance_tours else "_basic"
            json_path = Path(self.output_dir) / f"all_collections.json"
            timestamped_json_path = Path(self.output_dir) / f"all_collections_{timestamp}{status_suffix}.json"
            
            import json
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(collections, f, indent=2, ensure_ascii=False)
            
            with open(timestamped_json_path, 'w', encoding='utf-8') as f:
                json.dump(collections, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(collections)} collections to {json_path} and {timestamped_json_path}")
            
            # Create collections directory for individual collection data
            collections_dir = Path(self.output_dir) / 'collections'
            collections_dir.mkdir(parents=True, exist_ok=True)
            
            # Save individual collection data and tours
            for collection in collections:
                if 'id' not in collection:
                    continue
                
                # Create slug for folder name
                slug = get_collection_slug(collection.get('url', ''), collection.get('name', ''))
                collection_dir = collections_dir / slug
                collection_dir.mkdir(parents=True, exist_ok=True)
                
                # Save collection info
                collection_info_path = collection_dir / 'collection_info.json'
                with open(collection_info_path, 'w', encoding='utf-8') as f:
                    json.dump(collection, f, indent=2, ensure_ascii=False)
                
                # Create human-readable summary
                summary_path = collection_dir / 'collection_info.txt'
                with open(summary_path, 'w', encoding='utf-8') as f:
                    f.write(f"Collection: {collection.get('name', 'Unnamed')}\n")
                    f.write(f"ID: {collection.get('id', 'Unknown')}\n")
                    f.write(f"Type: {collection.get('type', 'Unknown')}\n")
                    if 'tours' in collection:
                        f.write(f"Tours: {len(collection['tours'])}\n")
                    if 'creator' in collection and 'display_name' in collection['creator']:
                        f.write(f"Creator: {collection['creator']['display_name']}\n")
                    if 'description' in collection and collection['description']:
                        f.write(f"\nDescription:\n{collection['description']}\n")
                    
                    # Add tour summary information
                    if 'tours' in collection and collection['tours']:
                        f.write(f"\nTours in this collection ({len(collection['tours'])}):\n")
                        for i, tour in enumerate(collection['tours'], 1):
                            tour_name = tour.get('name', f"Tour {tour.get('id', 'unknown')}")
                            tour_distance = f"{tour.get('distance_km', tour.get('distance', 0)/1000 if tour.get('distance', 0) else 0):.1f}km" if tour.get('distance_km') or tour.get('distance') else "unknown distance"
                            f.write(f"{i}. {tour_name} ({tour_distance})\n")
                
                # Export tours as CSV
                if 'tours' in collection and collection['tours']:
                    import csv
                    csv_path = collection_dir / f"{slug}_tours.csv"
                    
                    # Define fields to include in the CSV - add more fields for comprehensive data
                    fields = ['id', 'name', 'url', 'sport', 'distance_km', 'duration', 
                             'elevation_up', 'elevation_down', 'date', 'region', 
                             'unpaved_percentage', 'singletrack_percentage']
                    
                    with open(csv_path, 'w', encoding='utf-8', newline='') as csvfile:
                        writer = csv.DictWriter(csvfile, fieldnames=fields, extrasaction='ignore')
                        writer.writeheader()
                        for tour in collection['tours']:
                            # Make sure all fields are present even if empty
                            row = {field: tour.get(field, '') for field in fields}
                            # Calculate distance_km if it's not directly available but distance is
                            if not row['distance_km'] and tour.get('distance'):
                                row['distance_km'] = tour.get('distance') / 1000
                            writer.writerow(row)
            
            # Create Jekyll _config.yml file
            self.generate_jekyll_config(collections)
            
            # Create user index.html that redirects to their Komoot profile
            if self.user_id:
                create_user_index_html(self.user_id, None)
            
            return {
                'output_dir': self.output_dir,
                'files': {
                    'json': str(json_path),
                    'timestamped_json': str(timestamped_json_path)
                }
            }
            
        except Exception as e:
            logger.error(f"Error saving collections data: {str(e)}")
            raise Exception(f"Failed to save collections data: {str(e)}")
    
    def generate_jekyll_config(self, collections):
        """
        Generate Jekyll _config.yml file from collections data.
        
        Args:
            collections: List of collection objects to use for Jekyll configuration
            
        Returns:
            str: Path to the generated config file or None on failure
            
        This creates a Jekyll-compatible configuration file that allows collections 
        to be used directly in a static site.
        """
        try:
            # Create Jekyll config file
            config_path = Path(self.output_dir) / '_config.yml'
            
            # Prepare collections data for Jekyll
            collections_data = []
            for collection in collections:
                if 'id' not in collection or 'name' not in collection:
                    continue
                
                slug = get_collection_slug(collection.get('url', ''), collection.get('name', ''))
                
                # Create collection data
                coll_data = {
                    'name': collection['name'],
                    'id': collection['id'],
                    'slug': slug,
                    'output': True
                }
                
                # Add description if available
                if 'description' in collection and collection['description']:
                    coll_data['description'] = collection['description']
                
                collections_data.append(coll_data)
            
            # Write config file
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write("# Jekyll configuration for Komoot collections\n")
                f.write("title: Komoot Collections\n")
                f.write("description: Exported Komoot collections\n\n")
                
                f.write("# Collections configuration\n")
                f.write("collections:\n")
                for coll in collections_data:
                    f.write(f"  {coll['slug']}:\n")
                    f.write(f"    output: true\n")
                    f.write(f"    name: {coll['name']}\n")
                    if 'description' in coll:
                        f.write(f"    description: {coll['description']}\n")
                
                f.write("\n# Defaults\n")
                f.write("defaults:\n")
                f.write("  - scope:\n")
                f.write("      path: \"\"\n")
                f.write("    values:\n")
                f.write("      layout: default\n")
                
                for coll in collections_data:
                    f.write(f"  - scope:\n")
                    f.write(f"      path: \"\"\n")
                    f.write(f"      type: {coll['slug']}\n")
                    f.write(f"    values:\n")
                    f.write(f"      layout: collection\n")
            
            logger.info(f"Generated Jekyll config at {config_path}")
            return str(config_path)
            
        except Exception as e:
            logger.error(f"Error generating Jekyll config: {str(e)}")
            return None


def create_user_index_html(user_id, user_name=None):
    """
    Create an index.html file that redirects to the user's Komoot profile
    
    Args:
        user_id: The Komoot user ID to link to
        user_name: Optional display name of the user
        
    Returns:
        str: Path to the created index.html file or None on failure
    """
    try:
        # Get collection directory
        output_dir = Path(get_default_output_dir('collections'))
        user_dir = output_dir / f"user-{user_id}"
        
        # Ensure the directory exists
        user_dir.mkdir(parents=True, exist_ok=True)
        
        # Create the HTML file
        index_path = user_dir / 'index.html'
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write("<!DOCTYPE html>\n")
            f.write("<html>\n")
            f.write("<head>\n")
            f.write(f"  <title>Komoot User Profile - {user_name or user_id}</title>\n")
            f.write("  <meta http-equiv=\"refresh\" content=\"0; URL=https://www.komoot.com/user/" + user_id + "\" />\n")
            f.write("</head>\n")
            f.write("<body>\n")
            f.write(f"  <p>Redirecting to <a href=\"https://www.komoot.com/user/{user_id}\">Komoot profile for {user_name or user_id}</a>...</p>\n")
            f.write("</body>\n")
            f.write("</html>\n")
        
        logger.info(f"Created user index.html at {index_path}")
        return str(index_path)
        
    except Exception as e:
        logger.error(f"Error creating user index.html: {str(e)}")
        return None


# Create instance of CollectionManager
collections_manager = CollectionManager()

# Import tours module functions that collections module needs
import tours
from tours import extract_tours_from_html, fetch_all_tours_from_collection

# Initialize tours module
tours.logger = logger
tours.KOMOOTGPX_AVAILABLE = KOMOOTGPX_AVAILABLE
tours.BS4_AVAILABLE = BS4_AVAILABLE
tours.BeautifulSoup = BeautifulSoup if BS4_AVAILABLE else None
tours.processing_status = processing_status
tours.processing_lock = processing_lock
tours.add_log_entry = add_log_entry
tours.reset_status = reset_status
tours.get_default_output_dir = get_default_output_dir
tours.sanitize_filename = sanitize_filename
tours.make_request_with_retry = make_request_with_retry
tours.extract_collection_id_from_url = extract_collection_id_from_url

# Import the collections module using a direct import to avoid collision with the built-in collections module
import importlib.util
collections_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'collections.py')
collections_spec = importlib.util.spec_from_file_location("collections_module", collections_path)
collections_module = importlib.util.module_from_spec(collections_spec)
collections_spec.loader.exec_module(collections_module)

# Initialize collections module
collections_module.logger = logger
collections_module.collections_status = collections_status
collections_module.collections_lock = collections_lock
collections_module.processing_status = processing_status
collections_module.processing_lock = processing_lock
collections_module.BS4_AVAILABLE = BS4_AVAILABLE
collections_module.BeautifulSoup = BeautifulSoup if BS4_AVAILABLE else None
collections_module.collections_manager = collections_manager
collections_module.add_log_entry = add_log_entry
collections_module.reset_status = reset_status
collections_module.extract_user_id_from_url = extract_user_id_from_url
collections_module.extract_collection_id_from_url = extract_collection_id_from_url
collections_module.make_request_with_retry = make_request_with_retry
collections_module.get_default_output_dir = get_default_output_dir
collections_module.get_collection_slug = get_collection_slug
collections_module.sanitize_filename = sanitize_filename
collections_module.extract_tours_from_html = extract_tours_from_html
collections_module.fetch_all_tours_from_collection = fetch_all_tours_from_collection

# Register routes
@app.route('/')
def index():
    """Render the main HTML template"""
    return render_template('index.html')


@app.route('/api/selected-folder', methods=['GET'])
def api_selected_folder():
    """Get the currently selected download folder"""
    return jsonify({'folder': get_selected_folder()})


@app.route('/api/select-folder', methods=['POST'])
def api_select_folder():
    """Set the selected download folder"""
    data = request.json
    folder_path = data.get('folder')
    
    if not folder_path:
        return jsonify({'error': 'No folder path provided'}), 400
        
    try:
        # Ensure the directory exists
        os.makedirs(folder_path, exist_ok=True)
        
        # Set as selected folder
        set_selected_folder(folder_path)
        
        return jsonify({
            'success': True,
            'folder': folder_path
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stop-process', methods=['POST'])
def stop_process():
    """Stop any running processes"""
    try:
        with processing_lock:
            # Reset status to idle
            if processing_status['status'] == 'running':
                processing_status['status'] = 'idle'
                processing_status['error'] = 'Process stopped by user'
                add_log_entry('Process stopped by user', processing_status)
                logger.info('Process stopped by user')
        
        # Also check collections status
        with collections_lock:
            if collections_status['status'] == 'running':
                collections_status['status'] = 'idle'
                collections_status['error'] = 'Process stopped by user'
                add_log_entry('Process stopped by user', collections_status)
                logger.info('Collections process stopped by user')
                
        # Return success response
        return jsonify({'success': True, 'message': 'Process stopped successfully'})
    except Exception as e:
        logger.error(f"Error stopping process: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Register tour routes
tours.register_tour_routes(app)

# Register collection routes
collections_module.register_collection_routes(app, {
    'logger': logger,
    'collections_status': collections_status,
    'collections_lock': collections_lock,
    'processing_status': processing_status,
    'processing_lock': processing_lock,
    'BS4_AVAILABLE': BS4_AVAILABLE,
    'BeautifulSoup': BeautifulSoup if BS4_AVAILABLE else None,
    'collections_manager': collections_manager,
    'add_log_entry': add_log_entry,
    'reset_status': reset_status,
    'extract_user_id_from_url': extract_user_id_from_url,
    'extract_collection_id_from_url': extract_collection_id_from_url,
    'make_request_with_retry': make_request_with_retry,
    'get_default_output_dir': get_default_output_dir,
    'get_collection_slug': get_collection_slug,
    'sanitize_filename': sanitize_filename,
    'extract_tours_from_html': extract_tours_from_html,
    'fetch_all_tours_from_collection': fetch_all_tours_from_collection
})

# Run Flask app if this is the main module
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5003)