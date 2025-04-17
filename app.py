from flask import Flask, jsonify, request, render_template, send_from_directory, send_file
from flask_cors import CORS
import threading
import time
import json
import os
import logging
from datetime import datetime
import io
import sys
from math import ceil

# Import KomootGPX modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from komoot_adapter import KomootAdapter

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)  # Enable CORS for all routes

# Add an error handler for all exceptions
@app.errorhandler(Exception)
def handle_error(e):
    app.logger.error(f"Unhandled exception: {str(e)}")
    return jsonify({"error": str(e)}), 500

# Global variables to track KomootGPX state
komoot_status = {
    'status': 'idle',  # 'idle', 'running', 'completed', 'error', 'chunk_completed'
    'progress': 0.0,
    'tours_completed': 0,
    'tours_found': 0,
    'error': None,
    'log': [],
    'results': [],
    'next_chunk': 0
}

# Lock for thread-safe updates to komoot_status
status_lock = threading.Lock()

# Komoot Adapter instance
komoot_adapter = KomootAdapter()

def add_log_entry(message):
    """Add a timestamped entry to the log"""
    with status_lock:
        now = datetime.now().strftime('%H:%M:%S')
        entry = f"[{now}] {message}"
        komoot_status['log'].append(entry)
        logger.info(message)

def reset_status():
    """Reset the komoot status to initial state"""
    with status_lock:
        komoot_status['status'] = 'idle'
        komoot_status['progress'] = 0.0
        komoot_status['tours_completed'] = 0
        komoot_status['tours_found'] = 0
        komoot_status['error'] = None
        komoot_status['log'] = []
        komoot_status['results'] = []  # Reset results when starting a new fetch
        komoot_status['next_chunk'] = 0

