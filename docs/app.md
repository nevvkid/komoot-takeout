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

### Status Tracking

The application provides real-time status tracking with detailed progress information:

```python
processing_status = {
    'status': 'idle',  # 'idle', 'running', 'completed', 'error'
    'progress': 0.0,   # 0.0 to 1.0
    'tours_found': 0,
    'tours_completed': 0,
    'error': None,
    'log': [],         # Timestamped log entries
    'results': []      # Processed items
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

## File Management

- Creates organized directory structures for tours and collections
- Handles user-specific directories with proper permissions
- Generates consistent filenames with configurable formatting
- Creates backup copies of important files