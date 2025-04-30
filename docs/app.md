# app.py - Core Application Module

## Overview

`app.py` is the core backend module of the komoot-takeout application, implementing a Flask web server that handles all API endpoints and business logic. This file contains the functionality for downloading Komoot tours, extracting collection data, and managing file operations.

## Key Components

### Core Dependencies and Imports

The application relies on several key libraries:
- **Flask**: Web framework to handle HTTP requests
- **Beautiful Soup**: For HTML parsing of Komoot collection pages
- **KomootGPX**: Optional dependency for enhanced GPX export functionality
- **Requests**: For making HTTP requests to Komoot's website and API
- **Threading/Concurrency**: For handling long-running tasks without blocking

### Global Configuration

- **Selected Folder Management**: Tracks the user-selected download location
- **Status Tracking**: Maintains processing status for tours and collections
- **Thread Synchronization**: Implements locks to prevent race conditions

### Core Helper Functions

| Function | Purpose |
|----------|---------|
| `set_selected_folder()` | Sets the global download directory |
| `get_selected_folder()` | Retrieves current download directory |
| `get_default_output_dir()` | Creates and returns a subdirectory in the download folder |
| `reset_status()` | Resets processing status dictionaries |
| `add_log_entry()` | Adds timestamped log entries to status tracking |
| `sanitize_filename()` | Ensures filenames are valid across platforms |
| `extract_user_id_from_url()` | Parses user IDs from Komoot URLs |
| `extract_collection_id_from_url()` | Parses collection IDs from Komoot URLs |
| `get_collection_slug()` | Creates URL-friendly slugs for collections |
| `create_user_index_html()` | Generates redirect HTML files for user profiles |
| `make_request_with_retry()` | Makes HTTP requests with retry logic |

### Collection Management

The `CollectionManager` class handles all collection-related operations:
- Saving collection data to JSON and CSV files
- Generating Jekyll configuration files from collections
- Creating ZIP archives of collection data
- Organizing data by user ID

### Tour Extraction and Processing

| Function | Purpose |
|----------|---------|
| `download_tour_using_gpx_api()` | Downloads tours directly from Komoot's GPX API |
| `download_tour_using_komootgpx()` | Uses the KomootGPX library for enhanced GPX exports |
| `extract_tours_from_html()` | Parses tour data from HTML collection pages |
| `fetch_all_tours_from_collection()` | Retrieves all tours from a collection with pagination handling |
| `process_tours()` | Background worker that processes multiple tours concurrently |

### API Endpoints

#### Basic Application Endpoints
- `GET /`: Renders the main application page
- `GET /api/selected-folder`: Returns the currently selected download folder
- `POST /api/select-folder`: Sets the download folder

#### Tour Processing Endpoints
- `POST /api/start`: Starts the tour processing with various options
- `GET /api/status`: Returns the current processing status
- `GET /api/results`: Returns the results of tour processing
- `POST /api/clear`: Clears current results
- `GET /api/download/<tour_id>`: Downloads a specific tour as GPX
- `GET /api/export/all`: Exports all tours as a ZIP file
- `GET /api/export/images/<tour_id>`: Exports images for a specific tour
- `POST /api/tour-counts`: Counts tours for a user

#### Collection Processing Endpoints
- `POST /api/collections/personal`: Scrapes personal collections
- `POST /api/collections/saved`: Scrapes saved collections
- `POST /api/collections/public`: Scrapes public collections by URL
- `GET /api/collections-status`: Returns the collection processing status
- `GET /api/collections-results`: Returns collection processing results
- `POST /api/clear-collections`: Clears collection results
- `GET /api/export/collections`: Exports all collections as a ZIP file
- `POST /api/download-collection-tours`: Downloads all tours in specified collections
- `GET /api/export/collection/<collection_id>/csv`: Exports a collection as CSV

## Tour Processing Flow

1. User selects parameters (tour IDs, authentication details, etc.)
2. Application starts a background thread to process the request
3. For each tour:
   - Tries KomootGPX for enhanced export (if available)
   - Falls back to direct API download or custom adapter as needed
   - Processes tour metadata
   - Downloads images if requested
   - Updates status with progress information
4. Results are stored and made available via API endpoints

## Collection Processing Flow

1. User selects collection type or provides collection URLs
2. Background thread scrapes collection data from Komoot
3. The application:
   - Extracts all tours from each collection
   - Handles pagination for large collections
   - Processes metadata (cover images, descriptions, etc.)
   - Downloads all tours in the collection if requested
4. Results can be exported as JSON, CSV, or ZIP files

## Error Handling

The application implements comprehensive error handling:
- Thread-level error catching to prevent crashes
- Status tracking with error messages
- Retry logic for network operations
- Graceful fallbacks for missing dependencies

## Concurrency Model

Long-running operations are handled in background threads to prevent blocking:
- Main Flask server remains responsive during processing
- ThreadPoolExecutor for concurrent tour processing
- Thread synchronization using locks to protect shared state
- Progress tracking for UI updates

## Cross-Platform Considerations

- File path handling uses platform-agnostic methods
- Directory creation with proper error handling
- Filename sanitization for compatibility