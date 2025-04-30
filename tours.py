import os
import re
import time
import json
import logging
import threading
import concurrent.futures
from datetime import datetime
import requests
from flask import request, jsonify, send_file
from io import BytesIO
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables needed for tours
KOMOOTGPX_AVAILABLE = False
BS4_AVAILABLE = False
BeautifulSoup = None
processing_status = None
processing_lock = None

# Import Komoot adapter
from komoot_adapter import KomootAdapter

# These functions will be initialized from app.py
add_log_entry = None
reset_status = None
get_default_output_dir = None
sanitize_filename = None
make_request_with_retry = None
extract_collection_id_from_url = None

def download_tour_using_gpx_api(tour_id):
    """Download tour directly from Komoot's GPX API"""
    gpx_url = f"https://www.komoot.com/api/v007/tours/{tour_id}/gpx"
    response = requests.get(gpx_url)
    if response.status_code != 200:
        raise Exception(f"Failed to download tour from GPX API: HTTP {response.status_code}")
    return response.content

def download_tour_using_komootgpx(tour_id, email=None, password=None, output_dir=None, 
                                 include_poi=True, max_desc_length=-1, max_title_length=-1, add_date=True):
    """
    Download a tour using the KomootGPX library
    
    Args:
        tour_id: The Komoot tour ID to download
        email: Optional Komoot account email for authenticated download
        password: Optional Komoot account password
        output_dir: Directory where the GPX file will be saved
        include_poi: Whether to include points of interest in the GPX
        max_desc_length: Maximum length for descriptions (-1 for unlimited)
        max_title_length: Maximum length for titles (-1 for unlimited)
        add_date: Whether to add date to the filename
        
    Returns:
        str: Filename of the downloaded file or None if download failed
        
    This function uses the KomootGPX library if available, which provides
    the most reliable download experience with full metadata support.
    """
    if not KOMOOTGPX_AVAILABLE:
        return None
        
    try:
        # Import KomootGPX functionality
        from komootgpx import KomootGPX
        
        # Create output directory if it doesn't exist
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
        # Create a KomootGPX instance
        kgpx = KomootGPX(email=email, password=password)
        
        # Create filename based on tour_id
        filename = f"tour_{tour_id}.gpx"
        filepath = output_path / filename if output_dir else filename
        
        # Download the tour
        logger.info(f"Downloading tour {tour_id} using KomootGPX library...")
        kgpx.download(
            tour_id, 
            output_filepath=str(filepath),  # KomootGPX expects string path
            include_pois=include_poi,
            max_description_length=max_desc_length,
            max_title_length=max_title_length,
            add_date_to_filename=add_date
        )
        
        # Get the actual filename that was created (in case add_date changed it)
        if add_date and output_dir:
            # Look for files matching the pattern
            potential_files = list(output_path.glob(f"*-{tour_id}.gpx"))
            if potential_files:
                filename = potential_files[0].name
                filepath = output_path / filename
        
        # Check if file was actually created
        if filepath.exists():
            logger.info(f"Successfully downloaded tour {tour_id} to {filepath}")
            return filename
        else:
            logger.warning(f"KomootGPX did not create file for tour {tour_id}")
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
            add_log_entry("BeautifulSoup4 is not installed. Cannot parse HTML.", status_dict)
            return []
            
        tours = []
        
        # Parse the HTML content
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # First check for modern collection layout - using data-testid
        modern_cards = soup.select('[data-testid^="tour_item_"]')
        if modern_cards:
            add_log_entry(f"Found {len(modern_cards)} tours with modern layout", status_dict)
        
        # Try multiple selectors for tour cards to handle different page layouts
        tour_card_selectors = [
            "div.tour-card", 
            ".collection-tour-card",
            "a[href*='/tour/']",
            ".tw-mb-8",  # Newer Komoot layout
            "div[role='listitem']",  # New Komoot UI role attribute for tour items
            ".css-1qyi8eq",  # Another potential class in newer layouts
            "li.tw-flex"  # Tour list items in some layouts
        ]
        
        # Try each selector and collect all possible tour cards
        all_cards = modern_cards.copy() if modern_cards else []
        
        for selector in tour_card_selectors:
            cards = soup.select(selector)
            if cards:
                # Add only cards we haven't already found
                for card in cards:
                    if card not in all_cards:
                        all_cards.append(card)
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
                else:
                    # Look for tour links within the card
                    tour_links = card.select('a[href*="/tour/"]')
                    for link in tour_links:
                        match = re.search(r'/tour/(\d+)', link.get('href', ''))
                        if match:
                            tour_id = match.group(1)
                            break
                
                # Skip if no tour ID found
                if not tour_id:
                    continue
                
                # Check if we already have this tour
                if any(t.get('id') == tour_id for t in tours):
                    continue
                
                # Create tour object
                tour = {
                    'id': tour_id,
                    'url': f"https://www.komoot.com/tour/{tour_id}",
                    'name': f"Tour {tour_id}"  # Default name
                }
                
                # Try to extract tour name
                name_selectors = [
                    ".tour-card__title", 
                    ".tour-title", 
                    "h3",  # Common heading for tour names
                    ".tw-font-bold",  # Often used for tour names in newer layouts
                    ".tw-break-words",  # Used in some layouts for tour names
                    "[data-testid='tour_item_title']"  # Newer layout with test IDs
                ]
                
                for selector in name_selectors:
                    name_elem = card.select_one(selector)
                    if name_elem:
                        name_text = name_elem.get_text(strip=True)
                        if name_text:
                            tour['name'] = name_text
                            break
                
                # Try to extract tour stats
                stats_selectors = [
                    "div.tour-card__data-item",
                    "div.section__data__item",
                    ".tw-text-xs",  # Small text is often stats
                    ".tw-text-gray-500"  # Gray text often used for stats
                ]
                
                for selector in stats_selectors:
                    stats_elems = card.select(selector)
                    for elem in stats_elems:
                        text = elem.get_text(strip=True)
                        
                        # Extract distance
                        if ('km' in text or 'mi' in text) and 'distance_km' not in tour:
                            distance_match = re.search(r'([\d.,]+)', text)
                            if distance_match:
                                try:
                                    distance_km = float(distance_match.group(1).replace(',', '.'))
                                    tour['distance_km'] = distance_km
                                except:
                                    pass
                        
                        # Extract duration
                        elif ('h' in text or 'hr' in text or 'min' in text) and 'duration' not in tour:
                            hours = 0
                            minutes = 0
                            
                            h_match = re.search(r'(\d+)\s*h', text)
                            min_match = re.search(r'(\d+)\s*min', text)
                            
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
                        # Extract high point
                        elif ('high point' in text or 'highpoint' in text or 'max. height' in text) and 'high_point' not in tour:
                            high_match = re.search(r'([\d.,]+)', text)
                            if high_match:
                                try:
                                    tour['high_point'] = int(high_match.group(1).replace('.', ''))
                                except:
                                    pass
                                    
                        # Try to extract sport type
                        sport_words = ['hike', 'bike', 'run', 'mountain', 'road', 'tour', 'gravel', 'jog', 'cycle']
                        for sport in sport_words:
                            if sport in text.lower() and len(text) < 20:
                                tour['sport'] = sport
                                break
                            
                        if 'sport' in tour:
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
                
                # Try to extract tour image URL
                img_elems = card.select('img')
                for img in img_elems:
                    if img.get('src') and ('.jpg' in img['src'] or '.jpeg' in img['src'] or '.png' in img['src']):
                        # Skip very small thumbnails
                        if 'tiny' in img['src'] or 'thumb' in img['src']:
                            continue
                            
                        image_url = img['src']
                        # Remove small thumbnails or resized versions
                        # Try to find the original version by removing query parameters
                        if '?' in image_url:
                            image_url = image_url.split('?')[0]
                        tour['image_url'] = image_url
                        break
                    elif img.get('data-src'):
                        tour['image_url'] = img['data-src']
                        break
                    elif img.get('data-lazyload'):
                        tour['image_url'] = img['data-lazyload']
                        break
                    elif img.get('srcset'):
                        # Extract the largest image from srcset
                        srcset = img['srcset']
                        # Find the last URL in the srcset (typically the largest)
                        srcset_parts = srcset.split(',')
                        if srcset_parts:
                            last_part = srcset_parts[-1]
                            url_match = re.search(r'(https?://[^\s]+)', last_part)
                            if url_match:
                                tour['image_url'] = url_match.group(1)
                                break
                
                # Try to extract user/author
                creator_selectors = [
                    ".tour-card__user", 
                    ".user-card__name", 
                    ".tw-text-xs a"  # Often contains creator link in newer layouts
                ]
                
                for selector in creator_selectors:
                    creator_elem = card.select_one(selector)
                    if creator_elem:
                        tour['creator_name'] = creator_elem.get_text(strip=True)
                        break
                
                # Try to extract date
                date_selectors = [
                    ".tour-card__date", 
                    ".tour-date", 
                    ".tw-text-xs"  # Small text often contains date
                ]
                
                for selector in date_selectors:
                    date_elems = card.select(selector)
                    for elem in date_elems:
                        text = elem.get_text(strip=True)
                        # Look for date patterns like "Jan 2021" or "01/15/2021"
                        if re.search(r'\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}', text) or \
                           re.search(r'[A-Za-z]{3,9}\s+\d{4}', text) or \
                           re.search(r'\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}', text):
                            tour['date'] = text
                            break
                        
                        if 'date' in tour:
                            break
                
                # Try to extract region/location
                region_selectors = [
                    ".tour-card__location", 
                    ".tour-location", 
                    ".tw-text-xs"  # Small text often contains location
                ]
                
                for selector in region_selectors:
                    region_elems = card.select(selector)
                    for region_elem in region_elems:
                        region_text = region_elem.get_text(strip=True)
                        # Skip if it's just a date or has numbers (likely not a region)
                        if (region_text and not re.search(r'\d', region_text) and 
                            'ago' not in region_text.lower() and len(region_text) < 50):
                            tour['region'] = region_text
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
                
                # If not found, try looking for it in the meta description
                if not expected_count:
                    meta_desc = soup.select_one('meta[name="description"]')
                    if meta_desc and 'content' in meta_desc.attrs:
                        desc_text = meta_desc['content']
                        count_match = re.search(r'(\d+)\s*(?:routes|tours)', desc_text)
                        if count_match:
                            expected_count = int(count_match.group(1))
                            add_log_entry(f"Extracted expected tour count from meta description: {expected_count}", status_dict)
                
                # As a last resort, check for any text containing "X routes" or "X tours"
                if not expected_count:
                    # Look for text with numbers followed by 'routes' or 'tours'
                    for text_elem in soup.find_all(text=re.compile(r'\d+\s*(?:routes|tours)')):
                        count_match = re.search(r'(\d+)\s*(?:routes|tours)', text_elem)
                        if count_match:
                            expected_count = int(count_match.group(1))
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

