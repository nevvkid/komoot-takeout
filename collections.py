import os
import re
import time
import json
import csv
import logging
import threading
import concurrent.futures
from io import BytesIO
from datetime import datetime
from flask import request, jsonify, send_file
from pathlib import Path

# Import from main app module - these will be passed in when routes are registered
logger = None
collections_status = None
collections_lock = None
processing_status = None
processing_lock = None

# Will be set by the app.py module
BS4_AVAILABLE = False
BeautifulSoup = None
collections_manager = None

# Functions that will be initialized from app.py
add_log_entry = None
reset_status = None
extract_user_id_from_url = None
extract_collection_id_from_url = None
make_request_with_retry = None
get_default_output_dir = None
get_collection_slug = None
sanitize_filename = None

# Import Komoot adapter
from komoot_adapter import KomootAdapter

# These functions will be imported properly once modules are initialized
extract_tours_from_html = None
fetch_all_tours_from_collection = None

def scrape_collections_thread(email, password, collection_type):
    """Scrape collections in a background thread"""
    try:
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
                        return {'user_id': None, 'collections': []}
                        
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
                                        
                                        # Set the user ID for each collection explicitly
                                        for collection in page_collections:
                                            if 'creator' not in collection:
                                                collection['creator'] = {}
                                            if 'id' not in collection['creator']:
                                                collection['creator']['id'] = page_user_id
                                        
                                        # Process each collection to get full details
                                        detailed_collections = []
                                        for collection in page_collections:
                                            try:
                                                if 'url' in collection:
                                                    # Use our enhanced function to get all tours
                                                    detailed = fetch_all_tours_from_collection(anonymous_adapter, collection['url'], collections_status)
                                                    if detailed:
                                                        # Ensure user ID is preserved
                                                        if 'creator' not in detailed:
                                                            detailed['creator'] = {}
                                                        if 'id' not in detailed['creator']:
                                                            detailed['creator']['id'] = page_user_id
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
                                
                                # If we have a user ID from URL, ensure it's set in the collection
                                if extracted_user_id and not collection_user_id:
                                    if 'creator' not in collection:
                                        collection['creator'] = {}
                                    collection['creator']['id'] = extracted_user_id
                                    collection_user_id = extracted_user_id
                                
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
            for future in concurrent.futures.as_completed(future_to_url):
                try:
                    # Get the URL that this future was processing
                    url = future_to_url[future]
                    # Get the result from the future
                    result = future.result()
                    
                    if result:
                        # Extract collections from result
                        if result['collections']:
                            # Ensure user ID is properly set in each collection
                            for collection in result['collections']:
                                if result['user_id']:
                                    if 'creator' not in collection:
                                        collection['creator'] = {}
                                    if 'id' not in collection['creator'] or not collection['creator']['id']:
                                        collection['creator']['id'] = result['user_id']
                            
                            all_collections.extend(result['collections'])
                        
                        # Update user ID if we found one
                        if not user_id and result['user_id']:
                            user_id = result['user_id']
                            add_log_entry(f"Updated primary user ID to: {user_id}", collections_status)
                    
                    # Update progress
                    processed_urls += 1
                    with collections_lock:
                        collections_status['progress'] = processed_urls / len(collection_urls)
                        collections_status['collections_completed'] = processed_urls
                        
                except Exception as e:
                    add_log_entry(f"Error processing URL result: {str(e)}", collections_status)
        
        # Ensure all collections have the user ID properly set
        if user_id:
            for collection in all_collections:
                if 'creator' not in collection:
                    collection['creator'] = {}
                if 'id' not in collection['creator'] or not collection['creator']['id']:
                    collection['creator']['id'] = user_id
        
        # Save the results
        with collections_lock:
            collections_status['results'] = all_collections
            collections_status['status'] = 'completed'
            collections_status['progress'] = 1.0
            collections_status['collections_completed'] = processed_urls
            collections_status['collections_found'] = len(all_collections)
        
        add_log_entry(f"Public collections scraping completed. Found {len(all_collections)} collections with user ID: {user_id}", collections_status)
        
        # Save collections data to files if there are results, with explicit user_id
        if all_collections:
            collections_manager.save_collections_data(all_collections, user_id)
        
    except Exception as e:
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in public collections thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", collections_status)
        
        with collections_lock:
            collections_status['status'] = 'error'
            collections_status['error'] = error_msg