def fetch_komoot_tours_thread(email, password, anonymous, tour_selection, filter_type, 
                             include_poi, output_dir, skip_existing, id_filename,
                             add_date, max_title_length, max_desc_length,
                             download_images=False, image_output_dir=None,
                             chunk_start=0, chunk_size=None):
    """Runs the KomootGPX process in a background thread with chunking support"""
    try:
        with status_lock:
            komoot_status['status'] = 'running'
        
        # Log start
        add_log_entry(f"Starting Komoot GPX downloader")
        
        if not anonymous:
            add_log_entry(f"Logging in as {email}...")
            try:
                komoot_adapter.login(email, password)
                add_log_entry(f"Logged in successfully as {komoot_adapter.get_display_name()}")
            except Exception as e:
                add_log_entry(f"Login failed: {str(e)}")
                with status_lock:
                    komoot_status['status'] = 'error'
                    komoot_status['error'] = f"Login failed: {str(e)}"
                return
            
            # Handle range format in tour_selection
            # Format can be "start:count" to process a range of tours
            range_mode = False
            range_start = 0
            range_end = 0
            
            if isinstance(tour_selection, str) and ":" in tour_selection:
                try:
                    range_parts = tour_selection.split(":")
                    if len(range_parts) == 2:
                        range_start = int(range_parts[0])
                        range_count = int(range_parts[1])
                        range_end = range_start + range_count - 1
                        range_mode = True
                        add_log_entry(f"Processing tour range from index {range_start} to {range_end}")
                except Exception as e:
                    add_log_entry(f"Error processing range format: {str(e)}. Treating as single tour.")
            
            # Fetch all tours if requested
            if tour_selection == "all" or range_mode:
                add_log_entry(f"Fetching tours (filter: {filter_type})...")
                all_tours = komoot_adapter.fetch_tours(filter_type)
                
                # Convert to list for easier chunking
                tour_list = list(all_tours.items())
                total_tours = len(tour_list)
                
                with status_lock:
                    komoot_status['tours_found'] = total_tours
                
                # If range mode, select the specific range
                if range_mode:
                    if range_start < total_tours:
                        range_end = min(range_end, total_tours - 1)
                        add_log_entry(f"Processing tour index range {range_start} to {range_end} of {total_tours} total tours")
                        tour_list = tour_list[range_start:range_end + 1]
                    else:
                        add_log_entry(f"Error: Start index {range_start} is out of range (max: {total_tours - 1})")
                        with status_lock:
                            komoot_status['status'] = 'error'
                            komoot_status['error'] = f"Start index {range_start} is out of range"
                        return
                # Apply chunking if specified and not in range mode
                elif chunk_size is not None and not range_mode:
                    chunk_end = min(chunk_start + chunk_size, total_tours)
                    add_log_entry(f"Processing chunk {chunk_start+1}-{chunk_end} of {total_tours} tours")
                    tour_list = tour_list[chunk_start:chunk_end]
                
                add_log_entry(f"Processing {len(tour_list)} tours in this batch")
                
                # Calculate the offset for progress tracking
                offset = range_start if range_mode else chunk_start
                
                # Process each tour in the chunk
                for i, (tour_id, tour) in enumerate(tour_list, 1):
                    tour_id_str = str(tour_id)
                    
                    add_log_entry(f"Processing tour {offset + i}/{total_tours}: {tour['name']} (ID: {tour_id_str})")
                    
                    try:
                        # Generate GPX for this tour
                        gpx_content = komoot_adapter.make_gpx(
                            tour_id_str, output_dir, include_poi, skip_existing, 
                            tour, add_date, max_title_length, max_desc_length,
                            return_content=True
                        )
                        
                        # Download images if requested
                        image_paths = []
                        if download_images and image_output_dir:
                            try:
                                add_log_entry(f"Downloading images for tour {tour_id_str}...")
                                image_paths = komoot_adapter.download_tour_images(tour_id_str, tour, image_output_dir)
                                add_log_entry(f"Downloaded {len(image_paths)} images for tour {tour_id_str}")
                            except Exception as img_err:
                                add_log_entry(f"Error downloading images for tour {tour_id_str}: {str(img_err)}")
                        
                        # Add to results if content was returned
                        if gpx_content:
                            filename = komoot_adapter.get_last_filename()
                            with status_lock:
                                komoot_status['results'].append({
                                    'id': tour_id_str,
                                    'name': tour['name'],
                                    'sport': tour['sport'],
                                    'type': tour['type'],
                                    'distance_km': str(int(tour['distance']) / 1000.0),
                                    'duration': str(round(tour['duration'] / 3600.0, 2)),
                                    'elevation_up': tour['elevation_up'],
                                    'elevation_down': tour['elevation_down'],
                                    'date': tour['date'][:10] if 'date' in tour else '',
                                    'filename': filename,
                                    'url': f"https://www.komoot.com/tour/{tour_id_str}",
                                    'images': image_paths
                                })
                    except Exception as e:
                        add_log_entry(f"Error processing tour {tour_id_str}: {str(e)}")
                    
                    # Update progress
                    with status_lock:
                        komoot_status['tours_completed'] = offset + i
                        komoot_status['progress'] = (offset + i) / total_tours
            
            else:
                # Process a single tour
                tour_id_str = str(tour_selection)
                add_log_entry(f"Processing single tour: {tour_id_str}")
                
                try:
                    # Check if the tour exists in user's tours
                    tours = komoot_adapter.fetch_tours(filter_type, silent=True)
                    tour = None
                    
                    if int(tour_id_str) in tours:
                        tour = tours[int(tour_id_str)]
                        add_log_entry(f"Found tour in your profile: {tour['name']}")
                    
                    # Generate GPX for this tour
                    gpx_content = komoot_adapter.make_gpx(
                        tour_id_str, output_dir, include_poi, False, 
                        tour, add_date, max_title_length, max_desc_length,
                        return_content=True
                    )
                    
                    # Download images if requested
                    image_paths = []
                    if download_images and image_output_dir:
                        try:
                            add_log_entry(f"Downloading images for tour {tour_id_str}...")
                            image_paths = komoot_adapter.download_tour_images(tour_id_str, tour, image_output_dir)
                            add_log_entry(f"Downloaded {len(image_paths)} images for tour {tour_id_str}")
                        except Exception as img_err:
                            add_log_entry(f"Error downloading images for tour {tour_id_str}: {str(img_err)}")
                    
                    if gpx_content:
                        filename = komoot_adapter.get_last_filename()
                        with status_lock:
                            komoot_status['tours_found'] = 1
                            komoot_status['tours_completed'] = 1
                            komoot_status['progress'] = 1.0
                            
                            # Get tour details from the fetched tour
                            tour_fetched = komoot_adapter.get_last_tour()
                            
                            komoot_status['results'].append({
                                'id': tour_id_str,
                                'name': tour_fetched['name'],
                                'sport': tour_fetched['sport'],
                                'type': tour_fetched['type'],
                                'distance_km': str(int(tour_fetched['distance']) / 1000.0),
                                'duration': str(round(tour_fetched['duration'] / 3600.0, 2)),
                                'elevation_up': tour_fetched['elevation_up'],
                                'elevation_down': tour_fetched['elevation_down'],
                                'date': tour_fetched['date'][:10] if 'date' in tour_fetched else '',
                                'filename': filename,
                                'url': f"https://www.komoot.com/tour/{tour_id_str}",
                                'images': image_paths
                            })
                except Exception as e:
                    add_log_entry(f"Error processing tour {tour_id_str}: {str(e)}")
                    with status_lock:
                        komoot_status['status'] = 'error'
                        komoot_status['error'] = str(e)
                    return
        
        else:
            # Anonymous mode - only single tour processing is supported
            tour_id_str = str(tour_selection)
            add_log_entry(f"Anonymous mode: Processing tour {tour_id_str}")
            
            try:
                # Generate GPX for this tour
                gpx_content = komoot_adapter.make_gpx(
                    tour_id_str, output_dir, include_poi, False, 
                    None, add_date, max_title_length, max_desc_length,
                    return_content=True, anonymous=True
                )
                
                if gpx_content:
                    filename = komoot_adapter.get_last_filename()
                    with status_lock:
                        komoot_status['tours_found'] = 1
                        komoot_status['tours_completed'] = 1
                        komoot_status['progress'] = 1.0
                        
                        # Get tour details from the fetched tour
                        tour_fetched = komoot_adapter.get_last_tour()
                        
                        komoot_status['results'].append({
                            'id': tour_id_str,
                            'name': tour_fetched['name'],
                            'sport': tour_fetched['sport'],
                            'type': tour_fetched['type'],
                            'distance_km': str(int(tour_fetched['distance']) / 1000.0),
                            'duration': str(round(tour_fetched['duration'] / 3600.0, 2)),
                            'elevation_up': tour_fetched['elevation_up'],
                            'elevation_down': tour_fetched['elevation_down'],
                            'date': tour_fetched['date'][:10] if 'date' in tour_fetched else '',
                            'filename': filename,
                            'url': f"https://www.komoot.com/tour/{tour_id_str}",
                            'images': []
                        })
            except Exception as e:
                add_log_entry(f"Error processing tour {tour_id_str}: {str(e)}")
                with status_lock:
                    komoot_status['status'] = 'error'
                    komoot_status['error'] = str(e)
                return
        
        # Mark as completed
        with status_lock:
            if (tour_selection == "all" and chunk_size is not None and not range_mode):
                # If this was a chunk and there's more to process, mark as "chunk_completed"
                if chunk_start + len(tour_list) < komoot_status['tours_found']:
                    komoot_status['status'] = 'chunk_completed'
                    komoot_status['next_chunk'] = chunk_start + len(tour_list)
                else:
                    komoot_status['status'] = 'completed'
                    komoot_status['progress'] = 1.0
            else:
                komoot_status['status'] = 'completed'
                komoot_status['progress'] = 1.0
        
        add_log_entry(f"Process completed successfully. Downloaded {len(komoot_status['results'])} tours.")
        
    except Exception as e:
        # Log any errors
        error_msg = str(e)
        logger.error(f"Error during Komoot GPX processing: {error_msg}")
        add_log_entry(f"Error: {error_msg}")
        
        # Update status to error
        with status_lock:
            komoot_status['status'] = 'error'
            komoot_status['error'] = error_msg

