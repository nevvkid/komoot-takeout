import os
import io
import logging
import requests
import time
from datetime import datetime
from urllib.parse import urlparse
from komootgpx.api import KomootApi
from komootgpx.gpxcompiler import GpxCompiler

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KomootAdapter:
    """
    Adapter class to integrate KomootGPX with Flask
    """
    def __init__(self):
        self.api = KomootApi()
        self.last_filename = None
        self.last_tour = None
        self.user_display_name = None
        
    def login(self, email, password):
        """Login to Komoot"""
        try:
            logger.info(f"Logging in to Komoot as {email}")
            self.api.login(email, password)
            # Save display name for future reference
            if hasattr(self.api, 'user_display_name'):
                self.user_display_name = self.api.user_display_name
            else:
                # Try to extract display name from API response if possible
                self.user_display_name = "Komoot User"
            logger.info(f"Login successful as {self.get_display_name()}")
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            raise Exception(f"Komoot login failed: {str(e)}")
    
    def get_display_name(self):
        """Get the display name of the logged-in user"""
        if self.user_display_name:
            return self.user_display_name
        return "Unknown User"
    
    def fetch_tours(self, tour_type="all", silent=False):
        """Fetch tours from Komoot"""
        try:
            logger.info(f"Fetching tours with filter: {tour_type}, silent: {silent}")
            tours = self.api.fetch_tours(tourType=tour_type, silent=silent)
            logger.info(f"Successfully fetched {len(tours)} tours")
            return tours
        except Exception as e:
            logger.error(f"Error fetching tours: {str(e)}")
            raise Exception(f"Failed to fetch tours: {str(e)}")
    
    def fetch_tour(self, tour_id, retries=3):
        """Fetch a tour with retry capability"""
        attempt = 0
        last_error = None
        
        while attempt < retries:
            try:
                logger.info(f"Fetching tour {tour_id} (attempt {attempt+1}/{retries})")
                tour = self.api.fetch_tour(str(tour_id))
                logger.info(f"Successfully fetched tour {tour_id}")
                return tour
            except Exception as e:
                last_error = e
                logger.error(f"Error fetching tour (attempt {attempt+1}/{retries}): {str(e)}")
                attempt += 1
                time.sleep(1)  # Wait between retries
        
        # If we got here, all retries failed
        raise Exception(f"Failed to fetch tour after {retries} attempts: {last_error}")
    
    def download_tour_images(self, tour_id, tour=None, output_dir='static/exports/images'):
        """
        Download images associated with a tour
        
        Args:
            tour_id: The ID of the tour
            tour: Tour data if already fetched
            output_dir: Directory to save images
            
        Returns:
            List of relative paths to downloaded images
        """
        try:
            # Create output directory if it doesn't exist
            os.makedirs(output_dir, exist_ok=True)
            
            if tour is None:
                tour = self.fetch_tour(tour_id)
            
            image_paths = []
            
            # Check if there are photos in the tour
            if '_embedded' in tour and 'timeline' in tour['_embedded'] and '_embedded' in tour['_embedded']['timeline']:
                timeline_items = tour['_embedded']['timeline']['_embedded']['items']
                
                for item in timeline_items:
                    # Look for photos
                    if item['type'] == 'photo' and '_embedded' in item and 'reference' in item['_embedded']:
                        photo_ref = item['_embedded']['reference']
                        
                        # Get image URL
                        if 'src' in photo_ref:
                            image_url = photo_ref['src']
                            
                            # Extract filename from URL or generate one
                            parsed_url = urlparse(image_url)
                            filename = os.path.basename(parsed_url.path)
                            if not filename or '.' not in filename:
                                # Generate a filename if URL doesn't provide a good one
                                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                                filename = f"tour_{tour_id}_photo_{timestamp}.jpg"
                            
                            # Create a subdirectory for this tour
                            tour_img_dir = os.path.join(output_dir, str(tour_id))
                            os.makedirs(tour_img_dir, exist_ok=True)
                            
                            # Full path to save the image
                            image_path = os.path.join(tour_img_dir, filename)
                            
                            # Download the image
                            try:
                                logger.info(f"Downloading image: {image_url}")
                                response = requests.get(image_url, stream=True, timeout=10)
                                if response.status_code == 200:
                                    with open(image_path, 'wb') as f:
                                        for chunk in response.iter_content(1024):
                                            f.write(chunk)
                                    
                                    # Add relative path to the list
                                    rel_path = os.path.join(str(tour_id), filename)
                                    image_paths.append(rel_path)
                                    logger.info(f"Image saved to {image_path}")
                                else:
                                    logger.warning(f"Failed to download image: {image_url}, status code: {response.status_code}")
                            except Exception as e:
                                logger.error(f"Error downloading image {image_url}: {str(e)}")
            
            # Check for front images in highlight items
            if '_embedded' in tour and 'timeline' in tour['_embedded'] and '_embedded' in tour['_embedded']['timeline']:
                timeline_items = tour['_embedded']['timeline']['_embedded']['items']
                
                for item in timeline_items:
                    if item['type'] == 'highlight' and '_embedded' in item and 'reference' in item['_embedded']:
                        highlight_ref = item['_embedded']['reference']
                        
                        if '_embedded' in highlight_ref and 'front_image' in highlight_ref['_embedded']:
                            front_image = highlight_ref['_embedded']['front_image']
                            
                            if 'src' in front_image:
                                image_url = front_image['src']
                                
                                # Extract filename from URL or generate one
                                parsed_url = urlparse(image_url)
                                filename = os.path.basename(parsed_url.path)
                                if not filename or '.' not in filename:
                                    # Generate a filename if URL doesn't provide a good one
                                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                                    filename = f"tour_{tour_id}_highlight_{timestamp}.jpg"
                                
                                # Create a subdirectory for this tour
                                tour_img_dir = os.path.join(output_dir, str(tour_id))
                                os.makedirs(tour_img_dir, exist_ok=True)
                                
                                # Full path to save the image
                                image_path = os.path.join(tour_img_dir, filename)
                                
                                # Download the image
                                try:
                                    logger.info(f"Downloading highlight image: {image_url}")
                                    response = requests.get(image_url, stream=True, timeout=10)
                                    if response.status_code == 200:
                                        with open(image_path, 'wb') as f:
                                            for chunk in response.iter_content(1024):
                                                f.write(chunk)
                                        
                                        # Add relative path to the list
                                        rel_path = os.path.join(str(tour_id), filename)
                                        image_paths.append(rel_path)
                                        logger.info(f"Highlight image saved to {image_path}")
                                    else:
                                        logger.warning(f"Failed to download highlight image: {image_url}, status code: {response.status_code}")
                                except Exception as e:
                                    logger.error(f"Error downloading highlight image {image_url}: {str(e)}")
            
            return image_paths
            
        except Exception as e:
            logger.error(f"Error downloading tour images: {str(e)}")
            raise Exception(f"Failed to download tour images: {str(e)}")
        
    def make_gpx(self, tour_id, output_dir, include_poi, skip_existing, 
                tour_base, add_date, max_title_length, max_desc_length, 
                return_content=False, anonymous=False):
        """
        Generate a GPX file for a tour
        
        Args:
            tour_id: Tour ID
            output_dir: Output directory
            include_poi: Whether to include POIs
            skip_existing: Skip if the file already exists
            tour_base: Tour data if already fetched
            add_date: Add date to filename
            max_title_length: Max length of title in filename
            max_desc_length: Max length of POI descriptions
            return_content: Whether to return the GPX content
            anonymous: Whether to use anonymous mode
            
        Returns:
            GPX content if return_content=True, otherwise None
        """
        try:
            logger.info(f"Making GPX for tour {tour_id}, anonymous: {anonymous}")
            
            tour = None
            if tour_base is None:
                logger.info(f"Fetching tour {tour_id} details")
                tour_base = self.fetch_tour(str(tour_id))
                tour = tour_base
                
            # Save the last tour for reference
            self.last_tour = tour_base
            
            # Example date: 2022-01-02T12:26:41.795+01:00
            # :10 extracts "2022-01-02" from this.
            date_str = tour_base['date'][:10]+'_' if add_date else ''
            
            # Create filename
            from komootgpx.utils import sanitize_filename
            filename = sanitize_filename(tour_base['name'])
            if max_title_length == 0:
                filename = f"{tour_id}"
            elif max_title_length > 0 and len(filename) > max_title_length:
                filename = f"{filename[:max_title_length]}-{tour_id}"
            else:
                filename = f"{filename}-{tour_id}"
            
            # Full path
            path = f"{output_dir}/{date_str}{filename}.gpx"
            self.last_filename = f"{date_str}{filename}.gpx"
            
            logger.info(f"GPX will be saved as {path}")
            
            # Check if file already exists
            if skip_existing and os.path.exists(path):
                logger.info(f"File already exists, skipping: {path}")
                if return_content:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read()
                return None
            
            # Fetch tour if not already fetched
            if tour is None:
                logger.info(f"Fetching tour details for {tour_id}")
                tour = self.fetch_tour(str(tour_id))
                
            # Create GPX
            logger.info(f"Generating GPX content for tour {tour_id}")
            gpx = GpxCompiler(tour, self.api, not include_poi, max_desc_length)
            gpx_content = gpx.generate()
            
            # Create directory if needed
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            
            # Write to file
            logger.info(f"Writing GPX to file: {path}")
            with open(path, "w", encoding="utf-8") as f:
                f.write(gpx_content)
            
            # Return content if requested
            if return_content:
                return gpx_content
            return None
            
        except Exception as e:
            logger.error(f"Error generating GPX: {str(e)}")
            raise Exception(f"Failed to generate GPX: {str(e)}")
    
    def get_last_filename(self):
        """Get the last generated filename"""
        return self.last_filename
        
    def get_last_tour(self):
        """Get the last processed tour"""
        return self.last_tour