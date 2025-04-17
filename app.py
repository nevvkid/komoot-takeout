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

# Import KomootGPX modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from komoot_adapter import KomootAdapter

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)  # Enable CORS for all routes

# Global variables to track KomootGPX state
komoot_status = {
    'status': 'idle',  # 'idle', 'running', 'completed', 'error'
    'progress': 0.0,
    'tours_completed': 0,
    'tours_found': 0,
    'error': None,
    'log': [],
    'results': []
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

def fetch_komoot_tours_thread(email, password, anonymous, tour_selection, filter_type, 
                             include_poi, output_dir, skip_existing, id_filename,
                             add_date, max_title_length, max_desc_length):
    """Runs the KomootGPX process in a background thread"""
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
            
            # Fetch all tours if requested
            if tour_selection == "all":
                add_log_entry(f"Fetching all tours (filter: {filter_type})...")
                tours = komoot_adapter.fetch_tours(filter_type)
                
                with status_lock:
                    komoot_status['tours_found'] = len(tours)
                    
                add_log_entry(f"Found {len(tours)} tours")
                
                # Process each tour
                total_tours = len(tours)
                for i, (tour_id, tour) in enumerate(tours.items(), 1):
                    tour_id_str = str(tour_id)
                    
                    add_log_entry(f"Processing tour {i}/{total_tours}: {tour['name']} (ID: {tour_id_str})")
                    
                    try:
                        # Generate GPX for this tour
                        gpx_content = komoot_adapter.make_gpx(
                            tour_id_str, output_dir, include_poi, skip_existing, 
                            tour, add_date, max_title_length, max_desc_length,
                            return_content=True
                        )
                        
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
                                    'url': f"https://www.komoot.com/tour/{tour_id_str}"
                                })
                    except Exception as e:
                        add_log_entry(f"Error processing tour {tour_id_str}: {str(e)}")
                    
                    # Update progress
                    with status_lock:
                        komoot_status['tours_completed'] = i
                        komoot_status['progress'] = i / total_tours
            
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
                                'url': f"https://www.komoot.com/tour/{tour_id_str}"
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
                            'url': f"https://www.komoot.com/tour/{tour_id_str}"
                        })
            except Exception as e:
                add_log_entry(f"Error processing tour {tour_id_str}: {str(e)}")
                with status_lock:
                    komoot_status['status'] = 'error'
                    komoot_status['error'] = str(e)
                return
        
        # Mark as completed
        with status_lock:
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

@app.route('/api/start', methods=['POST'])
def start_processing():
    """Start the Komoot GPX processing"""
    try:
        # Get parameters from request
        data = request.json
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
        
        # Reset status for new processing job
        reset_status()
        
        # Start processing in a background thread
        threading.Thread(
            target=fetch_komoot_tours_thread,
            args=(email, password, anonymous, tour_selection, filter_type, 
                 include_poi, output_dir, skip_existing, id_filename, 
                 add_date, max_title_length, max_desc_length)
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

@app.route('/api/list-tours', methods=['POST'])
def list_tours():
    """List all tours for a user"""
    try:
        # Get parameters from request
        data = request.json
        email = data.get('email', '')
        password = data.get('password', '')
        filter_type = data.get('filterType', 'all')
        
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
            komoot_adapter.login(email, password)
            tours = komoot_adapter.fetch_tours(filter_type)
            
            # Convert to list for JSON
            tour_list = []
            for tour_id, tour in tours.items():
                tour_list.append({
                    'id': str(tour_id),
                    'name': tour['name'],
                    'sport': tour['sport'],
                    'type': tour['type'],
                    'distance_km': str(int(tour['distance']) / 1000.0),
                    'date': tour['date'][:10] if 'date' in tour else ''
                })
            
            return jsonify(tour_list)
            
        except Exception as e:
            return jsonify({'error': f'Error fetching tours: {str(e)}'}), 500
        
    except Exception as e:
        logger.error(f"Error listing tours: {str(e)}")
        return jsonify({'error': str(e)}), 500

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
    
    # Start the Flask app
    app.run(host='0.0.0.0', port=5001, debug=True)