# Routes
@app.route('/')
def index():
    """Serve the main HTML page"""
    return render_template('index.html')

@app.route('/api/tour-counts', methods=['POST'])
def get_tour_counts():
    """Get the count of tours for planning chunked downloads"""
    try:
        # Get parameters from request
        data = request.json
        if data is None:
            logger.error("No JSON data received in request")
            return jsonify({'error': 'No JSON data received'}), 400
            
        email = data.get('email', '')
        password = data.get('password', '')
        filter_type = data.get('filterType', 'all')
        
        logger.info(f"Counting tours for user {email}, filter: {filter_type}")
        
        # Translate filter type to KomootGPX format
        if filter_type == "planned":
            filter_type = "tour_planned"
        elif filter_type == "recorded":
            filter_type = "tour_recorded"
        
        # Validate input
        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400
            
        # Login and fetch tours
        try:
            logger.info("Logging in to Komoot...")
            komoot_adapter.login(email, password)
            logger.info("Logged in successfully, fetching tours...")
            
            tours = komoot_adapter.fetch_tours(filter_type)
            logger.info(f"Found {len(tours)} tours")
            
            return jsonify({
                'total_tours': len(tours),
                'success': True
            })
            
        except Exception as e:
            logger.error(f"Error in Komoot operations: {str(e)}")
            return jsonify({'error': f'Error fetching tour counts: {str(e)}'}), 500
        
    except Exception as e:
        logger.error(f"Error getting tour counts: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/start', methods=['POST'])
def start_processing():
    """Start the Komoot GPX processing"""
    try:
        # Get parameters from request
        data = request.json
        if data is None:
            logger.error("No JSON data received in request")
            return jsonify({'error': 'No JSON data received'}), 400
            
        email = data.get('email', '')
        password = data.get('password', '')
        anonymous = data.get('anonymous', False)
        tour_selection = data.get('tourSelection', '')
        filter_type = data.get('filterType', 'all')
        include_poi = not data.get('noPoi', False)
        output_dir = data.get('outputDir', 'static/exports/gpx')
        skip_existing = data.get('skipExisting', False)
        id_filename = data.get('idFilename', False)
        add_date = data.get('addDate', False)
        max_title_length = data.get('maxTitleLength', -1)
        max_desc_length = data.get('maxDescLength', -1)
        download_images = data.get('downloadImages', False)
        
        # Chunking parameters
        chunk_start = data.get('chunkStart', 0)
        chunk_size = data.get('chunkSize', None)
        
        # Translate filter type to KomootGPX format
        if filter_type == "planned":
            filter_type = "tour_planned"
        elif filter_type == "recorded":
            filter_type = "tour_recorded"
        
        # Validate input
        if anonymous and tour_selection == "all":
            return jsonify({'error': 'Cannot get all user\'s routes in anonymous mode, use a specific Tour ID'}), 400
            
        if anonymous and (email or password):
            return jsonify({'error': 'Cannot specify login/password in anonymous mode'}), 400
            
        if not tour_selection:
            return jsonify({'error': 'No tour selection provided (ID or "all")'}), 400
            
        if not anonymous and not email:
            return jsonify({'error': 'Email is required for non-anonymous mode'}), 400
            
        if not anonymous and not password:
            return jsonify({'error': 'Password is required for non-anonymous mode'}), 400
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Create images directory if needed
        image_output_dir = None
        if download_images:
            image_output_dir = os.path.join(os.path.dirname(output_dir), 'images')
            os.makedirs(image_output_dir, exist_ok=True)
        
        # If this is a new processing job (not a continued chunk), reset the status
        if chunk_start == 0:
            reset_status()
        
        # Start processing in a background thread
        threading.Thread(
            target=fetch_komoot_tours_thread,
            args=(email, password, anonymous, tour_selection, filter_type, 
                 include_poi, output_dir, skip_existing, id_filename, 
                 add_date, max_title_length, max_desc_length, 
                 download_images, image_output_dir,
                 chunk_start, chunk_size)
        ).start()
        
        return jsonify({'success': True, 'message': 'Processing started'})
        
    except Exception as e:
        logger.error(f"Error starting processing: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def get_status():
    """Get the current processing status"""
    with status_lock:
        # Return a copy of the status to avoid concurrent modification
        status_copy = {
            'status': komoot_status['status'],
            'progress': komoot_status['progress'],
            'tours_completed': komoot_status['tours_completed'],
            'tours_found': komoot_status['tours_found'],
            'error': komoot_status['error'],
            'next_chunk': komoot_status['next_chunk'],
            # Only return new log entries since last request
            'log': komoot_status['log'][-10:] if len(komoot_status['log']) > 10 else komoot_status['log']
        }
    
    return jsonify(status_copy)

@app.route('/api/results')
def get_results():
    """Get the processing results"""
    with status_lock:
        # Return results if any are available
        if len(komoot_status['results']) == 0:
            return jsonify({'error': 'No results available yet'}), 400
        
        return jsonify(komoot_status['results'])

@app.route('/api/download/<tour_id>')
def download_gpx(tour_id):
    """Download a specific GPX file"""
    try:
        # Find the tour in results
        tour = None
        with status_lock:
            for t in komoot_status['results']:
                if t['id'] == tour_id:
                    tour = t
                    break
        
        if not tour:
            return jsonify({'error': f'Tour with ID {tour_id} not found in results'}), 404
            
        # Path to the GPX file
        gpx_file = os.path.join('static/exports/gpx', tour['filename'])
        
        if not os.path.exists(gpx_file):
            return jsonify({'error': f'GPX file for tour {tour_id} not found'}), 404
        
        # Return the file for download
        return send_file(
            gpx_file,
            as_attachment=True,
            download_name=tour['filename'],
            mimetype='application/gpx+xml'
        )
    
    except Exception as e:
        logger.error(f"Error downloading GPX: {e}")
        return jsonify({'error': f'Error downloading GPX file: {str(e)}'}), 500

@app.route('/api/download-image/<path:image_path>')
def download_image(image_path):
    """Download a specific tour image"""
    try:
        # The image path should be relative to the static directory
        full_path = os.path.join('static/exports/images', image_path)
        
        if not os.path.exists(full_path):
            return jsonify({'error': f'Image file not found: {image_path}'}), 404
        
        # Get the directory and filename
        directory = os.path.dirname(full_path)
        filename = os.path.basename(full_path)
        
        # Return the file for download
        return send_from_directory(
            directory,
            filename,
            as_attachment=True
        )
    
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
        return jsonify({'error': f'Error downloading image file: {str(e)}'}), 500

@app.route('/api/export/all')
def export_all_gpx():
    """Create a zip file with all GPX files"""
    try:
        import zipfile
        
        # Create a zip file in memory
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            with status_lock:
                for tour in komoot_status['results']:
                    gpx_file = os.path.join('static/exports/gpx', tour['filename'])
                    if os.path.exists(gpx_file):
                        zf.write(gpx_file, tour['filename'])
                        
                        # Include images if any
                        if 'images' in tour and tour['images']:
                            # Create a subdirectory for each tour's images
                            tour_img_dir = os.path.splitext(tour['filename'])[0]
                            for img_path in tour['images']:
                                full_img_path = os.path.join('static/exports/images', img_path)
                                if os.path.exists(full_img_path):
                                    # Add to zip with a path that includes tour name subdirectory
                                    zf.write(full_img_path, os.path.join(tour_img_dir, os.path.basename(img_path)))
        
        # Reset file pointer
        memory_file.seek(0)
        
        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Return the zip file
        return send_file(
            memory_file,
            as_attachment=True,
            download_name=f'komoot_gpx_export_{timestamp}.zip',
            mimetype='application/zip'
        )
    
    except Exception as e:
        logger.error(f"Error creating zip archive: {e}")
        return jsonify({'error': f'Error creating zip archive: {str(e)}'}), 500

@app.route('/api/export/images/<tour_id>')
def export_tour_images(tour_id):
    """Create a zip file with all images for a specific tour"""
    try:
        import zipfile
        
        # Find the tour in results
        tour = None
        with status_lock:
            for t in komoot_status['results']:
                if t['id'] == tour_id:
                    tour = t
                    break
        
        if not tour:
            return jsonify({'error': f'Tour with ID {tour_id} not found in results'}), 404
            
        if not tour.get('images'):
            return jsonify({'error': f'No images found for tour {tour_id}'}), 404
            
        # Create a zip file in memory
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for img_path in tour['images']:
                full_img_path = os.path.join('static/exports/images', img_path)
                if os.path.exists(full_img_path):
                    zf.write(full_img_path, os.path.basename(img_path))
        
        # Reset file pointer
        memory_file.seek(0)
        
        # Generate filename
        filename = f"{tour['name'].replace(' ', '_')}_images.zip"
        
        # Return the zip file
        return send_file(
            memory_file,
            as_attachment=True,
            download_name=filename,
            mimetype='application/zip'
        )
    
    except Exception as e:
        logger.error(f"Error creating image zip archive: {e}")
        return jsonify({'error': f'Error creating image zip archive: {str(e)}'}), 500

@app.route('/api/clear')
def clear_data():
    """Clear all processed data"""
    with status_lock:
        if komoot_status['status'] == 'running':
            return jsonify({'error': 'Cannot clear data while processing is in progress'}), 400
        
        # Reset results
        komoot_status['results'] = []
        komoot_status['tours_found'] = 0
        komoot_status['tours_completed'] = 0
        
    return jsonify({'success': True, 'message': 'Data cleared'})

if __name__ == '__main__':
    # Create directories if they don't exist
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(templates_dir, exist_ok=True)
    
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    os.makedirs(static_dir, exist_ok=True)
    
    exports_dir = os.path.join(static_dir, 'exports')
    os.makedirs(exports_dir, exist_ok=True)
    
    gpx_dir = os.path.join(exports_dir, 'gpx')
    os.makedirs(gpx_dir, exist_ok=True)
    
    img_dir = os.path.join(exports_dir, 'images')
    os.makedirs(img_dir, exist_ok=True)
    
    # Start the Flask app
    app.run(host='0.0.0.0', port=5001, debug=True)