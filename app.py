import os
import re
import time
import json
import csv
import logging
import zipfile
import threading
import traceback
import subprocess
from io import BytesIO
from datetime import datetime
import requests
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template
from flask_cors import CORS
from pathlib import Path
import concurrent.futures

# Global variable for selected download folder
SELECTED_FOLDER = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    'status': 'idle',  # 'idle', 'running', 'completed', 'error', 'chunk_completed'
    'progress': 0.0,
    'tours_found': 0,
    'tours_completed': 0,
    'error': None,
    'log': [],
    'results': [],
    'next_chunk': 0  # For chunked processing
}

# Status tracking for collections
collections_status = {
    'status': 'idle',  # 'idle', 'running', 'completed', 'error'
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
    logger.info(f"Selected download folder set to: {folder_path}")
    return folder_path

def get_selected_folder():
    """Get the currently selected download folder"""
    global SELECTED_FOLDER
    
    if not SELECTED_FOLDER:
        # Default to user's home directory if not set
        default_folder = os.path.join(str(Path.home()), "komoot-takeout")
        os.makedirs(default_folder, exist_ok=True)
        SELECTED_FOLDER = default_folder
    
    return SELECTED_FOLDER

def get_default_output_dir(subdirectory=''):
    """Get the default output directory, using the selected folder if available"""
    base_dir = get_selected_folder()
        
    # Create the full path including the subdirectory if provided
    if subdirectory:
        output_dir = os.path.join(base_dir, subdirectory)
    else:
        output_dir = base_dir
        
    # Ensure the directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    return output_dir

def reset_status(status_dict):
    """Reset the status dictionary to initial values"""
    status_dict['status'] = 'idle'
    status_dict['progress'] = 0.0
    status_dict['tours_found'] = 0
    status_dict['tours_completed'] = 0
    status_dict['collections_found'] = 0
    status_dict['collections_completed'] = 0
    status_dict['error'] = None
    status_dict['log'] = []
    status_dict['results'] = []
    status_dict['next_chunk'] = 0

def add_log_entry(message, status_dict):
    """Add a timestamped log entry to the status"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    with processing_lock:
        status_dict['log'].append(log_entry)
        # Keep log at reasonable size
        if len(status_dict['log']) > 200:
            status_dict['log'] = status_dict['log'][-100:]

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
    """Extract user ID from a collection or user URL"""
    match = re.search(r'/user/([^/]+)', url)
    if match:
        return match.group(1)
    return None

def extract_collection_id_from_url(url):
    """Extract collection ID from a URL"""
    match = re.search(r'/collection/(\d+)', url)
    if match:
        return match.group(1)
    return None

def get_collection_slug(collection_url, collection_name, max_slug_length=50):
    """
    Create a URL-friendly slug for a collection
    
    Args:
        collection_url: URL of the collection
        collection_name: Name of the collection
        max_slug_length: Maximum length of the slug
        
    Returns:
        String: slug in the format "{id}-{slug}"
    """
    # Extract collection ID
    collection_id = extract_collection_id_from_url(collection_url)
    if not collection_id:
        # Fallback if ID can't be extracted from URL
        return f"collection-unknown"
    
    # Try to extract slug from URL
    url_slug = ""
    match = re.search(r'/collection/\d+/-(.*?)(?:/|$)', collection_url)
    if match:
        url_slug = match.group(1)
    
    # If URL doesn't have a slug, create one from the name
    if not url_slug:
        # Convert name to lowercase and replace spaces with hyphens
        url_slug = collection_name.lower().replace(' ', '-')
        # Remove special characters
        url_slug = re.sub(r'[^a-z0-9-]', '', url_slug)
        # Remove consecutive hyphens
        url_slug = re.sub(r'-+', '-', url_slug)
        # Remove leading/trailing hyphens
        url_slug = url_slug.strip('-')
    
    # Apply length limit
    if max_slug_length > 0 and len(url_slug) > max_slug_length:
        url_slug = url_slug[:max_slug_length]
    
    # Final format: id-slug
    return f"{collection_id}-{url_slug}"

def create_user_index_html(user_id, user_name=None):
    """
    Create an index.html file that redirects to the user's Komoot profile
    
    Args:
        user_id: User ID for the URL
        user_name: Display name for the page title
        
    Returns:
        String: HTML content
    """
    profile_url = f"https://www.komoot.com/user/{user_id}"
    title = f"Komoot Profile: {user_name or user_id}"
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="0; url={profile_url}">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.5;
            max-width: 600px;
            margin: 0 auto;
            padding: 2rem;
            text-align: center;
        }}
        h1 {{
            color: #2a6ebb;
        }}
        p {{
            margin-bottom: 1rem;
        }}
        a {{
            color: #2a6ebb;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <p>Redirecting to Komoot profile page...</p>
    <p>If you are not redirected automatically, click <a href="{profile_url}">here</a>.</p>
</body>
</html>
"""
    return html

def make_request_with_retry(url, headers, max_retries=3, timeout=30):
    """Make a request with retry logic for better reliability"""
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
        self.base_output_dir = output_dir if output_dir else get_default_output_dir('collections')
        self.output_dir = self.base_output_dir
        
    def set_user_id(self, user_id):
        """Set user ID to organize collections"""
        if user_id:
            self.output_dir = os.path.join(self.base_output_dir, f"user-{user_id}")
        else:
            self.output_dir = self.base_output_dir
        
    def save_collections_data(self, collections, user_id=None):
        """Save collections data to JSON and CSV files with enhanced formatting"""
        try:
            logger.info(f"Saving data for {len(collections)} collections")
            
            # Update output directory with user ID if provided
            if user_id:
                self.set_user_id(user_id)
            
            # Create output directory if it doesn't exist
            os.makedirs(self.output_dir, exist_ok=True)
            
            # Create index.html for user
            user_name = None
            if len(collections) > 0 and 'creator' in collections[0]:
                user_name = collections[0]['creator'].get('display_name')
                
            if user_id:
                index_html_content = create_user_index_html(user_id, user_name)
                with open(os.path.join(self.output_dir, 'index.html'), 'w', encoding='utf-8') as f:
                    f.write(index_html_content)
                logger.info(f"Created index.html redirect for user {user_id}")
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Save all collections to a single JSON file
            json_path = os.path.join(self.output_dir, f"all_collections.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(collections, f, indent=2, ensure_ascii=False)
            
            # Create timestamped copy
            timestamped_json_path = os.path.join(self.output_dir, f"all_collections_{timestamp}.json")
            with open(timestamped_json_path, 'w', encoding='utf-8') as f:
                json.dump(collections, f, indent=2, ensure_ascii=False)
            
            # Create separate CSV files for each collection's tours with enhanced metadata
            for collection in collections:
                if 'tours' in collection and collection['tours']:
                    # Process tours to remove duplicates
                    unique_tours = {}
                    for tour in collection['tours']:
                        tour_id = tour['id']
                        # Use the more detailed version of the tour
                        if tour_id in unique_tours:
                            # If the new tour has a more descriptive name (not just "Tour ID"), use it
                            if unique_tours[tour_id]['name'].startswith('Tour ') and not tour['name'].startswith('Tour '):
                                unique_tours[tour_id] = tour
                        else:
                            unique_tours[tour_id] = tour
                    
                    # Convert back to list
                    deduplicated_tours = list(unique_tours.values())
                    
                    # Get URL-friendly slug for the collection
                    slug = get_collection_slug(collection.get('url', ''), collection.get('name', ''))
                    
                    # Create collections directory if needed
                    collections_dir = os.path.join(self.output_dir, 'collections')
                    os.makedirs(collections_dir, exist_ok=True)
                    
                    csv_filename = f"{slug}_tours.csv"
                    csv_path = os.path.join(collections_dir, csv_filename)
                    
                    # Create a dataset similar to the bikepacking.com format
                    csv_tours = []
                    
                    # Post-process tours with enhanced fields
                    for i, tour in enumerate(deduplicated_tours):
                        # Create standardized tour object
                        distance_km = tour.get('distance_km', tour.get('distance', 0)/1000 if tour.get('distance', 0) else 0)
                        
                        csv_tour = {
                            "id": tour.get('id', ''),
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "name": tour.get('name', f"Tour {tour.get('id', 'unknown')}"),
                            "distance_km": f"{distance_km:.1f}" if distance_km else "Unknown",
                            "distance_mi": f"{distance_km * 0.621371:.1f}" if distance_km else "Unknown",
                            "duration": f"{tour.get('duration_hours', tour.get('duration', 0)/3600):.1f}" if tour.get('duration', 0) else "Unknown",
                            "unpaved_percentage": tour.get('unpaved_percentage', ''),
                            "singletrack_percentage": tour.get('singletrack_percentage', ''),
                            "rideable_percentage": tour.get('rideable_percentage', ''),
                            "total_ascent": tour.get('elevation_up', ''),
                            "total_descent": tour.get('elevation_down', ''),
                            "high_point": tour.get('high_point', ''),
                            "country": tour.get('country', ''),
                            "region": collection.get('region', ''),
                            "collection_name": collection.get('name', ''),
                            "collection_id": collection.get('id', ''),
                            "description": tour.get('description', ''),
                            "url": tour.get('url', f"https://www.komoot.com/tour/{tour.get('id', '')}"),
                            "gpx_url": tour.get('gpx_url', ''),
                            "image_url": tour.get('image_url', ''),
                            "collection_cover_image": collection.get('cover_image_url', ''),
                            "date_created": tour.get('date', '')[:10] if tour.get('date') else '',
                            "sport_type": tour.get('sport', '')
                        }
                        
                        # Add calculated fields
                        if tour.get('elevation_up') and distance_km:
                            try:
                                # Calculate meters climbed per kilometer
                                meters_per_km = float(tour['elevation_up']) / float(distance_km)
                                csv_tour["climbing_intensity"] = f"{meters_per_km:.1f}"
                            except:
                                csv_tour["climbing_intensity"] = ""
                        else:
                            csv_tour["climbing_intensity"] = ""
                                
                        csv_tours.append(csv_tour)
                    
                    # Define fields to include in the CSV - only include fields that have data
                    mandatory_fields = ["id", "timestamp", "name", "distance_km", "distance_mi", "duration"]
                    optional_fields = [
                        "unpaved_percentage", "singletrack_percentage", "rideable_percentage", 
                        "total_ascent", "total_descent", "high_point", "climbing_intensity",
                        "country", "region", "collection_name", "collection_id", 
                        "sport_type", "description", "url", "gpx_url", "image_url", 
                        "collection_cover_image", "date_created"
                    ]
                    
                    # Filter out empty optional fields
                    has_data = {field: any(tour.get(field) for tour in csv_tours) for field in optional_fields}
                    fieldnames = mandatory_fields + [field for field in optional_fields if has_data[field]]
                    
                    # Write the CSV file
                    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                        writer.writeheader()
                        writer.writerows(csv_tours)
                    
                    # Update the collection with deduplicated tours
                    collection['tours'] = deduplicated_tours
                    collection['tours_count'] = len(deduplicated_tours)
            
            logger.info(f"Saved collection data to {self.output_dir}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving collections data: {str(e)}")
            return False
    
    def get_collections_zip(self):
        """Create a zip file with all collection data"""
        try:
            # Check if there are any files to zip
            if not os.path.exists(self.base_output_dir):
                return None
                
            # Create a BytesIO object to store the zip file
            zip_buffer = BytesIO()
            
            # Create a ZIP file
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(self.base_output_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, os.path.dirname(self.base_output_dir))
                        zipf.write(file_path, arcname)
            
            # Reset file pointer to beginning
            zip_buffer.seek(0)
            return zip_buffer
            
        except Exception as e:
            logger.error(f"Error creating collections zip: {str(e)}")
            return None

# Create instance of CollectionManager
collections_manager = CollectionManager()

def download_tour_using_gpx_api(tour_id):
    """Download tour directly from Komoot's GPX API"""
    gpx_url = f"https://www.komoot.com/api/v007/tours/{tour_id}/gpx"
    response = requests.get(gpx_url)
    if response.status_code != 200:
        raise Exception(f"Failed to download tour from GPX API: HTTP {response.status_code}")
    return response.content

def download_tour_using_komootgpx(tour_id, email=None, password=None, output_dir=None, 
                                 include_poi=True, max_desc_length=-1, max_title_length=-1, add_date=True):
    """Download a tour using the KomootGPX library"""
    try:
        if not KOMOOTGPX_AVAILABLE:
            return None
            
        # Create output directory if needed
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            
        # Build arguments for KomootGPX command
        args = ["komootgpx"]
        
        # Add authentication if provided
        if email and password:
            args.extend(["-m", email, "-p", password])
        else:
            args.append("-n")  # Anonymous mode
            
        # Add tour ID
        args.extend(["-d", str(tour_id)])
        
        # Add output directory
        if output_dir:
            args.extend(["-o", output_dir])
            
        # Add POI option
        if not include_poi:
            args.append("-e")
            
        # Add description length
        if max_desc_length >= 0:
            args.extend(["--max-desc-length", str(max_desc_length)])
            
        # Add title length
        if max_title_length >= 0:
            args.extend(["--max-title-length", str(max_title_length)])
            
        # Add date option
        if add_date:
            args.append("-D")
            
        # Run KomootGPX command
        logger.info(f"Running KomootGPX with args: {args}")
        result = subprocess.run(args, capture_output=True, text=True)
        
        # Check result
        if result.returncode != 0:
            logger.error(f"KomootGPX failed: {result.stderr}")
            return None
            
        # Extract filename from output
        output = result.stdout
        match = re.search(r"GPX file written to '(.+?)'", output)
        if match:
            full_path = match.group(1)
            filename = os.path.basename(full_path)
            logger.info(f"KomootGPX wrote file to {full_path}")
            return filename
            
        return None
        
    except Exception as e:
        logger.error(f"Error using KomootGPX: {str(e)}")
        return None

def extract_tours_from_html(html_content, status_dict):
    """
    Extract all tours from a collection page HTML content
    
    This function is enhanced to handle different page layouts and tour card formats
    in Komoot collection pages.
    """
    try:
        if not BS4_AVAILABLE:
            add_log_entry("BeautifulSoup4 is required for HTML parsing but not installed", status_dict)
            return []
        
        soup = BeautifulSoup(html_content, 'html.parser')
        tours = []
        
        # Track seen tour IDs to prevent duplicates
        seen_tour_ids = set()
        
        # Try different selectors to find tour cards
        tour_card_selectors = [
            "div.tour-card", 
            ".collection-tour-card",
            "a[href*='/tour/']",
            ".tw-mb-8",  # Newer Komoot layout
            "[data-test='tour-item']"  # Data attribute used in some layouts
        ]
        
        # Try each selector and collect all possible tour cards
        all_cards = []
        for selector in tour_card_selectors:
            cards = soup.select(selector)
            if cards:
                all_cards.extend(cards)
                add_log_entry(f"Found {len(cards)} elements with selector '{selector}'", status_dict)
        
        # Process each potential tour card
        for card in all_cards:
            try:
                # Try to extract tour ID first to filter duplicates
                tour_id = None
                
                # Check if the card itself is a link to a tour
                if card.name == 'a' and '/tour/' in card.get('href', ''):
                    match = re.search(r'/tour/(\d+)', card.get('href', ''))
                    if match:
                        tour_id = match.group(1)
                
                # If not, look for tour links within the card
                if not tour_id:
                    tour_links = card.select("a[href*='/tour/']")
                    for link in tour_links:
                        match = re.search(r'/tour/(\d+)', link.get('href', ''))
                        if match:
                            tour_id = match.group(1)
                            break
                
                # Skip if no ID found or already processed this tour
                if not tour_id or tour_id in seen_tour_ids:
                    continue
                
                seen_tour_ids.add(tour_id)
                
                # Create basic tour object
                tour = {'id': tour_id}
                
                # Get tour URL
                tour_url = f"https://www.komoot.com/tour/{tour_id}"
                tour['url'] = tour_url
                
                # Try to extract tour name using multiple selectors
                name_selectors = [
                    "div.tour-card__title", 
                    "h3", 
                    "h2",
                    "h4",
                    ".tour-card-title",
                    ".tw-line-clamp-2",  # New Komoot layout
                    ".tw-font-bold"  # Often used for titles
                ]
                
                tour_name = f"Tour {tour_id}"  # Default name
                
                for selector in name_selectors:
                    name_elems = card.select(selector)
                    for elem in name_elems:
                        # Skip elements that are likely not the title
                        if elem.find('svg') or elem.select('svg'):
                            continue
                            
                        text = elem.get_text(strip=True)
                        if text and len(text) > 3 and len(text) < 100:
                            tour_name = text
                            break
                
                tour['name'] = tour_name
                
                # Extract date if available
                date_selectors = ["time.tour-card__date", "time[datetime]", ".tour-date"]
                for selector in date_selectors:
                    date_elem = card.select_one(selector)
                    if date_elem and 'datetime' in date_elem.attrs:
                        tour['date'] = date_elem['datetime']
                        break
                
                # Extract stats using multiple approaches
                # Look for stats elements with various selectors
                stats_selectors = [
                    ".tour-stats__value",
                    ".tour-card__distance",
                    ".tour-card__duration",
                    ".tour-card-stat",
                    ".tw-text-sm",  # Often contains stats in newer layouts
                    "span.tour-stats__stat-text",
                    "div.tour-card__stats-section span"
                ]
                
                for selector in stats_selectors:
                    stats = card.select(selector)
                    for stat in stats:
                        text = stat.get_text(strip=True)
                        
                        # Extract distance
                        if 'km' in text.lower() and 'distance' not in tour:
                            dist_match = re.search(r'([\d.,]+)', text)
                            if dist_match:
                                try:
                                    distance_km = float(dist_match.group(1).replace(',', '.'))
                                    tour['distance'] = distance_km * 1000  # Convert to meters
                                    tour['distance_km'] = distance_km
                                except:
                                    pass
                        
                        # Extract duration
                        elif ('h' in text or 'min' in text.lower()) and 'duration' not in tour:
                            hours = 0
                            minutes = 0
                            
                            h_match = re.search(r'(\d+)h', text)
                            min_match = re.search(r'(\d+)min', text)
                            
                            if h_match:
                                hours = int(h_match.group(1))
                            if min_match:
                                minutes = int(min_match.group(1))
                                
                            if hours > 0 or minutes > 0:
                                tour['duration'] = (hours * 3600) + (minutes * 60)  # Convert to seconds
                        
                        # Extract elevation
                        elif '↑' in text and 'elevation_up' not in tour:
                            elev_match = re.search(r'([\d.,]+)', text)
                            if elev_match:
                                try:
                                    tour['elevation_up'] = int(elev_match.group(1).replace(',', '').replace('.', ''))
                                except:
                                    pass
                        elif '↓' in text and 'elevation_down' not in tour:
                            elev_match = re.search(r'([\d.,]+)', text)
                            if elev_match:
                                try:
                                    tour['elevation_down'] = int(elev_match.group(1).replace(',', '').replace('.', ''))
                                except:
                                    pass
                
                # Try to extract sport type
                sport_selectors = [
                    ".tour-card__sport-type",
                    ".tour-type",
                    ".collection-card__sport"
                ]
                
                for selector in sport_selectors:
                    sport_elems = card.select(selector)
                    for elem in sport_elems:
                        text = elem.get_text(strip=True).lower()
                        if text:
                            tour['sport'] = text
                            break
                
                # Try to extract surface info (unpaved, singletrack percentages)
                surface_selectors = [
                    ".tour-stats__surface",
                    ".surface-stats",
                    ".tw-text-xs"  # Often contains surface info in newer layouts
                ]

                for selector in surface_selectors:
                    surface_elems = card.select(selector)
                    for elem in surface_elems:
                        text = elem.get_text(strip=True).lower()
                        
                        # Look for unpaved percentage
                        unpaved_match = re.search(r'(\d+)%\s*unpaved', text)
                        if unpaved_match:
                            tour['unpaved_percentage'] = unpaved_match.group(1)
                            
                        # Look for singletrack percentage
                        singletrack_match = re.search(r'(\d+)%\s*singletrack', text)
                        if singletrack_match:
                            tour['singletrack_percentage'] = singletrack_match.group(1)
                            
                        # Look for rideable percentage
                        rideable_match = re.search(r'(\d+)%\s*rideable', text)
                        if rideable_match:
                            tour['rideable_percentage'] = rideable_match.group(1)
                
                # Try to extract images
                image_selectors = [
                    "img.tour-card__image", 
                    "img.lazyload", 
                    "img.lazy",
                    "img[src*='cdn-assets']",
                    "img"
                ]
                
                for selector in image_selectors:
                    img_elems = card.select(selector)
                    for img in img_elems:
                        if img.get('src'):
                            tour['image_url'] = img['src']
                            break
                        elif img.get('data-src'):
                            tour['image_url'] = img['data-src']
                            break
                        elif img.get('data-lazyload'):
                            tour['image_url'] = img['data-lazyload']
                            break
                
                # Add to results
                tours.append(tour)
                
            except Exception as e:
                add_log_entry(f"Error extracting tour from card: {str(e)}", status_dict)
        
        add_log_entry(f"Successfully extracted {len(tours)} unique tours from HTML content", status_dict)
        return tours
    
    except Exception as e:
        add_log_entry(f"Error parsing HTML content: {str(e)}", status_dict)
        return []

def fetch_all_tours_from_collection(adapter, collection_url, status_dict, max_workers=5):
    """
    Fetch all tours from a collection using systematic pagination with concurrency
    
    This function implements multiple strategies to retrieve all tours from
    large collections, including standard pagination and page size parameters.
    Uses a ThreadPoolExecutor for concurrent processing to improve speed.
    """
    try:
        collection_id = extract_collection_id_from_url(collection_url)
        if not collection_id:
            add_log_entry(f"Could not extract collection ID from URL: {collection_url}", status_dict)
            return None
        
        # First, get the collection details using the URL to get basic info
        collection = adapter.fetch_collection_by_url(collection_url)
        if not collection:
            add_log_entry(f"Failed to fetch collection data from URL: {collection_url}", status_dict)
            return None
        
        # Setup browser-like headers for requests
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.komoot.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
        
        # Get the tours directly from the base page with retry
        add_log_entry(f"Fetching collection page directly: {collection_url}", status_dict)
        response = make_request_with_retry(collection_url, headers, max_retries=3)
        
        if response.status_code != 200:
            add_log_entry(f"Failed to fetch collection page: HTTP {response.status_code}", status_dict)
        else:
            # Extract tours directly from the HTML
            base_tours = extract_tours_from_html(response.text, status_dict)
            if base_tours:
                collection['tours'] = base_tours
                collection['tours_count'] = len(base_tours)
                add_log_entry(f"Found {len(base_tours)} tours from base collection page", status_dict)
        
        # Extract collection cover image URL
        if response and response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract collection metadata from the page
            # Try to get collection title
            title_elem = soup.select_one("h1.collection__title") or soup.select_one("h1")
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                collection['name'] = title_text
            
            # Try to get collection description
            desc_elem = soup.select_one(".collection__description") or soup.select_one("p.tw-mt-2")
            if desc_elem:
                desc_text = desc_elem.get_text(strip=True)
                collection['description'] = desc_text
            
            # Try to get creator name
            creator_elem = soup.select_one(".collection__creator-name") or soup.select_one(".tw-text-sm a")
            if creator_elem:
                creator_name = creator_elem.get_text(strip=True)
                if 'creator' not in collection:
                    collection['creator'] = {}
                collection['creator']['display_name'] = creator_name
                
            # Get cover image URL using multiple methods
            cover_image_url = None
            
            # Method 1: Look for meta og:image tag
            og_image = soup.select_one('meta[property="og:image"]')
            if og_image and 'content' in og_image.attrs:
                cover_image_url = og_image['content']
                add_log_entry(f"Found cover image URL from og:image meta tag", status_dict)
            
            # Method 2: Look for collection cover image in the page
            if not cover_image_url:
                cover_selectors = [
                    ".c-collection-cover__image img",
                    ".css-1dhdnz7",  # Class used in the provided HTML
                    "img[alt*='Collection']",
                    "img[sizes*='1344px']"  # Large images are likely covers
                ]
                
                for selector in cover_selectors:
                    img_elem = soup.select_one(selector)
                    if img_elem and 'src' in img_elem.attrs:
                        cover_image_url = img_elem['src']
                        add_log_entry(f"Found cover image URL using selector: {selector}", status_dict)
                        break
                    
            # If found, add to collection
            if cover_image_url:
                collection['cover_image_url'] = cover_image_url
                add_log_entry(f"Added cover image URL to collection", status_dict)
        
        # Try to get the total number of tours from collection page metadata
        expected_count = 0
        if 'expected_tours_count' in collection and collection['expected_tours_count']:
            expected_count = collection['expected_tours_count']
        else:
            # Try to parse from HTML if available
            if BS4_AVAILABLE and response and response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for tour count in various locations
                # First try the collection stats header that shows "XX routes"
                count_text = None
                routes_elem = soup.select_one('.tw-flex-none') or soup.select_one('.collection__stats')
                if routes_elem:
                    count_text = routes_elem.text.strip()
                    count_match = re.search(r'(\d+)\s*routes', count_text)
                    if count_match:
                        expected_count = int(count_match.group(1))
                        collection['expected_tours_count'] = expected_count
                        add_log_entry(f"Extracted expected tour count from page header: {expected_count}", status_dict)
                
                # If not found, try other selectors for tour count
                if not expected_count:
                    count_selectors = [
                        ".collection-header__stat-value", 
                        ".collection-meta-data__data",
                        ".tour-count",
                        ".collection-meta-data__item"
                    ]
                    
                    for selector in count_selectors:
                        count_elems = soup.select(selector)
                        for elem in count_elems:
                            text = elem.text.strip()
                            # Look for numbers that could be tour counts
                            match = re.search(r'\b(\d+)\b', text)
                            if match:
                                potential_count = int(match.group(1))
                                # If it's a reasonable number that's higher than what we have already
                                if potential_count > len(collection.get('tours', [])) and potential_count < 1000:
                                    expected_count = potential_count
                                    collection['expected_tours_count'] = expected_count
                                    add_log_entry(f"Extracted expected tour count: {expected_count}", status_dict)
                                    break
        
        # Initialize tracking for all tours found
        all_tour_ids = set(tour['id'] for tour in collection.get('tours', []))
        all_tours = collection.get('tours', []).copy()
        
        # Count tours found in initial fetch
        initial_tour_count = len(all_tours)
        
        # Adjust max_workers based on expected collection size
        if expected_count > 100:
            max_workers = min(8, (expected_count // 50) + 3)
        
        # If we need more tours, implement systematic pagination using concurrency
        if expected_count > initial_tour_count or expected_count == 0:
            add_log_entry(f"Need more tours: found {initial_tour_count}, expected {expected_count or 'unknown'}", status_dict)
            
            # Generate URLs to try different approaches
            urls_to_try = []
            
            # Approach 1: Systematic pagination with ?page=N parameter
            max_pages = 20  # Safety limit - most collections won't exceed this
            for page in range(2, max_pages + 1):
                page_url = f"{collection_url}?page={page}"
                urls_to_try.append(('page', page_url))
            
            # Approach 2: Different page sizes
            page_sizes = [50, 100, 200, 300, 500]
            for page_size in page_sizes:
                size_url = f"{collection_url}?size={page_size}"
                urls_to_try.append(('size', size_url))
            
            # Use a thread pool to fetch pages concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Define the function to process a URL
                def process_url(url_tuple):
                    url_type, url = url_tuple
                    try:
                        # Add a small delay to avoid rate limiting
                        time.sleep(0.2)
                        
                        # Fetch the page with retry
                        page_response = make_request_with_retry(url, headers, max_retries=2)
                        
                        if page_response.status_code == 200:
                            # Extract tours from this page
                            page_tours = extract_tours_from_html(page_response.text, status_dict)
                            return url_type, url, page_tours
                        else:
                            add_log_entry(f"Failed to fetch {url_type} URL {url}: HTTP {page_response.status_code}", status_dict)
                            return url_type, url, []
                    except Exception as e:
                        add_log_entry(f"Error processing {url_type} URL {url}: {str(e)}", status_dict)
                        return url_type, url, []
                
                # Submit all URLs to the thread pool
                future_to_url = {executor.submit(process_url, url_tuple): url_tuple for url_tuple in urls_to_try}
                
                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_url):
                    url_type, url, page_tours = future.result()
                    
                    # Add new tours to our collection
                    new_tours_count = 0
                    for tour in page_tours:
                        if tour['id'] not in all_tour_ids:
                            all_tour_ids.add(tour['id'])
                            all_tours.append(tour)
                            new_tours_count += 1
                    
                    if new_tours_count > 0:
                        add_log_entry(f"Found {new_tours_count} new tours from {url_type} URL {url}, total now: {len(all_tours)}", status_dict)
        
        # Update collection with all found tours
        collection['tours'] = all_tours
        collection['tours_count'] = len(all_tours)
        
        # Log final count
        add_log_entry(f"Total tours found for collection: {len(all_tours)}/{expected_count or '?'}", status_dict)
        
        return collection
        
    except Exception as e:
        add_log_entry(f"Error fetching all tours from collection: {str(e)}", status_dict)
        return None

@app.route('/')
def index():
    """Render the main application page"""
    return render_template('index.html')

@app.route('/api/selected-folder', methods=['GET'])
def get_selected_folder_api():
    """API endpoint to get the selected folder"""
    folder = get_selected_folder()
    return jsonify({
        'selected': bool(folder),
        'path': folder
    })

@app.route('/api/select-folder', methods=['POST'])
def select_folder_api():
    """API endpoint to set the selected folder"""
    data = request.json
    if not data or 'path' not in data:
        return jsonify({'error': 'No folder path provided'}), 400
    
    path = data['path']
    
    # Validate the folder exists
    if not os.path.exists(path):
        try:
            # Try to create it
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            return jsonify({'error': f'Could not create folder: {str(e)}'}), 400
    
    # Set the global folder
    set_selected_folder(path)
    
    return jsonify({
        'success': True,
        'path': path,
        'message': f'Selected folder: {path}'
    })

@app.route('/api/start', methods=['POST'])
def start_processing():
    """Start the processing based on provided parameters"""
    try:
        # Get parameters from request
        data = request.json
        if data is None:
            return jsonify({'error': 'No JSON data received'}), 400
            
        # Extract parameters
        anonymous = data.get('anonymous', False)
        email = data.get('email')
        password = data.get('password')
        tour_selection = data.get('tourSelection', 'all')
        filter_type = data.get('filterType', 'all')
        no_poi = data.get('noPoi', False)
        
        # Get output directory - either from the request or use default
        output_dir = data.get('outputDir')
        if not output_dir or output_dir.startswith('static/'):
            # If no directory specified or using the old static path, use the default
            output_dir = get_default_output_dir('gpx')
        
        skip_existing = data.get('skipExisting', True)
        id_filename = data.get('idFilename', False)
        add_date = data.get('addDate', True)
        max_title_length = data.get('maxTitleLength', -1)
        max_desc_length = data.get('maxDescLength', -1)
        download_images = data.get('downloadImages', False)
        
        # Optional chunking parameters
        chunk_size = data.get('chunkSize', 0)
        chunk_start = data.get('chunkStart', 0)
        
        # Validate required fields
        if not anonymous and (not email or not password):
            return jsonify({'error': 'Email and password are required for non-anonymous mode'}), 400
            
        if anonymous and tour_selection == 'all':
            return jsonify({'error': 'Cannot use "all tours" in anonymous mode'}), 400
            
        if tour_selection != 'all' and not tour_selection:
            return jsonify({'error': 'Tour ID is required for single tour download'}), 400
        
        # Reset status 
        with processing_lock:
            reset_status(processing_status)
        
        # Log the output directory for debugging
        add_log_entry(f"Output directory set to: {output_dir}", processing_status)
        
        # For single tour, try KomootGPX first if available
        if tour_selection != 'all' and KOMOOTGPX_AVAILABLE:
            with processing_lock:
                processing_status['status'] = 'running'
                processing_status['tours_found'] = 1
                processing_status['progress'] = 0.1
                
            add_log_entry(f"Using KomootGPX to download tour {tour_selection}", processing_status)
            
            # Create output directory if needed
            os.makedirs(output_dir, exist_ok=True)
            
            # Download the tour
            filename = download_tour_using_komootgpx(
                tour_id=tour_selection,
                email=email if not anonymous else None,
                password=password if not anonymous else None,
                output_dir=output_dir,
                include_poi=(not no_poi),
                max_desc_length=max_desc_length,
                max_title_length=max_title_length,
                add_date=add_date
            )
            
            if filename:
                # Get additional tour info
                adapter = KomootAdapter()
                
                # Login if needed
                if not anonymous:
                    add_log_entry(f"Logging in to get tour details", processing_status)
                    adapter.login(email, password)
                
                # Try to get tour details
                try:
                    tour_data = adapter.fetch_tour(tour_selection, anonymous=anonymous)
                except:
                    tour_data = None
                
                # Download images if requested
                image_paths = []
                if download_images and tour_data:
                    try:
                        images_dir = os.path.join(output_dir, '../images')
                        os.makedirs(images_dir, exist_ok=True)
                        image_paths = adapter.download_tour_images(
                            tour_id=tour_selection,
                            tour=tour_data,
                            output_dir=images_dir
                        )
                        add_log_entry(f"Downloaded {len(image_paths)} images for tour {tour_selection}", processing_status)
                    except Exception as img_err:
                        add_log_entry(f"Error downloading images: {str(img_err)}", processing_status)
                
                # Create result
                result = {
                    'id': tour_selection,
                    'name': tour_data['name'] if tour_data else f"Tour {tour_selection}",
                    'date': tour_data.get('date', '')[:10] if tour_data and tour_data.get('date') else '',
                    'sport': tour_data.get('sport', 'unknown') if tour_data else 'unknown',
                    'distance': tour_data.get('distance', 0) if tour_data else 0,
                    'distance_km': round(tour_data.get('distance', 0) / 1000, 2) if tour_data else 0,
                    'duration': round(tour_data.get('duration', 0) / 3600, 2) if tour_data else 0,
                    'elevation_up': tour_data.get('elevation_up', 0) if tour_data else 0,
                    'elevation_down': tour_data.get('elevation_down', 0) if tour_data else 0,
                    'url': f"https://www.komoot.com/tour/{tour_selection}",
                    'filename': filename,
                    'images': image_paths,
                    'output_dir': output_dir  # Include the output dir for user reference
                }
                
                with processing_lock:
                    processing_status['status'] = 'completed'
                    processing_status['progress'] = 1.0
                    processing_status['tours_completed'] = 1
                    processing_status['results'] = [result]
                
                add_log_entry(f"Tour {tour_selection} downloaded successfully to {output_dir}", processing_status)
                return jsonify({'success': True, 'message': 'Tour downloaded successfully'})
            
            add_log_entry(f"KomootGPX download failed, falling back to adapter", processing_status)
        
        # Start a background thread for processing using our adapter
        threading.Thread(
            target=process_tours,
            args=(anonymous, email, password, tour_selection, filter_type, 
                  no_poi, output_dir, skip_existing, id_filename, add_date,
                  max_title_length, max_desc_length, download_images,
                  chunk_size, chunk_start)
        ).start()
        
        return jsonify({'success': True, 'message': 'Processing started'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get the current processing status"""
    with processing_lock:
        return jsonify(processing_status)

@app.route('/api/results', methods=['GET'])
def get_results():
    """Get the processing results"""
    with processing_lock:
        if not processing_status['results']:
            return jsonify([]), 200  # Return empty array instead of error
        return jsonify(processing_status['results'])

@app.route('/api/clear', methods=['POST'])
def clear_results():
    """Clear the current results"""
    with processing_lock:
        processing_status['results'] = []
    return jsonify({'success': True})

@app.route('/api/download/<tour_id>', methods=['GET'])
def download_tour(tour_id):
    """Download a specific tour GPX file"""
    try:
        # Get parameters from query string
        anonymous = request.args.get('anonymous', 'false').lower() == 'true'
        email = request.args.get('email')
        password = request.args.get('password')
        no_poi = request.args.get('noPoi', 'false').lower() == 'true'
        
        # Create a temporary directory for the GPX
        output_dir = get_default_output_dir('temp')
        
        # First try using KomootGPX if available
        if KOMOOTGPX_AVAILABLE:
            filename = download_tour_using_komootgpx(
                tour_id=tour_id,
                email=email if not anonymous else None,
                password=password if not anonymous else None,
                output_dir=output_dir,
                include_poi=(not no_poi)
            )
            
            if filename:
                return send_from_directory(output_dir, filename, as_attachment=True)
        
        # If KomootGPX failed or not available, try direct GPX API for anonymous mode
        if anonymous:
            try:
                gpx_content = download_tour_using_gpx_api(tour_id)
                
                # Save to file
                filename = f"Tour_{tour_id}.gpx"
                filepath = os.path.join(output_dir, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(gpx_content)
                    
                return send_from_directory(output_dir, filename, as_attachment=True)
            except Exception as e:
                logger.error(f"Error downloading with direct API: {str(e)}")
        
        # Fall back to using our adapter
        adapter = KomootAdapter()
        
        # Login if not anonymous
        if not anonymous:
            if not email or not password:
                return jsonify({'error': 'Email and password are required for non-anonymous mode'}), 400
            adapter.login(email, password)
        
        # Generate the GPX
        adapter.make_gpx(
            tour_id=tour_id,
            output_dir=output_dir,
            include_poi=(not no_poi),
            skip_existing=False,
            tour_base=None,
            add_date=True,
            max_title_length=-1,
            max_desc_length=-1,
            return_content=False,
            anonymous=anonymous
        )
        
        # Get the filename from the adapter
        filename = adapter.get_last_filename()
        if not filename:
            return jsonify({'error': 'Failed to generate GPX file'}), 500
            
        # Return the file
        return send_from_directory(output_dir, filename, as_attachment=True)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/all', methods=['GET'])
def export_all():
    """Export all results as a ZIP file"""
    try:
        # Get the results
        with processing_lock:
            results = processing_status['results']
            
        if not results:
            return jsonify({'error': 'No results available'}), 404
            
        # Create a BytesIO object to store the zip file
        zip_buffer = BytesIO()
        
        # Create a ZIP file
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add each GPX file to the ZIP
            for result in results:
                if result.get('output_dir') and result.get('filename'):
                    gpx_file_path = os.path.join(result['output_dir'], result['filename'])
                    if os.path.exists(gpx_file_path):
                        arcname = result['filename'] 
                        zipf.write(gpx_file_path, arcname)
            
            # Add results JSON
            zipf.writestr('results.json', json.dumps(results, indent=2))
            
            # Add results CSV
            csv_data = []
            for tour in results:
                csv_tour = {}
                for key, value in tour.items():
                    if key != 'images':  # Skip images array
                        csv_tour[key] = value
                csv_data.append(csv_tour)
                
            if csv_data:
                csv_buffer = BytesIO()
                fieldnames = csv_data[0].keys()
                
                # Write CSV data
                writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_data)
                
                # Add CSV to zip
                zipf.writestr('results.csv', csv_buffer.getvalue().decode('utf-8'))
        
        # Reset file pointer to beginning
        zip_buffer.seek(0)
        
        # Set the filename with current date
        filename = f"komoot_gpx_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        # Return the ZIP file
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/images/<tour_id>', methods=['GET'])
def export_images(tour_id):
    """Export all images for a specific tour"""
    try:
        # Get base image directory
        base_dir = get_default_output_dir('images')
        
        # Check if images directory exists for this tour
        images_dir = os.path.join(base_dir, str(tour_id))
        if not os.path.exists(images_dir):
            return jsonify({'error': 'No images found for this tour'}), 404
            
        # Create a BytesIO object to store the zip file
        zip_buffer = BytesIO()
        
        # Create a ZIP file
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add each image file to the ZIP
            for root, _, files in os.walk(images_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.basename(file_path)
                    zipf.write(file_path, arcname)
        
        # Reset file pointer to beginning
        zip_buffer.seek(0)
        
        # Set the filename with tour ID
        filename = f"tour_{tour_id}_images.zip"
        
        # Return the ZIP file
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tour-counts', methods=['POST'])
def get_tour_counts():
    """Count the number of tours for a user"""
    try:
        data = request.json
        if data is None:
            return jsonify({'error': 'No JSON data received'}), 400
            
        email = data.get('email')
        password = data.get('password')
        filter_type = data.get('filterType', 'all')
        
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400
            
        logger.info(f"Counting tours for user {email}, filter: {filter_type}")
        
        # Login to Komoot
        logger.info("Logging in to Komoot...")
        adapter = KomootAdapter()
        adapter.login(email, password)
        logger.info("Logged in successfully, fetching tours...")
        
        # Fetch tours
        tours = adapter.fetch_tours(filter_type)
        logger.info(f"Found {len(tours)} tours")
        
        return jsonify({
            'success': True,
            'total_tours': len(tours)
        })
        
    except Exception as e:
        logger.error(f"Error counting tours: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/collections/personal', methods=['POST'])
def scrape_personal_collections():
    """Start scraping personal collections"""
    try:
        data = request.json
        if data is None:
            return jsonify({'error': 'No JSON data received'}), 400
            
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400
            
        logger.info(f"Starting personal collections scraping for user {email}")
        
        # Reset collections status
        with collections_lock:
            reset_status(collections_status)
        
        # Start a background thread for scraping
        threading.Thread(
            target=scrape_collections_thread,
            args=(email, password, 'personal')
        ).start()
        
        return jsonify({'success': True, 'message': 'Personal collections scraping started'})
        
    except Exception as e:
        logger.error(f"Error starting collection scraping: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/collections/saved', methods=['POST'])
def scrape_saved_collections():
    """Start scraping saved collections"""
    try:
        data = request.json
        if data is None:
            return jsonify({'error': 'No JSON data received'}), 400
            
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400
            
        logger.info(f"Starting saved collections scraping for user {email}")
        
        # Reset collections status
        with collections_lock:
            reset_status(collections_status)
        
        # Start a background thread for scraping
        threading.Thread(
            target=scrape_collections_thread,
            args=(email, password, 'saved')
        ).start()
        
        return jsonify({'success': True, 'message': 'Saved collections scraping started'})
        
    except Exception as e:
        logger.error(f"Error starting collection scraping: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/collections/public', methods=['POST'])
def scrape_public_collections():
    """Scrape public collections by URL without authentication"""
    try:
        # Get parameters from request
        data = request.json
        if data is None:
            logger.error("No JSON data received in request")
            return jsonify({'error': 'No JSON data received'}), 400
            
        collection_urls = data.get('urls', [])
        
        if not collection_urls:
            return jsonify({'error': 'No collection URLs provided'}), 400
            
        logger.info(f"Starting public collections scraping for {len(collection_urls)} URLs")
        
        # Reset collections status
        with collections_lock:
            reset_status(collections_status)
        
        # Start a background thread for scraping
        threading.Thread(
            target=scrape_public_collections_thread,
            args=(collection_urls,)
        ).start()
        
        return jsonify({'success': True, 'message': 'Public collections scraping started'})
        
    except Exception as e:
        logger.error(f"Error starting public collection scrape: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/collections-status', methods=['GET'])
def get_collections_status():
    """Get the current collections processing status"""
    with collections_lock:
        return jsonify(collections_status)

@app.route('/api/collections-results', methods=['GET'])
def get_collections_results():
    """Get the collections processing results"""
    with collections_lock:
        if not collections_status['results']:
            return jsonify([]), 200  # Return empty array instead of error
        return jsonify(collections_status['results'])

@app.route('/api/clear-collections', methods=['POST'])
def clear_collections():
    """Clear the current collections results"""
    with collections_lock:
        collections_status['results'] = []
    return jsonify({'success': True})

@app.route('/api/export/collections', methods=['GET'])
def export_collections():
    """Export all collection data as a ZIP file"""
    try:
        # Get the collections zip
        zip_buffer = collections_manager.get_collections_zip()
        
        if not zip_buffer:
            return jsonify({'error': 'No collections data available'}), 404
        
        # Set the filename with current date
        filename = f"komoot_collections_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        # Return the ZIP file
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download-collection-tours', methods=['POST'])
def download_collection_tours():
    """Download all tours in a collection as GPX files"""
    try:
        # Get parameters from request
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        collections = data.get('collections', [])
        if not collections:
            return jsonify({'error': 'No collections provided'}), 400
            
        # Use provided output directory or get default
        output_dir = data.get('outputDir')
        if not output_dir or output_dir.startswith('static/'):
            output_dir = get_default_output_dir('collections')
        
        include_metadata = data.get('includeMetadata', True)
        output_dir_structure = data.get('outputDirStructure', 'collection')
        download_images = data.get('downloadImages', False)
        
        # Get user ID from request if available
        user_id = data.get('userId')
        
        # GPX options
        gpx_options = data.get('gpxOptions', {})
        
        # Reset status
        with processing_lock:
            reset_status(processing_status)
            
        # Start background thread
        threading.Thread(
            target=download_collection_tours_thread,
            args=(collections, output_dir, include_metadata, output_dir_structure, download_images, gpx_options, user_id)
        ).start()
        
        return jsonify({'success': True, 'message': 'Collection tours download started'})
        
    except Exception as e:
        logger.error(f"Error starting collection tours download: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/collection/<collection_id>/csv', methods=['GET'])
def export_collection_csv(collection_id):
    """Export a specific collection as CSV"""
    try:
        # Find the collection in the results
        collection = None
        with collections_lock:
            for coll in collections_status['results']:
                if coll.get('id') == collection_id:
                    collection = coll
                    break
        
        if not collection:
            return jsonify({'error': 'Collection not found'}), 404
        
        # Create a timestamp for the filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create a name for the file based on collection name
        collection_name = collection.get('name', 'Unknown')
        safe_name = re.sub(r'[^\w\-]', '_', collection_name)
        filename = f"komoot_collection_{safe_name}_{timestamp}.csv"
        
        # Create a CSV with tour data
        # Define fields to include in the CSV - with bikepacking.com format in mind
        fieldnames = [
            "id", "timestamp", "name", "distance_km", "distance_mi", "duration",
            "unpaved_percentage", "singletrack_percentage", "rideable_percentage",
            "total_ascent", "total_descent", "high_point", "climbing_intensity",
            "country", "region", "collection_name", "collection_id", 
            "sport_type", "description", "url", "gpx_url", "image_url", 
            "collection_cover_image", "date_created"
        ]
        
        # Create a BytesIO object to store the CSV
        csv_buffer = BytesIO()
        
        # Get the timestamp for all records
        current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Process tours
        csv_tours = []
        for tour in collection.get('tours', []):
            # Create standardized tour object
            distance_km = tour.get('distance_km', tour.get('distance', 0)/1000 if tour.get('distance', 0) else 0)
            
            csv_tour = {
                "id": tour.get('id', ''),
                "timestamp": current_timestamp,
                "name": tour.get('name', f"Tour {tour.get('id', 'unknown')}"),
                "distance_km": f"{distance_km:.1f}" if distance_km else "",
                "distance_mi": f"{distance_km * 0.621371:.1f}" if distance_km else "",
                "duration": f"{tour.get('duration_hours', tour.get('duration', 0)/3600):.1f}" if tour.get('duration', 0) else "",
                "unpaved_percentage": tour.get('unpaved_percentage', ''),
                "singletrack_percentage": tour.get('singletrack_percentage', ''),
                "rideable_percentage": tour.get('rideable_percentage', ''),
                "total_ascent": tour.get('elevation_up', ''),
                "total_descent": tour.get('elevation_down', ''),
                "high_point": tour.get('high_point', ''),
                "country": tour.get('country', ''),
                "region": collection.get('region', ''),
                "collection_name": collection.get('name', ''),
                "collection_id": collection.get('id', ''),
                "sport_type": tour.get('sport', ''),
                "description": tour.get('description', ''),
                "url": tour.get('url', f"https://www.komoot.com/tour/{tour.get('id', '')}"),
                "gpx_url": tour.get('gpx_url', ''),
                "image_url": tour.get('image_url', ''),
                "collection_cover_image": collection.get('cover_image_url', ''),
                "date_created": tour.get('date', '')[:10] if tour.get('date') else ''
            }
            
            # Add calculated fields
            if tour.get('elevation_up') and distance_km:
                try:
                    # Calculate meters climbed per kilometer
                    meters_per_km = float(tour['elevation_up']) / float(distance_km)
                    csv_tour["climbing_intensity"] = f"{meters_per_km:.1f}"
                except:
                    csv_tour["climbing_intensity"] = ""
            else:
                csv_tour["climbing_intensity"] = ""
                    
            csv_tours.append(csv_tour)
        
        # Write the CSV file - only include fields that have data
        if csv_tours:
            # Only include fields that have data
            used_fields = set()
            for tour in csv_tours:
                for field, value in tour.items():
                    if value:
                        used_fields.add(field)
            
            # Filter fieldnames to only include fields that have data
            filtered_fieldnames = [f for f in fieldnames if f in used_fields or f in ('id', 'name', 'url')]
            
            with csv.DictWriter(csv_buffer, fieldnames=filtered_fieldnames, extrasaction='ignore') as writer:
                writer.writeheader()
                writer.writerows(csv_tours)
        
        # Reset file pointer to beginning
        csv_buffer.seek(0)
        
        # Return the CSV file
        return send_file(
            csv_buffer,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error exporting collection as CSV: {str(e)}")
        return jsonify({'error': str(e)}), 500

def process_tours(anonymous, email, password, tour_selection, filter_type, 
                 no_poi, output_dir, skip_existing, id_filename, add_date,
                 max_title_length, max_desc_length, download_images,
                 chunk_size, chunk_start):
    """Process tours in a background thread"""
    try:
        with processing_lock:
            processing_status['status'] = 'running'
            
        # Create a Komoot adapter
        adapter = KomootAdapter()
        
        # Login to Komoot if not anonymous
        if not anonymous:
            add_log_entry(f"Logging in to Komoot as {email}...", processing_status)
            adapter.login(email, password)
            add_log_entry(f"Logged in as {adapter.get_display_name()}", processing_status)
            
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        add_log_entry(f"Downloading to directory: {output_dir}", processing_status)
        
        # Process based on tour selection
        if tour_selection == 'all':
            # Fetch all tours
            add_log_entry(f"Fetching all {filter_type} tours...", processing_status)
            tours = adapter.fetch_tours(filter_type)
            
            # Update status with tour count
            with processing_lock:
                processing_status['tours_found'] = len(tours)
                
            add_log_entry(f"Found {len(tours)} tours", processing_status)
            
            # If chunking is enabled, limit the tours
            if chunk_size > 0:
                # Calculate the end index
                start_idx = chunk_start
                end_idx = min(start_idx + chunk_size, len(tours))
                
                # Get the subset of tours
                tour_ids = list(tours.keys())[start_idx:end_idx]
                
                add_log_entry(f"Processing chunk {start_idx+1}-{end_idx} of {len(tours)} tours", processing_status)
                
                # Keep track of next chunk start
                with processing_lock:
                    processing_status['next_chunk'] = end_idx
            else:
                # Process all tours
                tour_ids = list(tours.keys())
            
            # Determine optimal number of workers based on tour count
            max_workers = min(8, len(tour_ids) // 20 + 3)
            
            # Process multiple tours concurrently for better speed
            results = []
            completed_count = 0
            
            # Create a thread pool executor for processing tours
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Function to process a single tour
                def process_single_tour(tour_id):
                    try:
                        tour_data = tours[tour_id]
                        
                        # Try using KomootGPX first
                        filename = None
                        if KOMOOTGPX_AVAILABLE:
                            filename = download_tour_using_komootgpx(
                                tour_id=tour_id,
                                email=email,
                                password=password,
                                output_dir=output_dir,
                                include_poi=(not no_poi),
                                max_desc_length=max_desc_length,
                                max_title_length=max_title_length,
                                add_date=add_date
                            )
                        
                        # Fall back to adapter if KomootGPX failed
                        if not filename:
                            adapter.make_gpx(
                                tour_id=tour_id,
                                output_dir=output_dir,
                                include_poi=(not no_poi),
                                skip_existing=skip_existing,
                                tour_base=tour_data,
                                add_date=add_date,
                                max_title_length=max_title_length,
                                max_desc_length=max_desc_length,
                                return_content=False,
                                anonymous=anonymous
                            )
                            filename = adapter.get_last_filename()
                        
                        # Download images if requested
                        image_paths = []
                        if download_images:
                            try:
                                images_dir = os.path.join(output_dir, '../images')
                                os.makedirs(images_dir, exist_ok=True)
                                image_paths = adapter.download_tour_images(
                                    tour_id=tour_id,
                                    tour=tour_data,
                                    output_dir=images_dir
                                )
                            except Exception as img_err:
                                add_log_entry(f"Error downloading images for tour {tour_id}: {str(img_err)}", processing_status)
                        
                        # Create result
                        result = {
                            'id': tour_id,
                            'name': tour_data['name'],
                            'date': tour_data.get('date', '')[:10] if tour_data.get('date') else '',
                            'sport': tour_data.get('sport', 'unknown'),
                            'distance': tour_data.get('distance', 0),
                            'distance_km': round(tour_data.get('distance', 0) / 1000, 2),
                            'duration': round(tour_data.get('duration', 0) / 3600, 2),
                            'elevation_up': tour_data.get('elevation_up', 0),
                            'elevation_down': tour_data.get('elevation_down', 0),
                            'url': f"https://www.komoot.com/tour/{tour_id}",
                            'filename': filename,
                            'images': image_paths,
                            'output_dir': output_dir
                        }
                        
                        return result
                    except Exception as e:
                        add_log_entry(f"Error processing tour {tour_id}: {str(e)}", processing_status)
                        return None
                
                # Submit all tours to the thread pool
                future_to_tour = {executor.submit(process_single_tour, tour_id): tour_id for tour_id in tour_ids}
                
                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_tour):
                    tour_id = future_to_tour[future]
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                            completed_count += 1
                            
                            # Update progress
                            with processing_lock:
                                processing_status['progress'] = completed_count / len(tour_ids)
                                processing_status['tours_completed'] = completed_count
                                # Update results as we go
                                processing_status['results'] = results.copy()
                                
                            add_log_entry(f"Completed tour {completed_count}/{len(tour_ids)}: {result['name']}", processing_status)
                    except Exception as e:
                        add_log_entry(f"Error processing tour {tour_id}: {str(e)}", processing_status)
            
            # Update status with final results
            with processing_lock:
                processing_status['results'] = results
                
                # Update final status
                if chunk_size > 0 and end_idx < len(tours):
                    processing_status['status'] = 'chunk_completed'
                else:
                    processing_status['status'] = 'completed'
                    
                processing_status['progress'] = 1.0
            
            add_log_entry(f"Processing completed. Processed {len(results)} tours.", processing_status)
            
        else:
            # Process single tour
            tour_id = tour_selection
            
            add_log_entry(f"Processing single tour: {tour_id}", processing_status)
            
            # Update status
            with processing_lock:
                processing_status['tours_found'] = 1
                processing_status['tours_completed'] = 0
                processing_status['progress'] = 0.0
            
            try:
                # Try using KomootGPX first
                filename = None
                if KOMOOTGPX_AVAILABLE:
                    add_log_entry(f"Using KomootGPX to download tour {tour_id}", processing_status)
                    filename = download_tour_using_komootgpx(
                        tour_id=tour_id,
                        email=email if not anonymous else None,
                        password=password if not anonymous else None,
                        output_dir=output_dir,
                        include_poi=(not no_poi),
                        max_desc_length=max_desc_length,
                        max_title_length=max_title_length,
                        add_date=add_date
                    )
                
                # Fall back to adapter if KomootGPX failed
                if not filename:
                    add_log_entry(f"Using adapter to download tour {tour_id}", processing_status)
                    
                    # Try direct GPX API if anonymous
                    if anonymous:
                        try:
                            add_log_entry(f"Trying direct GPX API for tour {tour_id}", processing_status)
                            gpx_content = download_tour_using_gpx_api(tour_id)
                            
                            # Save to file
                            filename = f"Tour_{tour_id}.gpx"
                            filepath = os.path.join(output_dir, filename)
                            
                            with open(filepath, 'wb') as f:
                                f.write(gpx_content)
                                
                            add_log_entry(f"Successfully downloaded tour {tour_id} with direct API", processing_status)
                        except Exception as api_err:
                            add_log_entry(f"Direct API download failed: {str(api_err)}", processing_status)
                            
                            # Fall back to adapter
                            adapter.make_gpx(
                                tour_id=tour_id,
                                output_dir=output_dir,
                                include_poi=(not no_poi),
                                skip_existing=skip_existing,
                                tour_base=None,
                                add_date=add_date,
                                max_title_length=max_title_length,
                                max_desc_length=max_desc_length,
                                return_content=False,
                                anonymous=anonymous
                            )
                            filename = adapter.get_last_filename()
                    else:
                        # Use adapter for authenticated mode
                        adapter.make_gpx(
                            tour_id=tour_id,
                            output_dir=output_dir,
                            include_poi=(not no_poi),
                            skip_existing=skip_existing,
                            tour_base=None,
                            add_date=add_date,
                            max_title_length=max_title_length,
                            max_desc_length=max_desc_length,
                            return_content=False,
                            anonymous=anonymous
                        )
                        filename = adapter.get_last_filename()
                
                # Get the tour data
                tour_data = adapter.get_last_tour()
                
                # Download images if requested
                image_paths = []
                if download_images:
                    try:
                        images_dir = os.path.join(output_dir, '../images')
                        os.makedirs(images_dir, exist_ok=True)
                        image_paths = adapter.download_tour_images(
                            tour_id=tour_id,
                            tour=tour_data,
                            output_dir=images_dir
                        )
                        add_log_entry(f"Downloaded {len(image_paths)} images for tour {tour_id}", processing_status)
                    except Exception as img_err:
                        add_log_entry(f"Error downloading images for tour {tour_id}: {str(img_err)}", processing_status)
                
                # Add to results
                result = {
                    'id': tour_id,
                    'name': tour_data['name'] if tour_data else f"Tour {tour_id}",
                    'date': tour_data.get('date', '')[:10] if tour_data and tour_data.get('date') else '',
                    'sport': tour_data.get('sport', 'unknown') if tour_data else 'unknown',
                    'distance': tour_data.get('distance', 0) if tour_data else 0,
                    'distance_km': round(tour_data.get('distance', 0) / 1000, 2) if tour_data else 0,
                    'duration': round(tour_data.get('duration', 0) / 3600, 2) if tour_data else 0,
                    'elevation_up': tour_data.get('elevation_up', 0) if tour_data else 0,
                    'elevation_down': tour_data.get('elevation_down', 0) if tour_data else 0,
                    'url': f"https://www.komoot.com/tour/{tour_id}",
                    'filename': filename,
                    'images': image_paths,
                    'output_dir': output_dir
                }
                
                # Update status with results
                with processing_lock:
                    processing_status['results'] = [result]
                    processing_status['status'] = 'completed'
                    processing_status['progress'] = 1.0
                    processing_status['tours_completed'] = 1
                    
                add_log_entry(f"Processing completed for tour {tour_id}", processing_status)
                
            except Exception as e:
                add_log_entry(f"Error processing tour {tour_id}: {str(e)}", processing_status)
                
                with processing_lock:
                    processing_status['status'] = 'error'
                    processing_status['error'] = str(e)
        
    except Exception as e:
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in processing thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", processing_status)
        
        with processing_lock:
            processing_status['status'] = 'error'
            processing_status['error'] = error_msg

def scrape_collections_thread(email, password, collection_type):
    """Scrape collections in a background thread"""
    try:
        with collections_lock:
            collections_status['status'] = 'running'
            
        logger.info(f"Starting Komoot collections scraper")
        
        # Login to Komoot
        add_log_entry(f"Logging in as {email}... (type: {collection_type})", collections_status)
        adapter = KomootAdapter()
        adapter.login(email, password)
        add_log_entry(f"Logged in successfully as {adapter.get_display_name()}", collections_status)
        
        # Get user ID for folder organization
        user_id = adapter.get_user_id()
        user_name = adapter.get_display_name()
        
        # Fetch collections
        add_log_entry(f"Fetching collections...", collections_status)
        basic_collections = adapter.fetch_collections(collection_type)
        add_log_entry(f"Found {len(basic_collections)} collections", collections_status)
        
        # Enhance collections with complete tour data using concurrency
        enhanced_collections = []
        
        # Determine optimal number of workers based on collection count
        max_workers = min(5, len(basic_collections))
        
        # Process collections concurrently for better speed
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Function to enhance a single collection
            def enhance_collection(collection):
                try:
                    if 'url' not in collection:
                        add_log_entry(f"Skipping collection without URL: {collection.get('name', 'Unnamed')}", collections_status)
                        return None
                    
                    add_log_entry(f"Fetching complete data for collection: {collection['name']}", collections_status)
                    
                    # Use our enhanced function that handles pagination with concurrency
                    full_collection = fetch_all_tours_from_collection(adapter, collection['url'], collections_status)
                    
                    if full_collection:
                        add_log_entry(f"Successfully fetched all {len(full_collection['tours'])} tours for collection: {collection['name']}", collections_status)
                        return full_collection
                    else:
                        # Fall back to the original collection if enhancement failed
                        add_log_entry(f"Using basic collection data for: {collection['name']}", collections_status)
                        return collection
                except Exception as e:
                    add_log_entry(f"Error fetching details for collection {collection.get('name', 'Unknown')}: {str(e)}", collections_status)
                    return collection
            
            # Submit all collections to the thread pool
            future_to_collection = {executor.submit(enhance_collection, collection): collection for collection in basic_collections}
            
            # Process results as they complete
            for i, future in enumerate(concurrent.futures.as_completed(future_to_collection)):
                try:
                    enhanced_collection = future.result()
                    if enhanced_collection:
                        enhanced_collections.append(enhanced_collection)
                    
                    # Update progress
                    with collections_lock:
                        collections_status['collections_completed'] = i + 1
                        collections_status['collections_found'] = len(basic_collections)
                        collections_status['progress'] = (i + 1) / len(basic_collections)
                        
                except Exception as e:
                    add_log_entry(f"Error processing collection: {str(e)}", collections_status)
        
        # Update status with results
        with collections_lock:
            collections_status['results'] = enhanced_collections
            collections_status['status'] = 'completed'
            collections_status['progress'] = 1.0
            collections_status['collections_found'] = len(basic_collections)
            collections_status['collections_completed'] = len(enhanced_collections)
            
        add_log_entry(f"Collections scraping completed successfully", collections_status)
        
        # Save collections data to files with user_id
        collections_manager.save_collections_data(enhanced_collections, user_id)
        
    except Exception as e:
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in collections thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", collections_status)
        
        with collections_lock:
            collections_status['status'] = 'error'
            collections_status['error'] = error_msg

def scrape_public_collections_thread(collection_urls):
    """Scrapes public collections by URL in background thread with improved performance"""
    try:
        with collections_lock:
            collections_status['status'] = 'running'
            collections_status['collections_found'] = len(collection_urls)
        
        # Log start
        add_log_entry(f"Starting public collections scraper for {len(collection_urls)} URLs", collections_status)
        
        # Create a new adapter instance specifically for anonymous access
        anonymous_adapter = KomootAdapter()
        
        # Process collections from user collection pages and direct collection links
        all_collections = []
        processed_urls = 0
        user_id = None
        
        # Extract user ID from the first URL - if available
        for url in collection_urls:
            extracted_user_id = extract_user_id_from_url(url)
            if extracted_user_id:
                user_id = extracted_user_id
                add_log_entry(f"Found user ID in URL: {user_id}", collections_status)
                break
        
        # Determine optimal number of workers based on URL count
        max_workers = min(5, len(collection_urls))
        
        # Process URLs concurrently for better speed
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Function to process a single URL
            def process_url(url):
                try:
                    url = url.strip()
                    if not url:
                        return None
                        
                    add_log_entry(f"Processing URL: {url}", collections_status)
                    
                    # Extract user ID from the URL if available
                    extracted_user_id = extract_user_id_from_url(url)
                    
                    # Check if it's a user collections page or a direct collection link
                    if '/user/' in url and '/collections/' in url:
                        # This is a user collections page
                        add_log_entry(f"Detected user collections page: {url}", collections_status)
                        
                        # Extract user ID and collection type from URL
                        parts = url.split('/')
                        user_idx = parts.index('user') if 'user' in parts else -1
                        coll_idx = parts.index('collections') if 'collections' in parts else -1
                        
                        if user_idx >= 0 and user_idx + 1 < len(parts) and coll_idx >= 0 and coll_idx + 1 < len(parts):
                            page_user_id = parts[user_idx + 1]
                            coll_type = parts[coll_idx + 1]
                            
                            add_log_entry(f"Attempting to scrape collections for user {page_user_id} of type {coll_type}", collections_status)
                            
                            try:
                                # Set browser-like headers
                                headers = {
                                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                                    'Accept-Language': 'en-US,en;q=0.5',
                                    'Referer': 'https://www.komoot.com/',
                                    'DNT': '1'
                                }
                                
                                response = make_request_with_retry(url, headers, max_retries=3)
                                
                                if response.status_code == 200:
                                    # Extract collections from the page
                                    if BS4_AVAILABLE:
                                        page_collections = anonymous_adapter.extract_collections_from_page(response.text, coll_type)
                                        add_log_entry(f"Found {len(page_collections)} collections on page", collections_status)
                                        
                                        # Process each collection to get full details
                                        detailed_collections = []
                                        for collection in page_collections:
                                            try:
                                                if 'url' in collection:
                                                    # Use our enhanced function to get all tours
                                                    detailed = fetch_all_tours_from_collection(anonymous_adapter, collection['url'], collections_status)
                                                    if detailed:
                                                        detailed_collections.append(detailed)
                                                    else:
                                                        detailed_collections.append(collection)
                                            except Exception as e:
                                                add_log_entry(f"Error fetching details for collection {collection.get('name', 'Unknown')}: {str(e)}", collections_status)
                                                detailed_collections.append(collection)
                                                
                                        return {
                                            'user_id': page_user_id,
                                            'collections': detailed_collections
                                        }
                                    else:
                                        add_log_entry("BeautifulSoup4 is not installed. Cannot parse HTML.", collections_status)
                                else:
                                    add_log_entry(f"Failed to access URL: {url}, status code: {response.status_code}", collections_status)
                                    
                            except Exception as e:
                                add_log_entry(f"Error processing collections page: {str(e)}", collections_status)
                                
                    elif '/collection/' in url:
                        # This is a direct collection link
                        try:
                            add_log_entry(f"Fetching direct collection: {url} (may take time for large collections)", collections_status)
                            
                            # Use our enhanced function for fetching all tours in a collection
                            collection = fetch_all_tours_from_collection(anonymous_adapter, url, collections_status)
                            
                            if collection:
                                # Extract user ID from collection's creator if available
                                collection_user_id = None
                                if 'creator' in collection and 'id' in collection['creator']:
                                    collection_user_id = collection['creator']['id']
                                
                                return {
                                    'user_id': collection_user_id or extracted_user_id,
                                    'collections': [collection]
                                }
                            else:
                                add_log_entry(f"Failed to get collection data from URL: {url}", collections_status)
                                return {'user_id': extracted_user_id, 'collections': []}
                                
                        except Exception as e:
                            add_log_entry(f"Error fetching direct collection: {str(e)}", collections_status)
                            return {'user_id': extracted_user_id, 'collections': []}
                    else:
                        add_log_entry(f"Unrecognized URL format: {url}", collections_status)
                        return {'user_id': extracted_user_id, 'collections': []}
                    
                    return {'user_id': extracted_user_id, 'collections': []}
                    
                except Exception as e:
                    add_log_entry(f"Error processing URL {url}: {str(e)}", collections_status)
                    return {'user_id': None, 'collections': []}
            
            # Submit all URLs to the thread pool
            future_to_url = {executor.submit(process_url, url): url for url in collection_urls}
            
            # Process results as they complete
            for i, future in enumerate(concurrent.futures.as_completed(future_to_url)):
                try:
                    result = future.result()
                    if result:
                        # Extract collections from result
                        if result['collections']:
                            all_collections.extend(result['collections'])
                        
                        # Update user ID if we found one
                        if not user_id and result['user_id']:
                            user_id = result['user_id']
                    
                    # Update progress
                    processed_urls += 1
                    with collections_lock:
                        collections_status['progress'] = processed_urls / len(collection_urls)
                        collections_status['collections_completed'] = processed_urls
                        
                except Exception as e:
                    add_log_entry(f"Error processing URL result: {str(e)}", collections_status)
        
        # Save the results
        with collections_lock:
            collections_status['results'] = all_collections
            collections_status['status'] = 'completed'
            collections_status['progress'] = 1.0
            collections_status['collections_completed'] = processed_urls
            collections_status['collections_found'] = len(all_collections)
        
        add_log_entry(f"Public collections scraping completed. Found {len(all_collections)} collections.", collections_status)
        
        # Save collections data to files if there are results, with user_id
        if all_collections:
            collections_manager.save_collections_data(all_collections, user_id)
        
    except Exception as e:
        # Log any errors
        error_msg = str(e)
        logger.error(f"Error during public collections scraping: {error_msg}")
        add_log_entry(f"Error: {error_msg}", collections_status)
        
        # Update status to error
        with collections_lock:
            collections_status['status'] = 'error'
            collections_status['error'] = error_msg

def download_collection_tours_thread(collections, output_dir, include_metadata, output_dir_structure, download_images, gpx_options, user_id=None):
    """Background thread to download all tours in a collection or multiple collections with improved performance"""
    try:
        with processing_lock:
            processing_status['status'] = 'running'
        
        # Create base output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Process collections to remove duplicate tours
        for collection in collections:
            if 'tours' in collection and collection['tours']:
                # Store unique tours by ID, using the better name version
                unique_tours = {}
                for tour in collection['tours']:
                    tour_id = tour['id']
                    if tour_id in unique_tours:
                        # If this tour has a better name than the existing one, use it
                        if unique_tours[tour_id]['name'].startswith('Tour ') and not tour['name'].startswith('Tour '):
                            unique_tours[tour_id] = tour
                    else:
                        unique_tours[tour_id] = tour
                
                # Replace the tours array with deduplicated tours
                collection['tours'] = list(unique_tours.values())
                collection['tours_count'] = len(collection['tours'])
        
        # Count total tours across all collections
        total_tours = sum(len(collection.get('tours', [])) for collection in collections)
        total_collections = len(collections)
        
        with processing_lock:
            processing_status['tours_found'] = total_tours
        
        add_log_entry(f"Starting download of {total_tours} tours from {total_collections} collections", processing_status)
        
        # Create a Komoot adapter for anonymous download
        adapter = KomootAdapter()
        
        # Track global progress
        completed_tours = 0
        completed_collections = 0
        all_results = []
        
        # Determine optimal number of workers based on collection count
        collection_workers = min(3, total_collections)
        
        # Process collections concurrently using a ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=collection_workers) as executor_collections:
            
            # Function to process a single collection
            def process_collection(collection_idx, collection):
                try:
                    collection_id = collection.get('id')
                    collection_name = collection.get('name', f"Collection_{collection_id}")
                    
                    # Use the common user ID if provided, otherwise extract from collection
                    current_user_id = user_id
                    user_name = None
                    
                    # If no common user ID was provided, try to extract from this collection
                    if not current_user_id:
                        if 'creator' in collection:
                            if 'id' in collection['creator']:
                                current_user_id = collection['creator']['id']
                                add_log_entry(f"Using creator ID from collection: {current_user_id}", processing_status)
                            if 'display_name' in collection['creator']:
                                user_name = collection['creator']['display_name']
                        
                        # If still no user ID, try to extract from URL
                        if not current_user_id and collection.get('url'):
                            extracted_user_id = extract_user_id_from_url(collection.get('url'))
                            if extracted_user_id:
                                current_user_id = extracted_user_id
                                add_log_entry(f"Extracted user ID from URL: {current_user_id}", processing_status)
                    
                    # Create slug for collection folder
                    collection_slug = get_collection_slug(collection.get('url', ''), collection_name, max_slug_length=50)
                    
                    # Create user folder
                    user_dir = os.path.join(output_dir, f"user-{current_user_id}")
                    os.makedirs(user_dir, exist_ok=True)
                    
                    # Create user's index.html
                    index_html_content = create_user_index_html(current_user_id, user_name)
                    with open(os.path.join(user_dir, 'index.html'), 'w', encoding='utf-8') as f:
                        f.write(index_html_content)
                    
                    # Create collections folder for this user
                    collections_dir = os.path.join(user_dir, 'collections')
                    os.makedirs(collections_dir, exist_ok=True)
                    
                    # Create folder for this specific collection
                    collection_dir = os.path.join(collections_dir, collection_slug)
                    os.makedirs(collection_dir, exist_ok=True)
                    
                    add_log_entry(f"Processing collection {collection_idx+1}/{total_collections}: {collection_name}", processing_status)
                    add_log_entry(f"Collection output directory: {collection_dir}", processing_status)
                    
                    # Save collection metadata if requested
                    if include_metadata:
                        try:
                            # Save JSON metadata
                            with open(os.path.join(collection_dir, 'collection_info.json'), 'w', encoding='utf-8') as f:
                                json.dump(collection, f, indent=2, ensure_ascii=False)
                                
                            # Save human-readable metadata
                            with open(os.path.join(collection_dir, 'collection_info.txt'), 'w', encoding='utf-8') as f:
                                f.write(f"Collection: {collection_name}\n")
                                f.write(f"ID: {collection_id}\n")
                                f.write(f"Type: {collection.get('type', 'Unknown')}\n")
                                f.write(f"Privacy: {collection.get('privacy', 'Unknown')}\n")
                                f.write(f"Tours: {len(collection.get('tours', []))}\n\n")
                                
                                if collection.get('description'):
                                    f.write(f"Description:\n{collection['description']}\n\n")
                                    
                                # Add statistics if available
                                if collection.get('statistics'):
                                    f.write("Statistics:\n")
                                    for key, value in collection['statistics'].items():
                                        f.write(f"- {key}: {value}\n")
                                        
                            add_log_entry(f"Saved collection metadata to {collection_dir}", processing_status)
                        except Exception as e:
                            add_log_entry(f"Error saving collection metadata: {str(e)}", processing_status)
                    
                    # Get the tours from the collection
                    tours = collection.get('tours', [])
                    
                    # Track collection progress
                    collection_completed = 0
                    collection_results = []
                    
                    # Determine optimal number of workers based on tour count
                    tour_workers = min(5, len(tours))
                    
                    # Process tours in a collection concurrently
                    with concurrent.futures.ThreadPoolExecutor(max_workers=tour_workers) as executor_tours:
                        
                        # Function to process a single tour
                        def process_tour(tour_idx, tour):
                            try:
                                tour_id = tour.get('id')
                                tour_name = tour.get('name', f"Tour_{tour_id}")
                                
                                # Get GPX option values
                                no_poi = gpx_options.get('noPoi', False)
                                skip_existing = gpx_options.get('skipExisting', True)
                                add_date = gpx_options.get('addDate', True)
                                max_title_length = gpx_options.get('maxTitleLength', -1)
                                max_desc_length = gpx_options.get('maxDescLength', -1)
                                
                                # Try using KomootGPX first if available
                                filename = None
                                if KOMOOTGPX_AVAILABLE:
                                    filename = download_tour_using_komootgpx(
                                        tour_id=tour_id,
                                        output_dir=collection_dir,
                                        include_poi=(not no_poi),
                                        max_desc_length=max_desc_length,
                                        max_title_length=max_title_length,
                                        add_date=add_date
                                    )
                                
                                # Fall back to direct API if KomootGPX failed
                                if not filename:
                                    try:
                                        gpx_content = download_tour_using_gpx_api(tour_id)
                                        
                                        # Generate filename
                                        if add_date and 'date' in tour and tour['date']:
                                            date_str = tour['date'][:10] + '_'
                                        else:
                                            date_str = ''
                                            
                                        title = sanitize_filename(tour_name)
                                        if max_title_length == 0:
                                            title = f"{tour_id}"
                                        elif max_title_length > 0 and len(title) > max_title_length:
                                            title = f"{title[:max_title_length]}-{tour_id}"
                                        else:
                                            title = f"{title}-{tour_id}"
                                            
                                        filename = f"{date_str}{title}.gpx"
                                        filepath = os.path.join(collection_dir, filename)
                                        
                                        # Save to file
                                        with open(filepath, 'wb') as f:
                                            f.write(gpx_content)
                                    except Exception as api_err:
                                        # Fall back to adapter
                                        adapter.make_gpx(
                                            tour_id=tour_id,
                                            output_dir=collection_dir,
                                            include_poi=(not no_poi),
                                            skip_existing=skip_existing,
                                            tour_base=None,
                                            add_date=add_date,
                                            max_title_length=max_title_length,
                                            max_desc_length=max_desc_length,
                                            return_content=False,
                                            anonymous=True
                                        )
                                        filename = adapter.get_last_filename()
                                
                                # Get the tour data
                                tour_data = adapter.get_last_tour() or tour
                                
                                # Download images if requested
                                image_paths = []
                                if download_images:
                                    try:
                                        # Create an images subdirectory within the collection directory
                                        images_dir = os.path.join(collection_dir, 'images')
                                        image_paths = adapter.download_tour_images(
                                            tour_id=tour_id,
                                            tour=tour_data,
                                            output_dir=images_dir
                                        )
                                    except Exception:
                                        pass
                                
                                # Create a result object
                                result = {
                                    'id': tour_id,
                                    'name': tour_name,
                                    'collection_id': collection_id,
                                    'collection_name': collection_name,
                                    'url': tour.get('url'),
                                    'filename': filename,
                                    'output_dir': collection_dir,
                                    'images': image_paths
                                }
                                
                                # Add additional fields from tour_data if available
                                if isinstance(tour_data, dict):
                                    for key in ['date', 'sport', 'distance', 'distance_km', 'duration', 'elevation_up', 'elevation_down']:
                                        if key in tour_data:
                                            result[key] = tour_data[key]
                                            
                                    # Format fields with proper types
                                    if 'date' in result and result['date']:
                                        result['date'] = result['date'][:10]  # Just the date part
                                    if 'distance' in tour_data:
                                        result['distance_km'] = round(tour_data['distance'] / 1000, 2)
                                    if 'duration' in tour_data:
                                        result['duration'] = round(tour_data['duration'] / 3600, 2)
                                
                                return result
                                
                            except Exception as e:
                                add_log_entry(f"Error processing tour {tour_id}: {str(e)}", processing_status)
                                return None
                        
                        # Submit all tours to the thread pool
                        tour_futures = {}
                        for tour_idx, tour in enumerate(tours):
                            tour_futures[executor_tours.submit(process_tour, tour_idx, tour)] = tour
                        
                        # Process results as they complete
                        for future in concurrent.futures.as_completed(tour_futures):
                            result = future.result()
                            if result:
                                collection_results.append(result)
                                nonlocal completed_tours
                                completed_tours += 1
                                
                                # Update progress
                                with processing_lock:
                                    processing_status['progress'] = completed_tours / total_tours
                                    processing_status['tours_completed'] = completed_tours
                    
                    # Create a summary file for this collection
                    try:
                        with open(os.path.join(collection_dir, 'download_summary.json'), 'w', encoding='utf-8') as f:
                            summary = {
                                'collection': {
                                    'id': collection_id,
                                    'name': collection_name,
                                    'total_tours': len(tours)
                                },
                                'downloaded_tours': len(collection_results),
                                'tours': collection_results
                            }
                            json.dump(summary, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        add_log_entry(f"Error saving collection summary: {str(e)}", processing_status)
                    
                    # Create CSV summary with bikepacking.com-like format
                    try:
                        csv_path = os.path.join(collection_dir, f"{collection_slug}_tours.csv")
                        
                        # Define comprehensive fieldnames
                        fieldnames = [
                            "id", "timestamp", "name", "distance_km", "distance_mi", 
                            "duration", "unpaved_percentage", "singletrack_percentage", "rideable_percentage",
                            "total_ascent", "total_descent", "high_point", "climbing_intensity",
                            "country", "region", "collection_name", "collection_id", 
                            "sport_type", "description", "url", "gpx_url", "image_url", 
                            "collection_cover_image", "date_created"
                        ]
                        
                        # Create CSV tours data
                        csv_tours = []
                        
                        # Current timestamp for all entries
                        current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        for result in collection_results:
                            # Create standardized tour object
                            distance_km = result.get('distance_km', 0)
                            
                            csv_tour = {
                                "id": result.get('id', ''),
                                "timestamp": current_timestamp,
                                "name": result.get('name', ''),
                                "distance_km": f"{distance_km:.1f}" if distance_km else "",
                                "distance_mi": f"{distance_km * 0.621371:.1f}" if distance_km else "",
                                "duration": f"{result.get('duration', 0):.1f}" if result.get('duration') else "",
                                "unpaved_percentage": "",  # Not readily available
                                "singletrack_percentage": "",  # Not readily available
                                "rideable_percentage": "",  # Not readily available
                                "total_ascent": result.get('elevation_up', ''),
                                "total_descent": result.get('elevation_down', ''),
                                "high_point": "",  # Not readily available
                                "country": "",  # Not readily available
                                "region": "",  # Not readily available
                                "collection_name": collection_name,
                                "collection_id": collection_id,
                                "sport_type": result.get('sport', ''),
                                "description": "",  # Not readily available in this context
                                "url": result.get('url', ''),
                                "gpx_url": "",  # Generated locally
                                "image_url": result.get('images', [''])[0] if result.get('images') else '',
                                "collection_cover_image": collection.get('cover_image_url', ''),
                                "date_created": result.get('date', '')
                            }
                            
                            # Add calculated fields
                            if result.get('elevation_up') and distance_km:
                                try:
                                    # Calculate meters climbed per kilometer
                                    meters_per_km = float(result['elevation_up']) / float(distance_km)
                                    csv_tour["climbing_intensity"] = f"{meters_per_km:.1f}"
                                except:
                                    csv_tour["climbing_intensity"] = ""
                            else:
                                csv_tour["climbing_intensity"] = ""
                                
                            csv_tours.append(csv_tour)
                        
                        # Only include fields that have data
                        used_fields = set()
                        for tour in csv_tours:
                            for field, value in tour.items():
                                if value:
                                    used_fields.add(field)
                        
                        # Always include essential fields
                        essential_fields = ["id", "name", "url", "timestamp"]
                        for field in essential_fields:
                            used_fields.add(field)
                        
                        # Filter fieldnames to only include fields that have data
                        filtered_fieldnames = [f for f in fieldnames if f in used_fields]
                        
                        with open(csv_path, 'w', encoding='utf-8', newline='') as csvfile:
                            writer = csv.DictWriter(csvfile, fieldnames=filtered_fieldnames, extrasaction='ignore')
                            writer.writeheader()
                            writer.writerows(csv_tours)
                        
                        add_log_entry(f"Created CSV summary at {csv_path}", processing_status)
                    except Exception as e:
                        add_log_entry(f"Error creating CSV summary: {str(e)}", processing_status)
                    
                    return {
                        'collection_id': collection_id,
                        'collection_name': collection_name,
                        'results': collection_results
                    }
                    
                except Exception as e:
                    add_log_entry(f"Error processing collection {collection.get('name', 'Unknown')}: {str(e)}", processing_status)
                    return {
                        'collection_id': collection.get('id'),
                        'collection_name': collection.get('name', 'Unknown'),
                        'results': []
                    }
            
            # Submit all collections to the thread pool
            collection_futures = {}
            for collection_idx, collection in enumerate(collections):
                collection_futures[executor_collections.submit(process_collection, collection_idx, collection)] = collection
            
            # Process results as they complete
            for future in concurrent.futures.as_completed(collection_futures):
                try:
                    collection_result = future.result()
                    if collection_result:
                        all_results.extend(collection_result.get('results', []))
                        
                        completed_collections += 1
                        add_log_entry(f"Completed collection: {collection_result['collection_name']} with {len(collection_result['results'])} tours", processing_status)
                        
                except Exception as e:
                    add_log_entry(f"Error processing collection result: {str(e)}", processing_status)
        
        # Update status with final results
        with processing_lock:
            processing_status['results'] = all_results
            processing_status['status'] = 'completed'
            processing_status['progress'] = 1.0
            processing_status['tours_completed'] = completed_tours
        
        add_log_entry(f"Download completed. Downloaded {completed_tours} tours from {completed_collections} collections.", processing_status)
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in collection tours download thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", processing_status)
        
        with processing_lock:
            processing_status['status'] = 'error'
            processing_status['error'] = error_msg

if __name__ == '__main__':
    # Create directories if they don't exist
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(templates_dir, exist_ok=True)
    
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    os.makedirs(static_dir, exist_ok=True)
    
    exports_dir = os.path.join(static_dir, 'exports')
    os.makedirs(exports_dir, exist_ok=True)
    
    # Set default download directory in user's home folder
    default_download_dir = os.path.join(str(Path.home()), "komoot-takeout")
    os.makedirs(default_download_dir, exist_ok=True)
    set_selected_folder(default_download_dir)
    
    # Save the HTML to the templates directory if it exists in the current dir
    if os.path.exists('index.html'):
        with open(os.path.join(templates_dir, 'index.html'), 'w', encoding='utf-8') as f:
            with open('index.html', 'r', encoding='utf-8') as src:
                f.write(src.read())
    
    app.run(host='127.0.0.1', port=5001, debug=True)