def process_tours(anonymous, email, password, tour_selection, filter_type, 
                 no_poi, output_dir, skip_existing, id_filename, add_date,
                 max_title_length, max_desc_length, download_images,
                 chunk_size, chunk_start):
    """
    Process tours in a background thread
    
    Args:
        anonymous: Whether to use anonymous mode (no login required)
        email: Komoot account email for authenticated access
        password: Komoot account password
        tour_selection: 'all' or specific tour ID to download
        filter_type: Filter for tour types ('all', 'planned', 'recorded', 'favorite')
        no_poi: Whether to exclude points of interest
        output_dir: Directory to save downloaded files
        skip_existing: Whether to skip tours that already exist
        id_filename: Whether to use only tour ID as filename
        add_date: Whether to add date prefix to filenames
        max_title_length: Maximum length for tour titles
        max_desc_length: Maximum length for descriptions
        download_images: Whether to download tour images
        chunk_size: Size of chunks for batch processing
        chunk_start: Starting index for chunked processing
        
    This function handles both batch tour processing and single tour downloads,
    with support for chunked downloads of large tour sets.
    """
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
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
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
                
                # Keep track of next chunk start
                with processing_lock:
                    processing_status['next_chunk'] = end_idx
                    
                add_log_entry(f"Processing chunk {start_idx}-{end_idx} ({len(tour_ids)} tours)", processing_status)
            else:
                # Process all tours
                tour_ids = list(tours.keys())
            
            # Determine optimal number of workers based on tour count
            max_workers = min(8, len(tour_ids) // 20 + 3)
            
            # Results array
            results = []
            
            # Process multiple tours concurrently for better speed
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
                                # Create images directory using pathlib
                                images_dir = Path(output_dir).parent / 'images'
                                images_dir.mkdir(parents=True, exist_ok=True)
                                image_paths = adapter.download_tour_images(
                                    tour_id=tour_id,
                                    tour=tour_data,
                                    output_dir=str(images_dir)
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
                
                # Keep track of completed tours
                completed_count = 0
                
                # Process results as they complete
                for future in concurrent.futures.as_completed(future_to_tour):
                    tour_id = future_to_tour[future]
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                            
                        # Update progress
                        completed_count += 1
                        with processing_lock:
                            processing_status['tours_completed'] = completed_count
                            processing_status['progress'] = completed_count / len(tour_ids)
                            processing_status['results'] = results.copy()
                            
                        add_log_entry(f"Completed tour {completed_count}/{len(tour_ids)}: {result['name'] if result else 'Error'}", processing_status)
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
                
            # Try KomootGPX first if available
            filename = None
            tour_data = None
            if KOMOOTGPX_AVAILABLE and not anonymous:
                add_log_entry(f"Attempting to download tour {tour_id} using KomootGPX...", processing_status)
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
                
                if filename:
                    add_log_entry(f"Successfully downloaded tour {tour_id} with KomootGPX", processing_status)
                    # Get tour data from adapter
                    tour_data = adapter.get_tour_details(tour_id)
                else:
                    add_log_entry(f"KomootGPX download failed, falling back to adapter", processing_status)
            
            # If anonymous mode or KomootGPX failed, try direct API
            if anonymous:
                try:
                    add_log_entry(f"Attempting to download tour {tour_id} via direct API (anonymous mode)...", processing_status)
                    gpx_content = download_tour_using_gpx_api(tour_id)
                    
                    # Generate filename
                    filename = f"tour_{tour_id}.gpx"
                    if id_filename:
                        filename = f"{tour_id}.gpx"
                    
                    # Save to file using pathlib
                    filepath = Path(output_dir) / filename
                    with open(filepath, 'wb') as f:
                        f.write(gpx_content)
                        
                    add_log_entry(f"Successfully downloaded tour {tour_id} to {filepath}", processing_status)
                    
                except Exception as e:
                    add_log_entry(f"Direct API download failed: {str(e)}, falling back to adapter", processing_status)
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
                    tour_data = adapter.get_last_tour()
                    
            # If KomootGPX failed and not anonymous, use adapter
            elif not filename:
                add_log_entry(f"Downloading tour {tour_id} using adapter...", processing_status)
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
                tour_data = adapter.get_last_tour()
            
            # Download images if requested
            image_paths = []
            if download_images and tour_data:
                try:
                    # Create images directory using pathlib
                    images_dir = Path(output_dir).parent / 'images'
                    images_dir.mkdir(parents=True, exist_ok=True)
                    image_paths = adapter.download_tour_images(
                        tour_id=tour_selection,
                        tour=tour_data,
                        output_dir=str(images_dir)
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
    
    except Exception as e:
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in processing thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", processing_status)
        
        with processing_lock:
            processing_status['status'] = 'error'
            processing_status['error'] = error_msg

# Define the tour-specific route handlers
def register_tour_routes(app):
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
            
            # Start background thread
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
        """
        Download a specific tour GPX file
        
        Args:
            tour_id: The Komoot tour ID to download
            
        Returns:
            The GPX file for download or an error response
            
        This endpoint checks if the file exists locally first, and if not,
        attempts to download it directly from Komoot's API.
        """
        try:
            # Create output directory
            output_dir = Path(get_default_output_dir('gpx'))
            
            # Check if file exists already using pathlib
            potential_files = list(output_dir.glob(f"*{tour_id}.gpx"))
            
            if potential_files:
                # Return the first matching file
                return send_file(
                    str(potential_files[0]),
                    as_attachment=True,
                    download_name=potential_files[0].name
                )
            
            # If not found, try to download it anonymously
            try:
                gpx_content = download_tour_using_gpx_api(tour_id)
                filename = f"tour_{tour_id}.gpx"
                
                # Return as a file
                return send_file(
                    BytesIO(gpx_content),
                    as_attachment=True,
                    download_name=filename,
                    mimetype='application/gpx+xml'
                )
                
            except Exception as e:
                return jsonify({'error': f'Failed to download tour: {str(e)}'}), 404
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/export/images/<tour_id>', methods=['GET'])
    def export_images(tour_id):
        """
        Export all images for a specific tour as individual files
        
        Args:
            tour_id: The Komoot tour ID whose images will be exported
            
        Returns:
            JSON with list of image file paths or an error response
            
        This endpoint provides access to previously downloaded tour images.
        """
        try:
            # Use default images directory with pathlib
            base_dir = Path(get_default_output_dir('images'))
            
            # Check if images directory exists for this tour
            images_dir = base_dir / str(tour_id)
            if not images_dir.exists():
                return jsonify({'error': 'No images found for this tour'}), 404
    
            # Get list of image files using pathlib
            image_files = []
            for file_path in images_dir.glob('**/*'):
                if file_path.is_file():
                    image_files.append({
                        'path': str(file_path),
                        'filename': file_path.name
                    })
            
            # Return the list of images
            return jsonify({
                'tour_id': tour_id,
                'images': image_files
            })
            
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
            
            if not email or not password:
                return jsonify({'error': 'Email and password are required'}), 400
                
            # Create a Komoot adapter
            adapter = KomootAdapter()
            
            # Login to Komoot
            add_log_entry(f"Logging in to Komoot as {email}...", processing_status)
            adapter.login(email, password)
            
            # Fetch tours for each filter type and store counts
            all_tours = adapter.fetch_tours('all')
            recorded_tours = adapter.fetch_tours('recorded')
            planned_tours = adapter.fetch_tours('planned')
            favorite_tours = adapter.fetch_tours('favorite')
            
            # Create counts dictionary with the keys exactly matching what the frontend expects
            tour_counts = {
                'all': len(all_tours),
                'tour_recorded': len(recorded_tours),
                'tour_planned': len(planned_tours),
                'favorite': len(favorite_tours)
            }
            
            return jsonify({
                'success': True,
                'counts': tour_counts,
                'total_tours': len(all_tours),
                'user': {
                    'id': adapter.get_user_id(),
                    'name': adapter.get_display_name()
                }
            })
                
        except Exception as e:
            return jsonify({'error': str(e)}), 500