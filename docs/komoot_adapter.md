# komoot_adapter.py - Komoot API Integration Module

## Overview

`komoot_adapter.py` serves as the integration layer between the komoot-takeout application and Komoot's web service. This module handles authentication, retrieval of tour data, collection management, and GPX file generation. It implements various scraping and API access strategies to robustly handle Komoot's service changes and maintain compatibility.

## Key Components

### Core Dependencies

The adapter relies on several libraries for its functionality:
- **Requests**: For making HTTP requests to Komoot's API endpoints
- **Beautiful Soup**: For parsing HTML content when direct API access is unavailable
- **gpxpy**: For generating well-formed GPX files with advanced features
- **KomootGPX**: Optional external tool for enhanced GPX export capabilities

### Authentication and Session Management

The adapter implements a browser-like session for reliable access to Komoot:
- Maintains consistent headers to appear as a legitimate browser
- Handles authentication tokens and user credentials securely 
- Provides fallback mechanisms when direct API access is restricted

### Tour Data Access

| Function | Purpose |
|----------|---------|
| `fetch_tour()` | Retrieves comprehensive tour data with retries |
| `fetch_tours()` | Gets all tours for the authenticated user |
| `_scrape_tour_page()` | Fallback method to extract tour data from HTML |
| `make_gpx()` | Generates GPX files with configurable options |
| `download_tour_images()` | Downloads images associated with a tour |

### Collection Management

The adapter provides robust collection handling:
- Multi-strategy HTML parsing to extract collection metadata
- Pagination handling to retrieve all tours in large collections
- Support for both personal and saved collection types
- Export capabilities to JSON and CSV formats
- Tour name enhancement for better readability in exports

## Two-Step Collection Enhancement

The module provides a flexible approach to collection enhancement that can be performed in two separate steps:

1. **Basic Collection Retrieval**: Initial fast scraping to gather basic collection metadata
2. **Detailed Tour Enhancement**: Optional second step to add comprehensive tour details

### Tour Enhancement Process

The `enhance_collection_tours()` method handles enriching tour data in collections:

```python
def enhance_collection_tours(self, collection, max_tours=None):
    """Enhance tour data in a collection with full details
    
    Args:
        collection: The collection to enhance
        max_tours: Maximum number of tours to enhance (to avoid long processing times)
        
    Returns:
        Enhanced collection with detailed tour data
    """
```

The enhancement process intelligently:
- Skips tours that are already enhanced
- Attempts multiple strategies to retrieve tour data
- Falls back gracefully when tour data is unavailable
- Limits processing to a configurable number of tours to prevent timeouts

### Enhancement Detection

The enhancement system can detect which tours have already been enhanced:

```python
# Check if tour already has detailed data
if not tour['name'].startswith("Tour ") and 'distance_km' in tour:
    logger.info(f"Tour {tour_id} already has detailed data, skipping")
    enhanced_tours.append(tour)
    continue
```

### Enhancement Strategies

The module employs multiple strategies for tour enhancement:

1. **HTML Scraping**: First tries to scrape tour pages for faster access:
```python
# First try HTML scraping as it's faster
try:
    full_tour = self._scrape_tour_page(tour_id)
    logger.info(f"Retrieved tour data via scraping for {tour_id}")
except Exception as scrape_err:
    logger.warning(f"Error scraping tour page: {str(scrape_err)}")
```

2. **API Access**: Falls back to API calls for comprehensive data:
```python
# If scraping failed, try API call
if not full_tour or full_tour['name'] == f"Tour {tour_id}":
    try:
        full_tour = self.fetch_tour(tour_id, anonymous=True)
        logger.info(f"Retrieved tour data via API for {tour_id}")
    except Exception as api_err:
        logger.warning(f"Error fetching tour via API: {str(api_err)}")
```

## GPX Generation

The module offers several strategies for GPX generation:
1. Using the KomootGPX external tool if available
2. Direct download from Komoot's GPX API endpoint
3. Custom GPX generation using the gpxpy library
4. Simple fallback XML generation when other methods fail

## Core Class: KomootAdapter

The `KomootAdapter` class encapsulates all Komoot interaction functionality:

### Initialization and Authentication

```python
adapter = KomootAdapter()
adapter.login(email, password)  # Authenticate with Komoot
```

### Tour Access and GPX Creation

```python
# Fetch tour data
tour = adapter.fetch_tour(tour_id)

# Generate GPX file
adapter.make_gpx(
    tour_id,
    output_dir='exports', 
    include_poi=True,
    add_date=True,
    max_title_length=50
)
```

### Collection Management and Enhancement

```python
# Fetch user collections
collections = adapter.fetch_collections()

# Get a specific collection
collection = adapter.fetch_collection_by_url(collection_url)

# Export to formats
adapter.export_collection_to_json(collection)
adapter.export_collection_to_csv(collection)

# Enhance collection tours with detailed information
enhanced_collection = adapter.enhance_collection_tours(collection)
```

## Fallback Strategies

The adapter implements multiple fallback strategies to maintain compatibility:

### HTML Scraping vs Direct API Access

- Tries direct API access first for better data fidelity
- Falls back to HTML scraping when API limits are encountered
- Uses multiple CSS selector strategies to handle Komoot's UI changes

### Tour Extraction from Collections

When retrieving tours from a collection:
1. First attempts to extract from the initial page load
2. Then tries with increased page size parameters
3. Finally uses explicit pagination to get all tours
4. Handles and prevents duplicate tour entries

### Tour Name Enhancement

The module provides multiple strategies for enhancing tour data:
1. First attempts to scrape tour pages for faster access
2. Falls back to API calls when needed for comprehensive data
3. Intelligently skips already-enhanced tours to improve performance
4. Preserves original data when enhancement fails

### GPX Generation Options

The module offers a flexible approach to GPX generation:
- KomootGPX library integration for enhanced metadata
- Direct GPX API access for simplicity
- Custom GPX generation for full control over content
- Fallback simple XML when other options fail

## Error Handling

The adapter implements comprehensive error handling:
- Retry logic for transient network issues
- Fallback strategies when primary methods fail
- Detailed logging of errors and operations
- Graceful degradation when optional dependencies are missing

## Tour Content Processing

### Points of Interest (POIs) and Highlights

The adapter extracts and processes:
- Regular POIs with location and description
- Komoot Highlights with tips, comments and images
- User-contributed content from highlight discussions
- Photo references and downloadable image content

### Route Data Processing

The module handles:
- Coordinate conversion to GPX format
- Elevation data integration
- Timestamp calculation and correction
- Surface and way type metadata (when available)

## Cross-Platform Considerations

- Uses platform-independent path handling
- Implements proper filename sanitization
- Creates necessary directories with error handling
- Handles text encoding appropriately for international content

## Utility Functions

The module provides several utility functions:
- `sanitize_filename()`: Ensures valid filenames across platforms
- `extract_slug_from_url()`: Parses URL components for better organization
- `extract_user_from_tip()`: Formats user attribution in comments