# Copilot Instructions for komoot-takeout Project

## Project Identity
- **Project Name**: komoot-takeout
- **Purpose**: Desktop application for downloading Komoot Tours & Collections
- **Core Functionality**: Batch downloading of GPX files and Collections from Komoot
- **Target Platforms**: Windows 11 Pro & macOS 15.x.x (Apple silicon)

## Technical Architecture

### Core Stack
- **Backend**: Flask 3.1.0
- **Desktop Framework**: PyWebView 5.4
- **Packager**: PyInstaller 6.13.0
- **Key Libraries**:
  - komootGPX (core functionality)
  - Beautiful Soup 4.13.4 (web scraping)
  - gpxpy 1.6.2 (GPX handling)

### Project Structure
```
komoot-takeout/
├── app.py                 # Flask application core
├── pywebview_app.py       # Desktop window wrapper
├── komoot_adapter.py      # Komoot API integration
├── build_app.py          # Executable build script
├── templates/
│   └── index.html        # Main UI template
├── static/               # Static assets
└── requirements.txt      # Dependencies
```

## Development Guidelines

### Flask Backend Considerations
1. **Routes**:
   - Design RESTful endpoints for GPX downloads
   - Handle Collection management
   - Implement error handling for network issues
   - Use appropriate HTTP status codes

2. **File Operations**:
   - Handle downloads in user-selected directories
   - Manage file permissions across platforms
   - Implement proper error handling for I/O operations

### PyWebView Integration
1. **Window Management**:
   - Single window application
   - Native OS integration
   - Handle window events properly
   - Manage application lifecycle

2. **Cross-Platform Compatibility**:
   - Use platform-agnostic paths
   - Handle OS-specific file operations
   - Consider different window behaviors

### Data Handling
1. **GPX Processing**:
   - Implement proper GPX file validation
   - Handle large batch downloads
   - Manage concurrent downloads

2. **Collections Management**:
   - Implement collection parsing
   - Handle collection metadata
   - Manage nested data structures

## Code Patterns

### Error Handling
```python
try:
    # File operations, network requests, etc.
    pass
except IOError as e:
    logger.error(f"File operation failed: {e}")
    # Handle user feedback
except NetworkError as e:
    logger.error(f"Network operation failed: {e}")
    # Handle user feedback
```

### File Operations
```python
import os
from pathlib import Path

def ensure_download_directory(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    return path

def get_default_download_dir():
    return str(Path.home() / "komoot-takeout")
```

### API Integration
```python
def download_gpx(tour_id):
    try:
        # Implement download logic
        pass
    except Exception as e:
        logger.error(f"Download failed for tour {tour_id}: {e}")
        raise
```

## Testing Considerations
1. **Unit Tests**:
   - Test GPX file handling
   - Test collection parsing
   - Test file system operations

2. **Integration Tests**:
   - Test Komoot API integration
   - Test batch download functionality
   - Test UI-backend communication

## Build Process
1. **PyInstaller Configuration**:
   - Include all required assets
   - Handle platform-specific requirements
   - Manage hidden imports

2. **Platform-Specific Builds**:
   - Windows: Use semicolon for path separation
   - macOS: Use colon for path separation
   - Include all necessary DLLs and dependencies

## Logging
```python
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# File handler
fh = logging.FileHandler('komoot_exporter.log')
fh.setLevel(logging.DEBUG)
```

## Security Considerations
1. **File System**:
   - Validate all file paths
   - Handle permissions properly
   - Sanitize user inputs

2. **Network**:
   - Handle SSL/TLS properly
   - Implement proper timeout handling
   - Validate server responses

## Best Practices
1. Use pathlib for cross-platform path handling
2. Implement proper error handling and user feedback
3. Follow Flask application factory pattern
4. Use async operations for long-running tasks
5. Implement proper cleanup on application exit

## License Compliance
- Project is under GPL-3.0
- Ensure all contributions comply with license
- Maintain appropriate attribution

Remember to handle:
- Cross-platform path separators
- OS-specific file operations
- Proper error messages and logging
- Resource cleanup
- User feedback during long operations