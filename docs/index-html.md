# index.html - User Interface Documentation

## Overview

The `index.html` file provides the user interface for the komoot-takeout tool. It's a single-page application with multiple tabs that allows users to download tours and collections from Komoot. The UI is built with HTML, CSS, and JavaScript, using Tailwind CSS for styling.

## Key Components

### Layout Structure

The interface is organized into the following sections:

- **Header**: Application title and GitHub link
- **Tab Navigation**: Switches between Tours and Collections views
- **Tours Tab**: Interface for downloading individual or batch tours
- **Collections Tab**: Interface for managing and downloading collections
- **Floating Progress**: Shows real-time status during operations

### Tours Tab Features

The Tours tab provides functionality for downloading individual or batch tours:

- **Authentication**: Email/password login or anonymous mode (limited to single tour)
- **Tour Selection**: Options for downloading all tours or a specific tour by ID
- **Tour Filtering**: Ability to filter tours by type (all, recorded, planned)
- **Output Directory**: User-selectable download location with native folder browsing
- **Advanced Options**: Configurable GPX options (POIs, file naming, metadata) and chunking settings
- **Status Display**: Real-time progress tracking and log output
- **Results Table**: Displays downloaded tours with metadata (name, date, type, distance, duration, elevation)

### Collections Tab Features

The Collections tab handles collection management and bulk downloads:

- **Collection Input**: URL input for public collections
- **Collection List**: Displays scraped collections with metadata (name, ID, slug, tour count)
- **Enhancement Status**: Visual indicators showing if collections have basic or enhanced metadata
- **Collection Enhancement**: Dedicated feature to enrich basic collection data with detailed information
- **Batch Selection**: Multi-select capability for processing multiple collections simultaneously
- **Customizable Downloads**: Options for metadata inclusion and GPX formatting
- **Status Tracking**: Real-time progress indicators for both scraping and downloading operations

## Cross-Platform Integration

The UI detects if it's running in a desktop application context (via PyWebView):

```javascript
// Check if running in pywebview
const isPywebview = window.pywebview !== undefined;

// If running in pywebview, show desktop-only elements
if (isPywebview) {
    document.querySelectorAll('.desktop-only').forEach(el => {
        el.style.display = 'block';
    });
}
```

This enables platform-specific features like:
- Native folder selection dialogs via PyWebView API
- Direct file system access for selecting output directories
- "Open Folder" capabilities for viewing downloaded content
- Desktop-specific UI elements and indicators

### Folder Selection Implementation

The application uses a unified folder selection mechanism that works differently based on context:

```javascript
// Function to fetch and update the selected folder
function updateSelectedFolder() {
    fetch('/api/selected-folder')
        .then(response => response.json())
        .then(data => {
            const outputDirInput = document.getElementById('output-dir');
            const collectionsOutputDirInput = document.getElementById('collections-output-dir');
            
            if (outputDirInput) outputDirInput.value = data.path;
            if (collectionsOutputDirInput) collectionsOutputDirInput.value = data.path;
            
            // Display the folder selection elements if pywebview is available
            document.querySelectorAll('.folder-selection-btn').forEach(btn => {
                btn.style.display = isPywebview ? 'inline-block' : 'none';
            });
        });
}
```

## Collection Data Management

### Slug Extraction

The UI includes sophisticated handling of collection slugs:

```javascript
// Function to extract slug from URL or collection name
function extractSlug(url, name) {
    // Try to extract slug from URL first
    if (url) {
        const match = url.match(/\/collection\/\d+\/?-?([a-z0-9-]+)?/);
        if (match && match[1]) {
            return match[1];
        }
    }
    
    // If no URL or no slug in URL, generate slug from name
    if (name) {
        return name.toLowerCase()
            .replace(/[^\w\s-]/g, '') // Remove special characters
            .replace(/\s+/g, '-')     // Replace spaces with hyphens
            .replace(/-+/g, '-')      // Remove consecutive hyphens
            .substring(0, 50);        // Limit length
    }
    
    return "";
}
```

### Enhancement Status

Collections have enhancement status indicators that show whether they contain basic or detailed metadata:

- **Basic**: Collections with minimal information (ID, name, URL)
- **Enhanced**: Collections with complete metadata (distance, elevation, descriptions, etc.)

The enhancement process allows users to upgrade basic collections to enhanced status.

## Real-Time Updates

The interface uses polling to provide real-time status updates:

- Background status checking via interval timers for both tours and collections
- Live log display with auto-scrolling
- Progress bar updates with completion percentages
- Floating status indicator for long-running operations
- Separate status tracking for different operation types

## Error Handling

The application implements comprehensive error handling:

- User-friendly error messages via alerts
- Console logging for debugging purposes
- Status messages in the UI for process failures
- Graceful recovery from failed API calls
- Timeout handling for long-running operations

## JavaScript Architecture

The JavaScript implements a clean separation of concerns:

1. **Event Listeners**: Handle user interactions (button clicks, checkbox changes)
2. **API Integration**: Communicates with backend Flask endpoints using fetch API
3. **UI Updates**: Manages dynamic content and state changes (showing/hiding sections)
4. **Status Monitoring**: Tracks and displays progress for both tours and collections
5. **Cross-Platform Adaptation**: Adjusts behavior based on execution environment
6. **Data Management**: Tracks selection state and maintains consistency

## Key API Endpoints

The UI interacts with several backend endpoints:

- `/api/tour-counts`: Count available tours
- `/api/start`: Begin tour export process
- `/api/status`: Check tour export progress
- `/api/results`: Retrieve tour export results
- `/api/collections/public`: Scrape public collections
- `/api/collections-status`: Check collection operation status
- `/api/collections-results`: Get collection results
- `/api/enhance-collections`: Start collection enhancement
- `/api/download-collection-tours`: Download tours from collections
- `/api/select-folder`: Set output directory
- `/api/selected-folder`: Get current output directory