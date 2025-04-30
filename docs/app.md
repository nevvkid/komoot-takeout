# app.py - Core Application Module

## Overview

`app.py` is the main application file for the komoot-takeout tool. It implements a Flask web application that provides a REST API for downloading tour data and collections from Komoot. This module handles all the core functionality including user authentication, tour data retrieval, collection management, and file generation.

## Key Features

### Tour Management

- **Tour Retrieval**: Download individual or batch tours from a Komoot account
- **GPX Generation**: Create GPX files with configurable options for POIs, metadata, and formatting
- **Batch Processing**: Handle large tour sets with chunking and pagination
- **Image Downloads**: Optionally download associated tour images

### Collection Management

- **Two-Step Collection Processing**: Separate basic scraping from detailed enhancement
- **Collection Scraping**: Extract collections from personal, saved, or public sources
- **Tour Data Enhancement**: Improve collection data with detailed tour information
- **Export Options**: Generate JSON and CSV exports with comprehensive metadata

## Core Components

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/start` | POST | Start tour download process |
| `/api/status` | GET | Get current processing status |
| `/api/collections/personal` | POST | Scrape personal collections |
| `/api/collections/saved` | POST | Scrape saved collections |
| `/api/collections/public` | POST | Scrape public collections by URL |
| `/api/collections-status` | GET | Get collection processing status |
| `/api/download-collection-tours` | POST | Download all tours in a collection |
| `/api/enhance-collections` | POST | Enhance previously saved collections with detailed tour data |
| `/api/export/collection/<collection_id>/csv` | GET | Export a specific collection as CSV |
| `/api/selected-folder` | GET | Get the currently selected download folder |
| `/api/select-folder` | POST | Set the selected download folder |
| `/api/tour-counts` | POST | Count the number of tours for a user |
| `/api/download/<tour_id>` | GET | Download a specific tour GPX file |
| `/api/export/images/<tour_id>` | GET | Export all images for a specific tour |
| `/api/results` | GET | Get the processing results |
| `/api/clear` | POST | Clear the current results |
| `/api/collections-results` | GET | Get the collections processing results |
| `/api/clear-collections` | POST | Clear the collections results |

### Dependencies Management

The application handles dependencies intelligently with graceful fallbacks for optional components:

```python
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
```

Key dependencies include:
- **Flask**: Core web framework for the application
- **KomootGPX**: Primary API for Komoot integration (auto-installed if missing)
- **BeautifulSoup4**: HTML parsing for collection and tour scraping
- **Concurrent.futures**: Threading library for parallel operations

### Status Tracking

The application provides real-time status tracking with detailed progress information:

```python
processing_status = {
    'status': 'idle',  # 'idle', 'running', 'completed', 'error', 'chunk_completed'
    'progress': 0.0,   # 0.0 to 1.0
    'tours_found': 0,
    'tours_completed': 0,
    'error': None,
    'log': [],         # Timestamped log entries
    'results': [],     # Processed items
    'next_chunk': 0    # For chunked processing
}
```

### Collection Manager

The `CollectionManager` class handles all collection-related operations:

```python
class CollectionManager:
    def __init__(self, output_dir=None):
        # Initialize with config
    
    def set_user_id(self, user_id):
        # Set user ID for organization
    
    def save_collections_data(self, collections, user_id=None, enhance_tours=False):
        # Save collections with optional enhancement
    
    def generate_jekyll_config(self, collections):
        # Generate Jekyll site config
```

#### Collection Export Features

The `CollectionManager` provides comprehensive data export functionality:

- **JSON Export**: Saves collections to both standard and timestamped JSON files
- **CSV Export**: Creates detailed CSV files for each collection with tour metadata
- **Deduplication**: Intelligently removes duplicate tours while preserving the most detailed data
- **Enhanced Metadata**: Calculates and adds derived fields like climbing intensity
- **Jekyll Integration**: Generates Jekyll-compatible configuration for static site generation

The class handles complex tasks such as:

1. Organizing collections by user ID
2. Creating consistent file naming with URL-friendly slugs
3. Formatting data fields for better human readability
4. Calculating metric conversions (km to miles)
5. Selecting relevant fields dynamically based on available data

Example of enhanced field calculation:
```python
# Calculate meters climbed per kilometer
if tour.get('elevation_up') and distance_km:
    meters_per_km = float(tour['elevation_up']) / float(distance_km)
    csv_tour["climbing_intensity"] = f"{meters_per_km:.1f}"