def enhance_collections_thread(collections_file, user_id):
    """
    Background thread to enhance previously saved collections with detailed tour data
    
    Args:
        collections_file: Path to the JSON file containing collections to enhance
        user_id: User ID for organization and file structure
        
    This function loads collection data from a JSON file, enhances each collection
    with detailed tour information, and saves the enhanced data back to files.
    Progress is tracked in the collections_status dictionary.
    """
    try:
        # Update status
        with collections_lock:
            collections_status['status'] = 'running'
            collections_status['progress'] = 0.0
            
        add_log_entry(f"Starting collection enhancement from file: {collections_file}", collections_status)
        
        # Load collections from the JSON file
        with open(collections_file, 'r', encoding='utf-8') as f:
            collections = json.load(f)
            
        # Update status with collection count
        with collections_lock:
            collections_status['collections_found'] = len(collections)
            
        add_log_entry(f"Loaded {len(collections)} collections from file", collections_status)
        
        # Create a KomootAdapter for enhancing tour data
        adapter = KomootAdapter()
        
        # Enhance each collection one by one
        enhanced_collections = []
        enhanced_count = 0
        total_enhanced_tours = 0
        
        for i, collection in enumerate(collections):
            try:
                # Update progress
                with collections_lock:
                    collections_status['progress'] = i / len(collections)
                    collections_status['collections_completed'] = i
                    
                # Check if collection is already enhanced
                tour_count = len(collection.get('tours', []))
                already_enhanced_count = sum(1 for tour in collection.get('tours', []) 
                                       if (tour.get('distance_km') is not None) and
                                       not tour.get('name', '').startswith(f"Tour {tour.get('id', '')}"))
                
                # Skip already enhanced collections (>80% enhanced tours)
                if tour_count > 0 and already_enhanced_count / tour_count > 0.8:
                    add_log_entry(f"Collection '{collection.get('name', 'Unknown')}' already enhanced ({already_enhanced_count}/{tour_count} tours). Skipping.", collections_status)
                    collection['is_enhanced'] = True
                    enhanced_collections.append(collection)
                    enhanced_count += 1
                    continue
                
                # Only enhance if the collection has a URL
                if 'url' not in collection:
                    add_log_entry(f"Collection '{collection.get('name', 'Unknown')}' has no URL, cannot enhance.", collections_status)
                    enhanced_collections.append(collection)
                    continue
                
                add_log_entry(f"Enhancing collection: {collection.get('name', 'Unknown')}", collections_status)
                
                # Use fetch_all_tours_from_collection to enhance the collection
                enhanced_collection = fetch_all_tours_from_collection(adapter, collection['url'], collections_status)
                
                if enhanced_collection:
                    # Ensure the enhanced collection preserves the original ID and creator info
                    if 'id' not in enhanced_collection and 'id' in collection:
                        enhanced_collection['id'] = collection['id']
                    
                    if 'creator' not in enhanced_collection and 'creator' in collection:
                        enhanced_collection['creator'] = collection['creator']
                    
                    # Count newly enhanced tours
                    newly_enhanced_tours = 0
                    if 'tours' in enhanced_collection:
                        for tour in enhanced_collection['tours']:
                            if (tour.get('distance_km') is not None) and not tour['name'].startswith(f"Tour {tour['id']}"):
                                newly_enhanced_tours += 1
                    
                    # Mark as enhanced for UI state
                    enhanced_collection['is_enhanced'] = (newly_enhanced_tours > 0)
                    enhanced_collections.append(enhanced_collection)
                    total_enhanced_tours += newly_enhanced_tours
                    
                    add_log_entry(f"Successfully enhanced {newly_enhanced_tours} tours in collection '{collection.get('name', 'Unknown')}'", collections_status)
                else:
                    # Keep original if enhancement fails
                    add_log_entry(f"Failed to enhance collection '{collection.get('name', 'Unknown')}', keeping original", collections_status)
                    enhanced_collections.append(collection)
                
                # Update progress
                enhanced_count += 1
                
            except Exception as e:
                # Keep original if enhancement fails
                error_msg = str(e)
                add_log_entry(f"Error enhancing collection '{collection.get('name', 'Unknown')}': {error_msg}", collections_status)
                enhanced_collections.append(collection)
        
        # Save the enhanced collections back to file with _enhanced suffix
        add_log_entry(f"Enhancement completed. Enhanced {total_enhanced_tours} tours across {enhanced_count} collections.", collections_status)
        
        # Save collections data to files with user_id
        collections_manager.set_user_id(user_id)
        collections_manager.save_collections_data(enhanced_collections, user_id, enhance_tours=True)
        
        # Update status with results
        with collections_lock:
            collections_status['results'] = enhanced_collections
            collections_status['status'] = 'completed'
            collections_status['progress'] = 1.0
            collections_status['collections_completed'] = len(collections)
            
        add_log_entry(f"Saved enhanced collections", collections_status)
        
    except Exception as e:
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in collection enhancement thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", collections_status)
        
        with collections_lock:
            collections_status['status'] = 'error'
            collections_status['error'] = error_msg

