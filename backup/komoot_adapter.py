import os
import io
import logging
import requests
import time
import re
from datetime import datetime
from urllib.parse import urlparse
from komootgpx.api import KomootApi
from komootgpx.gpxcompiler import GpxCompiler
from bs4 import BeautifulSoup

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
    
    def get_user_id(self):
        """Get the user ID from the API if available"""
        if hasattr(self.api, 'user_id'):
            return self.api.user_id
        return None
    
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

    def fetch_collection_by_url(self, collection_url):
        """
        Fetch a specific collection by URL
        
        Args:
            collection_url: URL of the collection to fetch
            
        Returns:
            Collection data with tours
        """
        try:
            logger.info(f"Fetching collection from URL: {collection_url}")
            
            # Create a session for requests
            session = requests.Session()
            
            # Try to get cookies from KomootAPI session
            if hasattr(self.api, '_session'):
                if hasattr(self.api._session, 'cookies'):
                    session.cookies.update(self.api._session.cookies)
                    logger.info("Copied cookies from KomootAPI session")
                    
            # Pass common headers to look like a real browser
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Referer': 'https://www.komoot.com/',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0',
            }
            
            # Get the collection page
            logger.info(f"Sending GET request to {collection_url}")
            response = session.get(collection_url, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch collection: HTTP {response.status_code}")
                raise Exception(f"Failed to fetch collection: HTTP {response.status_code}")
            
            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract collection ID from URL
            collection_id = "unknown"
            url_parts = collection_url.split('/')
            for i, part in enumerate(url_parts):
                if part == "collection" and i+1 < len(url_parts):
                    collection_id = url_parts[i+1]
                    break
            
            # Extract collection name
            collection_name = f"Collection {collection_id}"
            name_element = soup.find('h1')
            if name_element:
                collection_name = name_element.text.strip()
            
            # Find tours in the collection
            tours = []
            tour_elements = soup.find_all('a', href=re.compile(r'^/tour/\d+'))
            
            for element in tour_elements:
                tour_url = element['href']
                tour_id = tour_url.split('/')[-1]
                
                # Try to find the tour name
                tour_name = f"Tour {tour_id}"
                name_element = element.find('div', class_=lambda x: x and 'name' in x)
                if name_element:
                    tour_name = name_element.text.strip()
                
                # Only add unique tours
                if not any(t['id'] == tour_id for t in tours):
                    tours.append({
                        'id': tour_id,
                        'name': tour_name,
                        'url': f"https://www.komoot.com{tour_url}"
                    })
            
            # Create collection object
            collection = {
                'id': collection_id,
                'name': collection_name,
                'type': 'collection',
                'tours': tours,
                'tours_count': len(tours)
            }
            
            logger.info(f"Successfully fetched collection '{collection_name}' with {len(tours)} tours")
            return collection
            
        except Exception as e:
            logger.error(f"Error fetching collection by URL: {str(e)}")
            raise Exception(f"Failed to fetch collection: {str(e)}")
    
    def fetch_collections(self):
        """Fetch user collections from Komoot"""
        try:
            logger.info("Fetching user collections")
            collections = []
            username = None
            
            # Try to get username or user ID
            if hasattr(self.api, 'username'):
                username = self.api.username
            elif hasattr(self.api, 'user_display_name'):
                username = self.api.user_display_name
            
            # If we couldn't get a username, try user_id
            if not username:
                user_id = self.get_user_id()
                if user_id:
                    # Make a request to get user profile and extract username
                    try:
                        user_url = f"https://api.komoot.de/v007/users/{user_id}"
                        response = self.api._session.get(user_url)
                        if response.status_code == 200:
                            user_data = response.json()
                            # The username might be in the 'username' field
                            username = user_data.get('username', None)
                    except Exception as e:
                        logger.warning(f"Error fetching user profile: {str(e)}")
            
            if not username:
                # Set a fallback username from the example
                username = "nevvkid"
                logger.warning(f"Could not determine username, using fallback: {username}")
            else:
                logger.info(f"Using username: {username}")
            
            # URLs for collections
            personal_collections_url = f"https://www.komoot.com/user/{username}/collections/personal"
            saved_collections_url = f"https://www.komoot.com/user/{username}/collections/saved"
            
            # Create a session for web requests
            session = requests.Session()
            
            # Try to get cookies from KomootAPI session
            if hasattr(self.api, '_session'):
                if hasattr(self.api._session, 'cookies'):
                    session.cookies.update(self.api._session.cookies)
                    logger.info("Copied cookies from KomootAPI session")
            
            # Pass common headers to look like a real browser
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Referer': 'https://www.komoot.com/',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0',
            }
            
            # Fetch personal collections
            try:
                logger.info(f"Fetching personal collections from {personal_collections_url}")
                personal_resp = session.get(personal_collections_url, headers=headers)
                
                if personal_resp.status_code == 200:
                    # Parse the HTML
                    soup = BeautifulSoup(personal_resp.text, 'html.parser')
                    
                    # Find collection links
                    collection_links = soup.find_all('a', href=re.compile(r'^/collection/\d+'))
                    
                    logger.info(f"Found {len(collection_links)} personal collection links")
                    
                    for link in collection_links:
                        collection_url = f"https://www.komoot.com{link['href']}"
                        try:
                            collection = self.fetch_collection_by_url(collection_url)
                            if collection:
                                # Mark as personal collection
                                collection['type'] = 'personal'
                                collections.append(collection)
                        except Exception as coll_err:
                            logger.error(f"Error fetching personal collection {link['href']}: {str(coll_err)}")
                else:
                    logger.warning(f"Failed to fetch personal collections page: HTTP {personal_resp.status_code}")
            except Exception as pers_err:
                logger.error(f"Error processing personal collections: {str(pers_err)}")
            
            # Fetch saved collections
            try:
                logger.info(f"Fetching saved collections from {saved_collections_url}")
                saved_resp = session.get(saved_collections_url, headers=headers)
                
                if saved_resp.status_code == 200:
                    # Parse the HTML
                    soup = BeautifulSoup(saved_resp.text, 'html.parser')
                    
                    # Find collection links
                    collection_links = soup.find_all('a', href=re.compile(r'^/collection/\d+'))
                    
                    logger.info(f"Found {len(collection_links)} saved collection links")
                    
                    for link in collection_links:
                        collection_url = f"https://www.komoot.com{link['href']}"
                        try:
                            collection = self.fetch_collection_by_url(collection_url)
                            if collection:
                                # Mark as saved collection
                                collection['type'] = 'saved'
                                collections.append(collection)
                        except Exception as coll_err:
                            logger.error(f"Error fetching saved collection {link['href']}: {str(coll_err)}")
                else:
                    logger.warning(f"Failed to fetch saved collections page: HTTP {saved_resp.status_code}")
            except Exception as saved_err:
                logger.error(f"Error processing saved collections: {str(saved_err)}")
            
            # Make sure we have at least one virtual collection with all tours
            try:
                # Get all tours
                all_tours = self.fetch_tours()
                
                # Create a list of tour objects
                tour_list = []
                for tour_id, tour in all_tours.items():
                    tour_list.append({
                        'id': str(tour_id),
                        'name': tour.get('name', f"Tour {tour_id}"),
                        'url': f"https://www.komoot.com/tour/{tour_id}",
                        'date': tour.get('date', '')[:10] if 'date' in tour else '',
                        'sport': tour.get('sport', ''),
                        'type': tour.get('type', '')
                    })
                
                # Add virtual collection for all tours
                collections.append({
                    'id': 'all_tours',
                    'name': 'All Tours',
                    'type': 'virtual',
                    'tours': tour_list,
                    'tours_count': len(tour_list)
                })
                
                # Also add collections based on tour type
                tour_types = {}
                for tour in tour_list:
                    tour_type = tour.get('type', '')
                    if tour_type:
                        if tour_type not in tour_types:
                            tour_types[tour_type] = []
                        tour_types[tour_type].append(tour)
                
                for tour_type, tours in tour_types.items():
                    # Format the tour type name
                    if tour_type == 'tour_planned':
                        type_name = 'Planned Tours'
                    elif tour_type == 'tour_recorded':
                        type_name = 'Recorded Tours'
                    else:
                        type_name = tour_type.replace('_', ' ').title() + ' Tours'
                    
                    collections.append({
                        'id': f'type_{tour_type}',
                        'name': type_name,
                        'type': 'virtual',
                        'tours': tours,
                        'tours_count': len(tours)
                    })
            except Exception as virtual_err:
                logger.error(f"Error creating virtual collections: {str(virtual_err)}")
            
            logger.info(f"Found a total of {len(collections)} collections")
            return collections
            
        except Exception as e:
            logger.error(f"Error fetching collections: {str(e)}")
            raise Exception(f"Failed to fetch collections: {str(e)}")
    
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