```

## Advanced HTML Parsing

The application implements sophisticated HTML parsing to handle Komoot's evolving UI:

```python
def extract_tours_from_html(html_content, status_dict):
    """
    Extract all tours from a collection page HTML content
    
    This function is enhanced to handle different page layouts and tour card formats
    in Komoot collection pages.
    """
```

### Multi-strategy HTML Parsing

To ensure resilience against UI changes, the parser implements multiple extraction strategies:

1. **Multiple Selector Targeting**: Uses a variety of CSS selectors to find tour cards
   ```python
   tour_card_selectors = [
       "div.tour-card", 
       ".collection-tour-card",
       "a[href*='/tour/']",
       ".tw-mb-8",  # Newer Komoot layout
       "div[role='listitem']",  # New Komoot UI role attribute
       ".css-1qyi8eq",  # Another potential class in newer layouts
       "li.tw-flex"  # Tour list items in some layouts
   ]
   ```

2. **Progressive Enhancement**: Extracts basic data first, then adds details when available
   ```python
   # First extract tour ID (essential)
   # Then extract additional details like title, date, stats, etc.
   ```

3. **Contextual Data Extraction**: Uses pattern matching for different data formats
   ```python
   # Extract duration from text like "2h 30min" or "45min"
   h_match = re.search(r'(\d+)\s*h', text)
   min_match = re.search(r'(\d+)\s*min', text)
   ```

4. **Fallback Mechanisms**: Provides default values when extraction fails
   ```python
   tour_name = f"Tour {tour_id}"  # Default name if extraction fails
   ```

This approach ensures the application continues to work even when Komoot updates its web interface.

## Concurrency and Threading Model

The application uses a sophisticated threading model with adaptive concurrency based on workload size:

### Thread Pool Management

```python
# Determine optimal number of workers based on tour count
max_workers = min(8, len(tour_ids) // 20 + 3)

# Process multiple tours concurrently for better speed
with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
    # Submit all tours to the thread pool
    future_to_tour = {executor.submit(process_single_tour, tour_id): tour_id for tour_id in tour_ids}
    
    # Process results as they complete
    for future in concurrent.futures.as_completed(future_to_tour):
        # Process results
```

### Adaptive Concurrency

The application dynamically adjusts thread pool size based on the workload:

1. **Large Collections**: More workers for collections with many tours
   ```python
   # Adjust max_workers based on expected collection size
   if expected_count > 100:
       max_workers = min(8, (expected_count // 50) + 3)
   ```

2. **Multi-level Concurrency**: Separate thread pools for collections and tours
   ```python
   # Process collections concurrently
   with concurrent.futures.ThreadPoolExecutor(max_workers=collection_workers) as executor_collections:
       # For each collection, process tours concurrently
       with concurrent.futures.ThreadPoolExecutor(max_workers=tour_workers) as executor_tours:
           # Process tours
   ```

3. **Resource Throttling**: Controls resource usage through worker limits
   ```python
   # Limit workers to prevent resource exhaustion
   max_workers = min(5, len(basic_collections))
   ```

### Thread Synchronization

Thread-safe operations are ensured through locks:

```python
# Lock for thread synchronization
processing_lock = threading.Lock()
collections_lock = threading.Lock()

# Thread-safe status updates
with processing_lock:
    processing_status['status'] = 'running'
```

## Network Resilience

The application implements robust network handling with retry logic:

### Exponential Backoff

```python
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
```

### Browser-like Headers

```python
headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36...',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.komoot.com/',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0'
}
```

## File System Management

The application implements a sophisticated file system organization:

### Path Management

```python
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
```

### Hierarchical Organization

```
selected_folder/
├── gpx/                     # Tour GPX files
├── images/                  # Tour images
│   └── {tour_id}/           # Images for specific tour
├── collections/             # Collection data
│   ├── user-{user_id}/      # User-specific collections
│   │   ├── index.html       # User profile redirect
│   │   ├── all_collections.json  # Master collection data
│   │   └── collections/     # Individual collection folders
│   │       └── {collection_slug}/ # Collection-specific data
│   │           ├── collection_info.json  # Collection metadata
│   │           ├── collection_info.txt   # Human-readable metadata
│   │           ├── {collection_slug}_tours.csv  # Tour data in CSV
│   │           └── [GPX files]  # Individual tour GPX files
```

## Two-Step Collection Enhancement

The application implements a two-step approach for collection enhancement:

1. **Basic Collection Scraping**: Fast initial scraping that captures collection metadata and basic tour information
2. **Detailed Tour Enhancement**: Optional second step that enriches tours with comprehensive details

### Benefits

- Faster initial collection saving
- Optional enhancement for users who need detailed data
- Prevents conflicts between downloading and enhancement
- Provides progress tracking for the enhancement process
- Efficiently skips already-enhanced collections

### Enhancement Process

The enhancement process intelligently detects and skips already-enhanced tours:

```python
# Skip collections that appear to be already enhanced
tour_count = len(collection.get('tours', []))
enhanced_tour_count = sum(1 for tour in collection.get('tours', []) 
                         if not tour.get('name', '').startswith(f"Tour {tour.get('id', '')}"))

if tour_count > 0 and enhanced_tour_count / tour_count > 0.8:
    # Collection is already enhanced, skip it
```

## Chunked Processing for Large Tour Sets

For large tour sets, the application implements chunked processing:

```python
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
```

This allows for processing very large tour sets in manageable chunks, with progress tracking between chunks.

## Multiple Download Methods

The application implements a cascading approach to downloading tours:

1. **KomootGPX Library** (preferred method, if available)
   ```python
   if KOMOOTGPX_AVAILABLE:
       filename = download_tour_using_komootgpx(tour_id, ...)
   ```

2. **Direct GPX API** (anonymous mode fallback)
   ```python
   if anonymous:
       try:
           gpx_content = download_tour_using_gpx_api(tour_id)
           # Save to file
       except Exception:
           # Fall back to adapter
   ```

3. **KomootAdapter** (final fallback)
   ```python
   adapter.make_gpx(
       tour_id=tour_id,
       output_dir=output_dir,
       # ... other parameters
   )
   ```

This ensures maximum compatibility and resilience against API changes.

## Background Processing

All long-running operations execute in background threads to keep the UI responsive:

```python
# Start a background thread
threading.Thread(
    target=process_function,
    args=(param1, param2)
).start()
```

## Error Handling

The application implements comprehensive error handling:

- Thread-safe status updates with locks
- Detailed error logging
- User-friendly error messages
- Graceful fallbacks when operations fail

## Performance Optimizations

The application includes several performance optimizations:

1. **Parallel Processing**: Concurrent downloads using thread pools
2. **Caching**: Reuse of existing tour data
3. **Intelligent Skipping**: Avoids reprocessing already enhanced collections
4. **Resource Limiting**: Dynamic worker pool sizing based on workload
5. **Chunked Processing**: Breaking large workloads into manageable pieces

## Security Considerations

1. **File System Safety**:
   ```python
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
   ```

2. **Browser-like Headers**: Mimics legitimate browser traffic
3. **Timeout Handling**: Prevents hanging on unresponsive servers
4. **Input Validation**: Checks for required parameters and valid formats

## Jekyll Site Generation

The application provides built-in support for generating Jekyll-compatible static sites:

```python
def generate_jekyll_config(self, collections):
    """Generate Jekyll _config.yml file from collections data."""
```

This creates a complete site configuration with:
- Collection definitions
- Metadata
- Permalinks
- Front matter defaults
- Plugin configuration

The generated site can be used with GitHub Pages or any Jekyll-compatible hosting.

## Enhance Collections Functionality

### Overview

The "Enhance Collections" feature allows users to improve previously saved collections with more detailed tour data. This is a two-step approach:

1. First, collections are scraped with basic metadata (faster initial process)
2. Later, these collections can be enhanced with comprehensive tour details (optional deeper analysis)

### API Implementation

```python
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
        return jsonify({'error': str(e)}), 500
```

### Enhancement Thread Implementation

The enhancement process runs in a background thread to keep the UI responsive:

```python
def enhance_collections_thread(collections_file, user_id):
    """Background thread to enhance previously saved collections with detailed tour data"""
    try:
        # Load collections from the JSON file
        with open(collections_file, 'r', encoding='utf-8') as f:
            collections = json.load(f)
        
        # Create a KomootAdapter for enhancing tour data
        adapter = KomootAdapter()
        
        # Enhance each collection one by one
        enhanced_collections = []
        enhanced_count = 0
        total_enhanced_tours = 0
        
        for i, collection in enumerate(collections):
            try:
                # Check if collection is already enhanced
                tour_count = len(collection.get('tours', []))
                already_enhanced_count = sum(1 for tour in collection.get('tours', []) 
                                         if (tour.get('distance_km') is not None) and
                                         not tour.get('name', '').startswith(f"Tour {tour.get('id', '')}"))
                
                # Skip already enhanced collections (>80% enhanced tours)
                if tour_count > 0 and already_enhanced_count / tour_count > 0.8:
                    collection['is_enhanced'] = True
                    enhanced_collections.append(collection)
                    enhanced_count += 1
                    continue
                
                # Only enhance if the collection has a URL
                if 'url' not in collection:
                    enhanced_collections.append(collection)
                    continue
                
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
                else:
                    enhanced_collections.append(collection)
                
                # Update progress
                enhanced_count += 1
                
            except Exception as e:
                # Keep original if enhancement fails
                enhanced_collections.append(collection)
        
        # Save the enhanced collections back to file with _enhanced suffix
        collections_manager.set_user_id(user_id)
        collections_manager.save_collections_data(enhanced_collections, user_id, enhance_tours=True)
        
    except Exception as e:
        # Handle any uncaught exceptions
        error_msg = str(e)
        logger.error(f"Error in collection enhancement thread: {error_msg}")
        with collections_lock:
            collections_status['status'] = 'error'
            collections_status['error'] = error_msg
```

### Key Enhancement Features

The enhancement process includes several intelligent features:

1. **Smart Skip Logic**: Already enhanced collections are detected and skipped
2. **Progress Tracking**: Real-time progress updates during the enhancement process
3. **Error Resilience**: Failed enhancements preserve original data
4. **Metadata Flagging**: Collections are explicitly marked as enhanced for UI feedback
5. **Timestamped Outputs**: Enhanced collections are saved with timestamp and suffix
6. **Detection Heuristics**: Multiple methods to determine if tours are already enhanced

### Enhanced Collection Data Storage

Enhanced collections are saved with special handling:

```python
# Save collections to JSON files with enhancement indicator
status_suffix = "_enhanced" if enhance_tours else "_basic"
timestamped_json_path = os.path.join(self.output_dir, 
    f"all_collections_{timestamp}{status_suffix}.json")
```

This two-step approach provides several benefits:
- Faster initial collection saving
- Optional enhancement for users who need detailed data
- Clear indication of enhancement status
- Preservation of original data

## Pagination and Data Collection Strategies

### Multi-Page Content Extraction

For collections with many tours, the application implements sophisticated pagination strategies:

```python
# Different approaches to fetch all pages
urls_to_try = []

# Approach 1: Systematic pagination with ?page=N parameter
max_pages = 20  # Safety limit
for page in range(2, max_pages + 1):
    page_url = f"{collection_url}?page={page}"
    urls_to_try.append(('page', page_url))

# Approach 2: Different page sizes for single-page retrieval
page_sizes = [50, 100, 200, 300, 500]
for page_size in page_sizes:
    size_url = f"{collection_url}?size={page_size}"
    urls_to_try.append(('size', size_url))
```

These pages are fetched concurrently with a thread pool:

```python
# Use a thread pool to fetch pages concurrently
with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_url = {executor.submit(process_url, url_tuple): url_tuple 
                     for url_tuple in urls_to_try}
```

This approach ensures maximum tour retrieval across different Komoot page layouts and configurations.