def download_collection_tours_thread(collections, output_dir, include_metadata, output_dir_structure, 
                                   download_images, gpx_options, user_id=None):
    """
    Download all tours in collections as GPX files in a background thread
    
    Args:
        collections: List of collections containing tours to download
        output_dir: Base directory to save downloaded files
        include_metadata: Whether to save collection metadata
        output_dir_structure: Organization structure for output directories
        download_images: Whether to download tour images
        gpx_options: Dictionary of GPX generation options
        user_id: Optional user ID for organization
        
    This function processes multiple collections concurrently, and for each collection,
    processes multiple tours concurrently using thread pools to improve performance.
    """
    try:
        with processing_lock:
            processing_status['status'] = 'running'
            
        # Create anonymous adapter for downloading
        adapter = KomootAdapter()
        
        # Get total tour count across all collections
        total_tours = sum(len(collection.get('tours', [])) for collection in collections)
        total_collections = len(collections)
        
        with processing_lock:
            processing_status['tours_found'] = total_tours
            
        add_log_entry(f"Starting download of {total_tours} tours from {total_collections} collections", processing_status)
        
        # Track global progress
        completed_tours = 0
        completed_collections = 0
        all_results = []
        
        # If no user_id was provided, try to extract it from the first collection
        if not user_id:
            for collection in collections:
                if 'creator' in collection and 'id' in collection['creator']:
                    user_id = collection['creator']['id']
                    add_log_entry(f"Using creator ID from collection as primary user ID: {user_id}", processing_status)
                    break
                
                # If still no user ID, try to extract from URL
                if collection.get('url'):
                    extracted_id = extract_user_id_from_url(collection.get('url'))
                    if extracted_id:
                        user_id = extracted_id
                        add_log_entry(f"Extracted user ID from URL as primary user ID: {user_id}", processing_status)
                        break
        
        # First create collections directory
        collections_base_dir = Path(output_dir) / 'collections'
        collections_base_dir.mkdir(parents=True, exist_ok=True)
        
        # Create user directory under collections directory
        if user_id:
            collections_dir = collections_base_dir / f"user-{user_id}"
        else:
            collections_dir = collections_base_dir
            
        collections_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine optimal number of workers based on collection count
        collection_workers = min(3, total_collections)
        
        # Process collections concurrently using a ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=collection_workers) as executor_collections:
            
            # Function to process a single collection
            def process_collection(collection_idx, collection):
                try:
                    collection_id = collection.get('id')
                    collection_name = collection.get('name', f"Collection_{collection_id}")
                    
                    # Create slug for collection folder
                    collection_slug = get_collection_slug(collection.get('url', ''), collection_name, max_slug_length=50)
                    
                    # Create folder for this specific collection in the user's collections directory
                    collection_dir = collections_dir / collection_slug
                    collection_dir.mkdir(parents=True, exist_ok=True)
                    
                    add_log_entry(f"Processing collection {collection_idx+1}/{total_collections}: {collection_name}", processing_status)
                    add_log_entry(f"Collection output directory: {collection_dir}", processing_status)
                    
                    # Save collection metadata if requested
                    if include_metadata:
                        try:
                            # Save JSON metadata
                            with open(collection_dir / 'collection_info.json', 'w', encoding='utf-8') as f:
                                json.dump(collection, f, indent=2, ensure_ascii=False)
                                
                            # Save human-readable metadata
                            with open(collection_dir / 'collection_info.txt', 'w', encoding='utf-8') as f:
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
                                add_date = gpx_options.get('addDate', True)
                                max_title_length = gpx_options.get('maxTitleLength', -1)
                                max_desc_length = gpx_options.get('maxDescLength', -1)
                                skip_existing = gpx_options.get('skipExisting', True)
                                
                                # Try using the direct API first for anonymous download
                                filename = None
                                try:
                                    add_log_entry(f"Downloading tour {tour_id} from collection {collection_name}", processing_status)
                                    gpx_content = None
                                    
                                    try:
                                        # Try to download directly from API first
                                        from tours import download_tour_using_gpx_api
                                        gpx_content = download_tour_using_gpx_api(tour_id)
                                        add_log_entry(f"Downloaded tour {tour_id} content from API", processing_status)
                                    except Exception as api_err:
                                        add_log_entry(f"Could not download tour {tour_id} directly: {str(api_err)}", processing_status)
                                        
                                    if gpx_content:
                                        # Create filename from tour name
                                        date_str = ""
                                        if add_date and 'date' in tour and tour['date']:
                                            # Get the date from the tour
                                            try:
                                                tour_date = tour['date']
                                                # Extract the date part if it's a datetime
                                                if 'T' in tour_date:
                                                    tour_date = tour_date.split('T')[0]
                                                # Format as YYYY-MM-DD
                                                parts = tour_date.split('-')
                                                if len(parts) >= 3:
                                                    date_str = f"{parts[0]}-{parts[1]}-{parts[2]}-"
                                                else:
                                                    # Try other date formats
                                                    try:
                                                        parsed_date = datetime.strptime(tour_date, '%Y-%m-%d')
                                                        date_str = f"{parsed_date.strftime('%Y-%m-%d')}-"
                                                    except ValueError:
                                                        # If we can't parse the date, just use it as is
                                                        date_str = f"{tour_date}-"
                                            except Exception as date_err:
                                                # If there's any error, just skip the date
                                                add_log_entry(f"Error parsing date for tour {tour_id}: {str(date_err)}", processing_status)
                                                date_str = ""
                                        
                                        # Create safe filename
                                        title = sanitize_filename(tour_name)
                                        if max_title_length == 0:
                                            title = f"{tour_id}"
                                        elif max_title_length > 0 and len(title) > max_title_length:
                                            title = f"{title[:max_title_length]}-{tour_id}"
                                        else:
                                            title = f"{title}-{tour_id}"
                                            
                                        filename = f"{date_str}{title}.gpx"
                                        filepath = collection_dir / filename
                                        
                                        # Save to file
                                        with open(filepath, 'wb') as f:
                                            f.write(gpx_content)
                                except Exception as api_err:
                                    # Fall back to adapter
                                    adapter.make_gpx(
                                        tour_id=tour_id,
                                        output_dir=str(collection_dir),
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
                                        images_dir = collection_dir / 'images'
                                        images_dir.mkdir(parents=True, exist_ok=True)
                                        
                                        image_paths = adapter.download_tour_images(
                                            tour_id=tour_id,
                                            tour=tour_data,
                                            output_dir=str(images_dir)
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
                                    'output_dir': str(collection_dir),
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
                                completed_tours += 1
                                
                                # Update progress
                                with processing_lock:
                                    processing_status['tours_completed'] = completed_tours
                                    processing_status['progress'] = completed_tours / total_tours
                    
                    # Create a summary file for this collection
                    try:
                        with open(collection_dir / 'download_summary.json', 'w', encoding='utf-8') as f:
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
                        csv_path = collection_dir / f"{collection_slug}_tours.csv"
                        
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
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in collection tours download thread: {error_msg}")
        add_log_entry(f"Error: {error_msg}", processing_status)
        
        with processing_lock:
            processing_status['status'] = 'error'
            processing_status['error'] = error_msg

# Collections-specific route handlers
def register_collection_routes(app, _globals):
    """Register all collection-related routes to the Flask app"""
    # Import globals from main app
    global logger, collections_status, collections_lock, processing_status, processing_lock
    global BS4_AVAILABLE, BeautifulSoup, collections_manager
    global add_log_entry, reset_status, extract_user_id_from_url, extract_collection_id_from_url
    global make_request_with_retry, get_default_output_dir, get_collection_slug, sanitize_filename
    global extract_tours_from_html, fetch_all_tours_from_collection
    
    # Set globals from the parent module
    logger = _globals.get('logger')
    collections_status = _globals.get('collections_status')
    collections_lock = _globals.get('collections_lock')
    processing_status = _globals.get('processing_status')
    processing_lock = _globals.get('processing_lock')
    BS4_AVAILABLE = _globals.get('BS4_AVAILABLE')
    BeautifulSoup = _globals.get('BeautifulSoup')
    collections_manager = _globals.get('collections_manager')
    add_log_entry = _globals.get('add_log_entry')
    reset_status = _globals.get('reset_status')
    extract_user_id_from_url = _globals.get('extract_user_id_from_url')
    extract_collection_id_from_url = _globals.get('extract_collection_id_from_url')
    make_request_with_retry = _globals.get('make_request_with_retry')
    get_default_output_dir = _globals.get('get_default_output_dir')
    get_collection_slug = _globals.get('get_collection_slug')
    sanitize_filename = _globals.get('sanitize_filename')
    extract_tours_from_html = _globals.get('extract_tours_from_html')
    fetch_all_tours_from_collection = _globals.get('fetch_all_tours_from_collection')

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
            data = request.json
            if data is None:
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
            logger.error(f"Error starting public collection scraping: {str(e)}")
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
            
            # If user_id is not in request, try to extract it from the first collection URL
            if not user_id:
                for collection in collections:
                    if 'url' in collection:
                        extracted_id = extract_user_id_from_url(collection['url'])
                        if extracted_id:
                            user_id = extracted_id
                            logger.info(f"Extracted user ID from collection URL: {user_id}")
                            break
            
            # GPX options
            gpx_options = data.get('gpxOptions', {})
            
            # Log details for debugging
            logger.info(f"Starting collection tours download with user_id: {user_id}")
            
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

    @app.route('/api/enhance-collections', methods=['POST'])
    def enhance_saved_collections():
        """Enhance previously saved collections with detailed tour data"""
        try:
            # Get parameters from request
            data = request.json
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            # Get user ID - required to locate the saved collections
            user_id = data.get('userId')
            if not user_id:
                return jsonify({'error': 'User ID is required to locate saved collections'}), 400
                
            # Set the output directory path
            output_dir = get_default_output_dir('collections')
            user_dir = os.path.join(output_dir, f"user-{user_id}")
            
            # Check if the user directory exists
            if not os.path.exists(user_dir):
                return jsonify({'error': f'No saved collections found for user ID: {user_id}'}), 404
                
            # Find the most recent collections file with basic metadata
            basic_json_files = [f for f in os.listdir(user_dir) if f.startswith('all_collections_') and f.endswith('_basic.json')]
            
            if not basic_json_files:
                return jsonify({'error': f'No basic collections file found in {user_dir}'}), 404
                
            # Use the most recent basic file
            collections_file = os.path.join(user_dir, sorted(basic_json_files)[-1])
            
            # Reset collections status
            with collections_lock:
                reset_status(collections_status)
            
            # Start a background thread for enhancement
            threading.Thread(
                target=enhance_collections_thread,
                args=(collections_file, user_id)
            ).start()
            
            return jsonify({
                'success': True, 
                'message': 'Enhancement of saved collections started',
                'statusEndpoint': '/api/collections-status'
            })
            
        except Exception as e:
            logger.error(f"Error starting collections enhancement: {str(e)}")
            return jsonify({'error': str(e)}), 500