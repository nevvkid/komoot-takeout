import os
import io
import logging
import requests
import time
import re
import base64
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import json
import csv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# KomootGPX-style authentication class
class BasicAuthToken(requests.auth.AuthBase):
    def __init__(self, key, value):
        self.key = key
        self.value = value

    def __call__(self, r):
        authstr = 'Basic ' + base64.b64encode(bytes(self.key + ":" + self.value, 'utf-8')).decode('utf-8')
        r.headers['Authorization'] = authstr
        return r

# Define utility function for sanitizing filenames
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

def extract_slug_from_url(url):
    """Extract the slug part from a Komoot collection URL"""
    if not url:
        return ""
        
    # Pattern to match the slug part after the collection ID
    match = re.search(r'/collection/\d+/?-?([a-zA-Z0-9-]+)?', url)
    if match and match.group(1):
        return match.group(1)
    return ""

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logger.warning("BeautifulSoup4 not available. Some functions will be limited.")
    
try:
    import gpxpy
    import gpxpy.gpx
    GPXPY_AVAILABLE = True
except ImportError:
    GPXPY_AVAILABLE = False
    logger.warning("gpxpy not available. GPX file generation will be limited.")

# Check if KomootGPX is installed
try:
    import komootgpx
    KOMOOTGPX_AVAILABLE = True
    KOMOOTGPX_VERSION = getattr(komootgpx, '__version__', 'unknown')
    logger.info(f"KomootGPX library found. Version: {KOMOOTGPX_VERSION}")
except ImportError:
    KOMOOTGPX_AVAILABLE = False
    logger.warning("KomootGPX library not found. Some functions will be limited.")

class KomootAdapter:
    """
    Adapter class for Komoot API integration with GPX and collection functionality
    """
    def __init__(self):
        self.user_id = None
        self.token = None
        self.user_display_name = None
        self.username = None
        self.email = None
        self.session = requests.Session()
        self.authenticated = False
        self.last_filename = None
        self.last_tour = None
        
        # Set common headers to look like a browser
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.komoot.com/',
            'DNT': '1'
        })
        
    def __build_header(self):
        """Build authentication header similar to KomootGPX"""
        if self.user_id and self.token:
            return BasicAuthToken(self.user_id, self.token)
        return None
    
    def __send_request(self, url, auth=None, critical=True, headers=None, method="GET", json_data=None):
        """Send authenticated request, similar to KomootGPX"""
        try:
            request_headers = self.session.headers.copy()
            if headers:
                request_headers.update(headers)
            
            if method.upper() == "GET":
                r = self.session.get(url, auth=auth, headers=request_headers)
            elif method.upper() == "POST":
                r = self.session.post(url, auth=auth, headers=request_headers, json=json_data)
            else:
                raise ValueError(f"Unsupported method: {method}")
                
            if r.status_code != 200:
                error_msg = f"Error {r.status_code}"
                try:
                    error_msg += f": {r.json()}"
                except:
                    error_msg += f": {r.text}"
                
                logger.error(error_msg)
                if critical:
                    raise Exception(error_msg)
            return r
        except Exception as e:
            if critical:
                raise e
            else:
                logger.error(f"Request error: {str(e)}")
                return None
    
    def login(self, email, password):
        """Login to Komoot with KomootGPX's method"""
        try:
            logger.info(f"Logging in to Komoot as {email}")
            print("Logging in...")
            
            # Save email for reference
            self.email = email
            
            # Use KomootGPX's login strategy
            r = self.__send_request(f"https://api.komoot.de/v006/account/email/{email}/",
                              BasicAuthToken(email, password))
            
            if r.status_code != 200:
                raise Exception(f"Login failed with status code: {r.status_code}")
            
            # Extract authentication data from response
            response_data = r.json()
            self.user_id = response_data['username']
            self.token = response_data['password']
            self.user_display_name = response_data['user']['displayname']
            
            # Extract username for URL construction if available
            if 'username' in response_data['user']:
                self.username = response_data['user']['username']
            else:
                # Try to infer a username from the display name
                self.username = re.sub(r'[^a-zA-Z0-9_]', '', self.user_display_name.lower().replace(' ', '_'))
            
            print(f"Logged in as '{self.user_display_name}'")
            self.authenticated = True
            logger.info(f"Login successful as {self.get_display_name()}")
            return True
            
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            raise Exception(f"Komoot login failed: {str(e)}")
    
    def get_display_name(self):
        """Get the display name of the logged-in user"""
        if self.user_display_name:
            return self.user_display_name
        return "Komoot User"
    
    def get_user_id(self):
        """Get the user ID from the API if available"""
        return self.user_id
        
    def get_username(self):
        """Get the username for URL construction"""
        if self.username:
            return self.username
        elif self.user_display_name:
            # Try to convert display name to a username format
            username = re.sub(r'[^a-zA-Z0-9_]', '', self.user_display_name.lower().replace(' ', '_'))
            return username
        return None
    
    def fetch_tours(self, tour_type="all", silent=False):
        """Fetch tours from Komoot using KomootGPX's method"""
        try:
            if not silent:
                logger.info(f"Fetching tours with filter: {tour_type}, silent: {silent}")
                print(f"Fetching tours of user '{self.user_id}'...")
            
            results = {}
            has_next_page = True
            current_uri = f"https://api.komoot.de/v007/users/{self.user_id}/tours/"
            
            while has_next_page:
                r = self.__send_request(current_uri, self.__build_header())
                
                has_next_page = '_links' in r.json() and 'next' in r.json()['_links'] and 'href' in r.json()['_links']['next']
                if has_next_page:
                    current_uri = r.json()['_links']['next']['href']
                
                tours = r.json()['_embedded']['tours']
                for tour in tours:
                    # Apply proper type filtering based on Komoot's API values
                    if tour_type == "all":
                        # Include all tours
                        results[tour['id']] = tour
                    elif tour_type == "recorded" and tour['type'] == "tour_recorded":
                        # Match recorded tours
                        results[tour['id']] = tour
                    elif tour_type == "planned" and tour['type'] == "tour_planned":
                        # Match planned tours
                        results[tour['id']] = tour
                    elif tour_type == "favorite" and tour.get('is_favorite', False):
                        # Match favorite tours
                        results[tour['id']] = tour
                    elif tour_type == tour['type']:
                        # Direct match for any other types
                        results[tour['id']] = tour
            
            if not silent:
                print(f"Found {len(results)} tours")
                logger.info(f"Successfully fetched {len(results)} tours")
            
            return results
            
        except Exception as e:
            logger.error(f"Error fetching tours: {str(e)}")
            raise Exception(f"Failed to fetch tours: {str(e)}")
    
    def fetch_tour(self, tour_id, retries=3, anonymous=False):
        """Fetch a tour with KomootGPX's method"""
        attempt = 0
        last_error = None
        
        while attempt < retries:
            try:
                logger.info(f"Fetching tour {tour_id} (attempt {attempt+1}/{retries}), anonymous: {anonymous}")
                print(f"Fetching tour '{tour_id}'...")
                
                # For anonymous mode, try direct request without auth
                if anonymous:
                    # Try to fetch the tour data directly from API
                    r = self.__send_request(f"https://api.komoot.de/v007/tours/{tour_id}?_embedded=coordinates,way_types,"
                                          f"surfaces,directions,participants,"
                                          f"timeline&directions=v2&fields"
                                          f"=timeline&format=coordinate_array"
                                          f"&timeline_highlights_fields=tips,"
                                          f"recommenders", critical=False)
                    
                    if r and r.status_code == 200:
                        logger.info(f"Successfully fetched tour {tour_id} anonymously")
                        return r.json()
                    else:
                        # Try web page scraping as fallback
                        logger.info(f"API failed for anonymous mode, trying HTML scraping")
                        tour_data = self._scrape_tour_page(tour_id)
                        if tour_data:
                            return tour_data
                else:
                    # Use authenticated request
                    r = self.__send_request(f"https://api.komoot.de/v007/tours/{tour_id}?_embedded=coordinates,way_types,"
                                          f"surfaces,directions,participants,"
                                          f"timeline&directions=v2&fields"
                                          f"=timeline&format=coordinate_array"
                                          f"&timeline_highlights_fields=tips,"
                                          f"recommenders",
                                        self.__build_header())
                    
                    logger.info(f"Successfully fetched tour {tour_id}")
                    return r.json()
                
            except Exception as e:
                last_error = e
                logger.error(f"Error fetching tour (attempt {attempt+1}/{retries}): {str(e)}")
                attempt += 1
                time.sleep(1)  # Wait between retries
        
        # If all retries failed, re-raise the last error
        logger.error(f"Failed to fetch tour after {retries} attempts: {str(last_error)}")
        raise Exception(f"Failed to fetch tour: {str(last_error)}")
    
    def _scrape_tour_page(self, tour_id):
        """Scrape a tour page to get basic information (for anonymous mode)"""
        if not BS4_AVAILABLE:
            logger.warning("BeautifulSoup4 not available, cannot scrape tour page")
            return None
            
        try:
            url = f"https://www.komoot.com/tour/{tour_id}"
            response = requests.get(url, headers=self.session.headers)
            
            if response.status_code != 200:
                logger.warning(f"Failed to access tour page: {response.status_code}")
                return None
                
            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract basic tour information
            tour_data = {
                'id': tour_id,
                'name': f"Tour {tour_id}"
            }
            
            # Try to get the title
            title_elem = soup.find('h1', class_='headline')
            if title_elem:
                tour_data['name'] = title_elem.text.strip()
            
            # Try to get stats
            stats_elem = soup.select('.tour-stats__value')
            if stats_elem:
                for stat in stats_elem:
                    label = stat.find_previous('div', class_='tour-stats__label')
                    if label and stat:
                        key = label.text.strip().lower()
                        value = stat.text.strip()
                        
                        if 'distance' in key:
                            # Extract numeric value
                            match = re.search(r'([\d.,]+)', value)
                            if match:
                                try:
                                    distance_km = float(match.group(1).replace(',', '.'))
                                    tour_data['distance'] = distance_km * 1000  # Convert to meters
                                    tour_data['distance_km'] = distance_km
                                except:
                                    pass
                        elif 'elevation' in key and 'up' in key:
                            # Extract numeric value
                            match = re.search(r'([\d.,]+)', value)
                            if match:
                                try:
                                    tour_data['elevation_up'] = int(match.group(1).replace(',', '.'))
                                except:
                                    pass
                        elif 'elevation' in key and 'down' in key:
                            # Extract numeric value
                            match = re.search(r'([\d.,]+)', value)
                            if match:
                                try:
                                    tour_data['elevation_down'] = int(match.group(1).replace(',', '.'))
                                except:
                                    pass
                        elif 'duration' in key:
                            # Extract hours and minutes
                            hours_match = re.search(r'(\d+)h', value)
                            minutes_match = re.search(r'(\d+)min', value)
                            
                            hours = int(hours_match.group(1)) if hours_match else 0
                            minutes = int(minutes_match.group(1)) if minutes_match else 0
                            
                            # Calculate total seconds
                            duration_seconds = (hours * 60 * 60) + (minutes * 60)
                            tour_data['duration'] = duration_seconds
            
            # Try to get sport type
            sport_elem = soup.select('.tour-type')
            if sport_elem and len(sport_elem) > 0:
                tour_data['sport'] = sport_elem[0].text.strip().lower()
            
            # Try to get date
            date_elem = soup.select('.tour-stats__date')
            if date_elem and len(date_elem) > 0:
                date_text = date_elem[0].text.strip()
                try:
                    # Parse the date string
                    date_obj = datetime.strptime(date_text, '%d.%m.%Y')
                    tour_data['date'] = date_obj.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                except:
                    pass
            
            self.last_tour = tour_data
            return tour_data
            
        except Exception as e:
            logger.error(f"Error scraping tour page: {str(e)}")
            return None
    
    def fetch_highlight_tips(self, highlight_id):
        """Fetch highlight tips as in KomootGPX"""
        try:
            logger.info(f"Fetching highlight {highlight_id}")
            print(f"Fetching highlight '{highlight_id}'...")
            
            r = self.__send_request(f"https://api.komoot.de/v007/highlights/{highlight_id}/tips/",
                                  self.__build_header(), critical=False)
            
            return r.json()
            
        except Exception as e:
            logger.warning(f"Error fetching highlight tips: {str(e)}")
            return {"_embedded": {"items": []}}  # Return empty tips as fallback
    
    def extract_collections_from_page(self, page_html, collection_type):
        """
        Extract collections from a page HTML
        
        Args:
            page_html: HTML content of the page
            collection_type: Type of collection (personal or saved)
            
        Returns:
            List of collection objects with basic info
        """
        if not BS4_AVAILABLE:
            raise Exception("BeautifulSoup4 is not installed. Please install it with: pip install beautifulsoup4")
            
        collections = []
        soup = BeautifulSoup(page_html, 'html.parser')
        
        # Try multiple strategies to find collection elements in the current Komoot UI
        collection_elements = []
        
        # Strategy 1: Try modern UI with data-test attributes
        modern_elements = soup.select('[data-test="collection-item"]')
        if modern_elements:
            collection_elements.extend(modern_elements)
            logger.info(f"Found {len(modern_elements)} collections with modern data-test selector")
            
        # Strategy 2: Try classic collection-card class
        if not collection_elements:
            classic_elements = soup.select('.collection-card')
            if classic_elements:
                collection_elements.extend(classic_elements)
                logger.info(f"Found {len(classic_elements)} collections with classic collection-card selector")
        
        # Strategy 3: Try new UI with tailwind classes
        if not collection_elements:
            # Look for elements with common Tailwind container classes that might contain collections
            tw_elements = soup.select('.tw-mb-8')
            if tw_elements:
                collection_elements.extend(tw_elements)
                logger.info(f"Found {len(tw_elements)} collections with Tailwind container selector")
        
        # Strategy 4: Directly find collection links
        if not collection_elements:
            link_elements = soup.find_all('a', href=re.compile(r'/collection/\d+'))
            if link_elements:
                collection_elements.extend(link_elements)
                logger.info(f"Found {len(link_elements)} collections with direct link selector")
        
        # Strategy 5: Look for any elements with collection in the class name
        if not collection_elements:
            collection_class_elements = soup.find_all(class_=lambda c: c and 'collection' in c.lower())
            if collection_class_elements:
                collection_elements.extend(collection_class_elements)
                logger.info(f"Found {len(collection_class_elements)} collections with collection class selector")
        
        logger.info(f"Found total of {len(collection_elements)} {collection_type} collection elements")
        
        # Process each collection element
        for element in collection_elements:
            try:
                # Find collection link - try multiple strategies
                link = None
                
                # Direct link element
                if element.name == 'a' and '/collection/' in element.get('href', ''):
                    link = element
                else:
                    # Find first link to a collection in this element
                    link = element.find('a', href=re.compile(r'/collection/\d+'))
                
                if not link or not link.get('href'):
                    continue
                    
                # Extract collection ID from URL
                href = link.get('href', '')
                match = re.search(r'/collection/(\d+)', href)
                if not match:
                    continue
                    
                collection_id = match.group(1)
                
                # Get collection name - try multiple potential elements
                collection_name = f"Collection {collection_id}"
                
                # Try multiple selectors to find the collection title
                name_element = None
                
                # Method 1: Look for specific title classes
                name_selectors = [
                    '.collection-card__title',
                    '[data-test="collection-title"]',
                    '.tw-text-lg.tw-font-bold',
                    '.tw-font-bold',
                ]
                
                for selector in name_selectors:
                    name_element = element.select_one(selector)
                    if name_element:
                        break
                
                # Method 2: Try headings if no title found
                if not name_element:
                    for heading in ['h3', 'h2', 'h4', 'h5']:
                        name_element = element.find(heading)
                        if name_element:
                            break
                
                # Method 3: Use any prominent text elements
                if not name_element:
                    # Look for elements with font-bold class
                    bold_elements = element.select('.tw-font-bold')
                    if bold_elements:
                        name_element = bold_elements[0]  # Use the first bold element
                
                if name_element:
                    name_text = name_element.text.strip()
                    if name_text:
                        collection_name = name_text
                
                # Get full URL
                collection_url = href if href.startswith('http') else f"https://www.komoot.com{href}"
                
                # Get tour count if available
                tour_count = 0
                
                # Try multiple strategies for finding tour count
                count_element = element.select_one('.collection-card__tours-count')
                if not count_element:
                    count_element = element.select_one('[data-test="collection-count"]')
                
                if not count_element:
                    # Look for text content that mentions tours or routes
                    for span_element in element.find_all('span'):
                        span_text = span_element.text.strip().lower()
                        if 'tour' in span_text or 'route' in span_text or 'activity' in span_text:
                            count_match = re.search(r'(\d+)', span_text)
                            if count_match:
                                try:
                                    tour_count = int(count_match.group(1))
                                    break
                                except ValueError:
                                    pass
                
                if count_element:
                    count_text = count_element.text.strip()
                    count_match = re.search(r'(\d+)', count_text)
                    if count_match:
                        try:
                            tour_count = int(count_match.group(1))
                        except ValueError:
                            pass
                
                # Look for cover image
                cover_image_url = None
                img_element = element.find('img')
                if img_element and 'src' in img_element.attrs:
                    cover_image_url = img_element['src']
                
                # Create collection object
                collection = {
                    'id': collection_id,
                    'name': collection_name,
                    'url': collection_url,
                    'type': collection_type,
                    'tours_count': tour_count,
                    'cover_image_url': cover_image_url
                }
                
                collections.append(collection)
                logger.debug(f"Added collection: {collection_name} (ID: {collection_id}) with {tour_count} tours")
                
            except Exception as e:
                logger.error(f"Error processing collection element: {str(e)}")
        
        if not collections:
            logger.warning(f"No collections extracted from page. HTML content length: {len(page_html)}")
            # Save a snippet of the HTML for debugging
            snippet = page_html[:500] + "... [truncated]" if len(page_html) > 500 else page_html
            logger.debug(f"HTML snippet: {snippet}")
            
        return collections
    
    def extract_tours_from_collection_page(self, page_url):
        """Extract tours from a collection page URL"""
        try:
            response = self.__send_request(page_url, critical=False)
            if not response or response.status_code != 200:
                logger.warning(f"Failed to fetch page {page_url}: HTTP {response.status_code if response else 'None'}")
                return []
                
            soup = BeautifulSoup(response.text, 'html.parser')
            tours = []
            
            # Track unique tour IDs to prevent duplicates
            tour_ids_seen = set()
            
            # Find all tour cards - look for the structure that contains tour titles
            tour_cards = []
            
            # First try the modern layout with data-test="tour-item"
            modern_cards = soup.select('[data-test="tour-item"]')
            if modern_cards:
                tour_cards.extend(modern_cards)
                logger.info(f"Found {len(modern_cards)} tour cards with [data-test='tour-item']")
            
            # Try the more generic selectors as backups
            if not tour_cards:
                # Try different selectors for tour cards
                backup_selectors = [
                    '.tour-card',
                    '.collection-tour-card',
                    'div[role="listitem"]',
                    '.css-1qyi8eq',
                    'a[href*="/tour/"]'
                ]
                
                for selector in backup_selectors:
                    backup_cards = soup.select(selector)
                    if backup_cards:
                        tour_cards.extend(backup_cards)
                        logger.info(f"Found {len(backup_cards)} tour cards with '{selector}'")
                
            logger.info(f"Total found: {len(tour_cards)} tour cards on page {page_url}")
            
            for card in tour_cards:
                try:
                    # Find the link to the tour
                    tour_link = None
                    if card.name == 'a' and '/tour/' in card.get('href', ''):
                        tour_link = card
                    else:
                        tour_link = card.select_one('a[href*="/tour/"]')
                    
                    if not tour_link or not tour_link.get('href'):
                        continue
                        
                    # Extract tour ID
                    href = tour_link.get('href', '')
                    match = re.search(r'/tour/(\d+)', href)
                    if not match:
                        continue
                        
                    tour_id = match.group(1)
                    
                    # Skip if we already have this tour
                    if tour_id in tour_ids_seen:
                        continue
                        
                    tour_ids_seen.add(tour_id)
                    
                    # Default name
                    tour_name = f"Tour {tour_id}"
                    
                    # Try multiple strategies to find the title
                    
                    # Strategy 1: Find element with data-test-id="tour_title" inside this card
                    title_elem = card.select_one('[data-test-id="tour_title"]')
                    
                    # Strategy 2: Also check for hyphenated version
                    if not title_elem:
                        title_elem = card.select_one('[data-test-id="tour-title"]')
                    
                    # Strategy 3: Look for h3 inside the anchor that has the tour link
                    if not title_elem and tour_link:
                        title_elem = tour_link.find('h3')
                    
                    # Strategy 4: Look for any heading elements
                    if not title_elem:
                        for heading in ['h3', 'h2', 'h4']:
                            if card.find(heading):
                                title_elem = card.find(heading)
                                break
                    
                    # Strategy 5: Look for elements with title-like classes
                    if not title_elem:
                        title_classes = [
                            '.tw-font-bold',
                            '.tour-card__title',
                            '.tw-text-xl',
                            '.tw-text-2xl'
                        ]
                        for cls in title_classes:
                            if card.select_one(cls):
                                title_elem = card.select_one(cls)
                                break
                    
                    # If we found a title element, extract the text
                    if title_elem:
                        title_text = title_elem.get_text(strip=True)
                        if title_text:
                            tour_name = title_text
                            logger.debug(f"Found tour name: {tour_name}")
                    
                    # Get full URL
                    tour_url = href if href.startswith('http') else f"https://www.komoot.com{href}"
                    
                    # Create tour data object with the extracted information
                    tour_data = {
                        'id': tour_id,
                        'name': tour_name,
                        'url': tour_url
                    }
                    
                    # Extract any available statistics
                    distance_element = card.select_one('.tour-card__distance')
                    if distance_element:
                        distance_text = distance_element.text.strip()
                        distance_match = re.search(r'([\d.,]+)', distance_text)
                        if distance_match:
                            try:
                                distance_km = float(distance_match.group(1).replace(',', '.'))
                                tour_data['distance_km'] = distance_km
                                tour_data['distance'] = distance_km * 1000  # Convert to meters
                            except:
                                pass
                    
                    # Try to extract duration
                    duration_element = card.select_one('.tour-card__duration')
                    if duration_element:
                        duration_text = duration_element.text.strip()
                        hours_match = re.search(r'(\d+)h', duration_text)
                        minutes_match = re.search(r'(\d+)min', duration_text)
                        
                        hours = int(hours_match.group(1)) if hours_match else 0
                        minutes = int(minutes_match.group(1)) if minutes_match else 0
                        
                        # Calculate total seconds
                        duration_seconds = (hours * 60 * 60) + (minutes * 60)
                        tour_data['duration'] = duration_seconds
                    
                    # Try to extract sport type
                    sport_element = card.select_one('.tour-card__sport-type')
                    if sport_element:
                        tour_data['sport'] = sport_element.text.strip().lower()
                    
                    tours.append(tour_data)
                    
                except Exception as e:
                    logger.error(f"Error processing tour card: {str(e)}")
            
            return tours
            
        except Exception as e:
            logger.error(f"Error extracting tours from page {page_url}: {str(e)}")
            return []
    
    def fetch_collection_by_url(self, collection_url):
        """
        Fetch a specific collection by URL, with enhanced HTML scraping
        
        Args:
            collection_url: URL of the collection to fetch
            
        Returns:
            Collection data with tours
        """
        if not BS4_AVAILABLE:
            raise Exception("BeautifulSoup4 is not installed. Please install it with: pip install beautifulsoup4")
            
        try:
            logger.info(f"Fetching collection from URL: {collection_url}")
            
            # Extract the collection ID from the URL
            collection_id = None
            match = re.search(r'/collection/(\d+)', collection_url)
            if match:
                collection_id = match.group(1)
            else:
                logger.warning(f"Could not extract collection ID from URL: {collection_url}")
                raise Exception(f"Invalid collection URL: {collection_url}")
            
            # Extract the slug from the URL if present
            collection_slug = ""
            slug_match = re.search(r'/collection/\d+/?-?([a-z0-9-]+)?', collection_url)
            if slug_match and slug_match.group(1):
                collection_slug = slug_match.group(1)
                logger.info(f"Extracted slug from URL: {collection_slug}")
            
            # We'll directly scrape the HTML page since API access is no longer reliable
            # Set browser-like headers for better success
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Referer': 'https://www.komoot.com/',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0',
                'TE': 'Trailers'
            }
            
            response = requests.get(collection_url, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch collection page: {response.status_code}")
                raise Exception(f"Failed to access collection page, status code: {response.status_code}")
                
            # Parse HTML content
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract collection name
            collection_name = f"Collection {collection_id}"
            
            # Try multiple selectors for title, focusing on newer Komoot structure first
            title_elem = soup.select_one('[data-test-id="c_title"]') or soup.select_one('.css-1q93hcd') or soup.select_one('h1.tw-font-bold')  # New Komoot structure
            if not title_elem:
                # Try alternative selectors for older versions
                title_elem = soup.select_one("h1.collection__title") or soup.find('h1') or soup.find('h2')
                
            if title_elem:
                # Get clean text, removing any "<!-- -->" comments
                title_text = re.sub(r'<!--\s*-->', '', title_elem.text).strip()
                if title_text:
                    collection_name = title_text
            
            # Extract description
            description = ""
            desc_elem = soup.select_one('.collection-description') or soup.select_one('[data-test-id="c_description"]') or soup.select_one('.tw-text-gray-600.tw-whitespace-pre-line')
            if desc_elem:
                description = desc_elem.text.strip()
                
            # Extract user ID from URL path or profile link
            user_id = None
            creator_name = None
            creator_elem = soup.select_one('.collection-header__user-link') or soup.select_one('[data-test-id="c_author"]') or soup.select_one('a[href*="/user/"]')
            if creator_elem:
                creator_name = creator_elem.text.strip()
                creator_href = creator_elem.get('href')
                if creator_href:
                    user_match = re.search(r'/user/([^/]+)', creator_href)
                    if user_match:
                        user_id = user_match.group(1)
            
            # Extract expected number of tours
            expected_tours_count = 0
            
            # Try the stats elements first
            stats_elems = soup.select('.collection-meta-data__item') or soup.select('[data-test-id="c_stats"]') or soup.select('.tw-text-sm.tw-font-medium')
            for stat in stats_elems:
                # Try to extract from text content directly for newer layouts
                stat_text = stat.text.strip().lower()
                count_match = re.search(r'(\d+)\s*(route|tour|activity|activities)', stat_text)
                if count_match:
                    expected_tours_count = int(count_match.group(1))
                    break
                
                # Try the older layout with separate elements for title and value
                key_elem = stat.select_one('.collection-meta-data__title')
                value_elem = stat.select_one('.collection-meta-data__data')
                if key_elem and value_elem:
                    key = key_elem.text.strip().lower()
                    value = value_elem.text.strip()
                    
                    if 'route' in key or 'activit' in key or 'tour' in key:
                        # Extract tour count
                        count_match = re.search(r'(\d+)', value)
                        if count_match:
                            expected_tours_count = int(count_match.group(1))
            
            # If no explicit count found, count tour cards
            if expected_tours_count == 0:
                tour_cards = soup.select('.tour-card') or soup.select('[data-test-id="tour_card"]') or soup.select('a[href*="/tour/"]')
                if tour_cards:
                    expected_tours_count = len(tour_cards)
            
            # Extract cover image URL
            cover_image_url = None
            
            # Method 1: Look for meta og:image tag
            og_image = soup.select_one('meta[property="og:image"]')
            if og_image and 'content' in og_image.attrs:
                cover_image_url = og_image['content']
                logger.info("Found cover image from og:image meta tag")
            
            # Method 2: Look for collection cover image in the page
            if not cover_image_url:
                cover_selectors = [
                    ".c-collection-cover__image img",
                    ".css-1dhdnz7",  # Class from recent Komoot collections
                    "img[alt*='Collection']",
                    "img[sizes*='1344px']",  # Large images are likely covers
                    ".tw-object-cover",  # New Komoot UI class for cover images
                    "img.tw-h-full"  # Another potential cover image class
                ]
                
                for selector in cover_selectors:
                    img_elem = soup.select_one(selector)
                    if img_elem and 'src' in img_elem.attrs:
                        cover_image_url = img_elem['src']
                        logger.info(f"Found cover image using selector: {selector}")
                        break
            
            # Generate slug from collection name if not present in URL
            if not collection_slug and collection_name:
                collection_slug = re.sub(r'[^a-z0-9]', '-', collection_name.lower())
                collection_slug = re.sub(r'-+', '-', collection_slug)  # Remove duplicate hyphens
                collection_slug = collection_slug.strip('-')  # Remove leading/trailing hyphens
                # Limit slug length
                if len(collection_slug) > 50:
                    collection_slug = collection_slug[:50]
            
            # Initialize collection data
            collection = {
                'id': collection_id,
                'name': collection_name,
                'url': collection_url,
                'type': 'public',
                'description': description,
                'expected_tours_count': expected_tours_count,
                'slug': collection_slug,  # Add slug to collection data
                'cover_image_url': cover_image_url  # Add cover image URL
            }
            
            # Add creator info if available
            if user_id:
                collection['creator'] = {
                    'id': user_id,
                    'display_name': creator_name or "Unknown User"
                }
            
            # Now extract tours - try multiple strategies to get as many as possible
            tours = []
            tour_ids_seen = set()
            
            # Strategy 1: Extract from current page
            page_tours = self.extract_tours_from_collection_page(collection_url)
            
            if page_tours:
                for tour in page_tours:
                    tour_id = tour['id']
                    if tour_id not in tour_ids_seen:
                        tour_ids_seen.add(tour_id)
                        tours.append(tour)
                        
                logger.info(f"Extracted {len(tours)} tours from first page")
            
            # Strategy 2: Try to get more tours by increasing page size
            if expected_tours_count > 0 and len(tours) < expected_tours_count:
                logger.info(f"Trying to get more tours - found {len(tours)}/{expected_tours_count}")
                
                # Try different page sizes
                for page_size in [50, 100, 200, 500]:
                    large_page_url = f"{collection_url}?size={page_size}"
                    logger.info(f"Trying page size {page_size}: {large_page_url}")
                    
                    page_tours = self.extract_tours_from_collection_page(large_page_url)
                    if page_tours:
                        new_count = 0
                        for tour in page_tours:
                            tour_id = tour['id']
                            if tour_id not in tour_ids_seen:
                                tour_ids_seen.add(tour_id)
                                tours.append(tour)
                                new_count += 1
                                
                        logger.info(f"Added {new_count} new tours with page size {page_size}")
                        
                    # If we've reached expected count, stop trying
                    if len(tours) >= expected_tours_count:
                        logger.info("Reached expected tour count, stopping search")
                        break
                        
            # Strategy 3: Try pagination if still missing tours
            if expected_tours_count > 0 and len(tours) < expected_tours_count:
                logger.info(f"Trying pagination - found {len(tours)}/{expected_tours_count}")
                
                page = 2  # Start from page 2
                max_pages = 20  # Safety limit
                
                while len(tours) < expected_tours_count and page <= max_pages:
                    page_url = f"{collection_url}?page={page}"
                    logger.info(f"Trying page {page}: {page_url}")
                    
                    page_tours = self.extract_tours_from_collection_page(page_url)
                    if not page_tours:
                        logger.info(f"No tours found on page {page}, stopping pagination")
                        break
                        
                    new_count = 0
                    for tour in page_tours:
                        tour_id = tour['id']
                        if tour_id not in tour_ids_seen:
                            tour_ids_seen.add(tour_id)
                            tours.append(tour)
                            new_count += 1
                            
                    logger.info(f"Added {new_count} new tours from page {page}")
                    
                    # If no new tours found on this page, stop
                    if new_count == 0:
                        logger.info(f"No new tours found on page {page}, stopping pagination")
                        break
                        
                    page += 1
            
            # Add tours to collection
            collection['tours'] = tours
            collection['tours_count'] = len(tours)
            
            # Log final result
            logger.info(f"Collection '{collection_name}' scraping completed: {len(tours)} tours")
            
            return collection
            
        except Exception as e:
            logger.error(f"Error fetching collection by URL: {str(e)}")
            raise Exception(f"Failed to fetch collection: {str(e)}")
    
    def fetch_collections(self, collection_type=None):
        """
        Fetch user collections using web scraping rather than API
        
        Args:
            collection_type: Optional filter for collection type ('personal' or 'saved')
            
        Returns:
            List of collection objects
        """
        if not BS4_AVAILABLE:
            raise Exception("BeautifulSoup4 is not installed. Please install it with: pip install beautifulsoup4")
            
        try:
            logger.info(f"Fetching user collections (type filter: {collection_type})")
            collections = []
            
            # Ensure we have user information
            if not self.user_id:
                raise Exception("Not logged in. Cannot fetch collections.")
            
            # Determine username for URL
            username_for_url = self.username or self.user_id
            if not username_for_url:
                logger.warning("No username found for URL construction")
                raise Exception("Cannot fetch collections without a username or user ID")
            
            logger.info(f"Using username for URLs: {username_for_url}")
                
            # The types to process
            types_to_process = ['personal', 'saved']
            if collection_type:
                types_to_process = [collection_type]
                
            # Process each collection type
            for coll_type in types_to_process:
                try:
                    # Build URL for this collection type
                    url = f"https://www.komoot.com/user/{username_for_url}/collections/{coll_type}"
                    logger.info(f"Fetching {coll_type} collections from {url}")
                    
                    # Request the page with modern browser headers
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Referer': 'https://www.komoot.com/',
                        'sec-ch-ua': '"Chromium";v="121", "Not A(Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"macOS"',
                        'sec-fetch-dest': 'document',
                        'sec-fetch-mode': 'navigate',
                        'sec-fetch-site': 'same-origin',
                        'sec-fetch-user': '?1',
                        'upgrade-insecure-requests': '1'
                    }
                    
                    # Add any cookies from session to help with authentication
                    response = requests.get(url, headers=headers, cookies=self.session.cookies)
                    
                    if response.status_code != 200:
                        logger.warning(f"Failed to fetch {coll_type} collections: HTTP {response.status_code}")
                        continue
                        
                    # Extract collections from the page
                    page_collections = self.extract_collections_from_page(response.text, coll_type)
                    logger.info(f"Found {len(page_collections)} {coll_type} collections")
                    
                    # Process each collection to get full details
                    for i, collection in enumerate(page_collections):
                        try:
                            # Limit to prevent timeouts for users with many collections
                            if i >= 20:
                                logger.info(f"Limiting to first 20 collections to avoid timeouts")
                                break
                                
                            logger.info(f"Fetching details for collection {i+1}/{len(page_collections)}: {collection['name']}")
                            
                            # Attempt to get detailed collection information
                            try:
                                collection_details = self.fetch_collection_by_url(collection['url'])
                                
                                if collection_details:
                                    # Keep original type and ID
                                    original_type = collection['type']
                                    original_id = collection['id']
                                    
                                    # Update collection with details
                                    collection.update(collection_details)
                                    
                                    # Ensure type and ID remain consistent
                                    collection['type'] = original_type
                                    collection['id'] = original_id
                                    
                                    # Add to collection list
                                    collections.append(collection)
                                else:
                                    logger.warning(f"Failed to fetch details for collection: {collection['name']}")
                                    collections.append(collection)  # Add the basic version anyway
                                    
                            except Exception as detail_err:
                                logger.warning(f"Error fetching collection details: {str(detail_err)}")
                                collections.append(collection)  # Add the basic version anyway
                            
                        except Exception as coll_err:
                            logger.error(f"Error fetching collection details: {str(coll_err)}")
                            # Still add the basic collection
                            collections.append(collection)
                    
                except Exception as e:
                    logger.error(f"Error processing {coll_type} collections: {str(e)}")
            
            logger.info(f"Found {len(collections)} total collections")
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
    
    def extract_user_from_tip(self, json):
        """Extract user info from tip data, similar to KomootGPX"""
        if "_embedded" in json and "creator" in json["_embedded"] and "display_name" in json["_embedded"]["creator"]:
            return json["_embedded"]["creator"]["display_name"] + ": "
        return ""
        
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
            
            # Ensure output directory is valid
            if not output_dir or output_dir == "undefined":
                output_dir = os.path.join(os.path.expanduser("~"), "komoot-takeout", "gpx")
                logger.warning(f"Invalid output directory provided, using default: {output_dir}")
            
            # Ensure the output directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            # First try using KomootGPX if available
            if KOMOOTGPX_AVAILABLE:
                logger.info("Trying KomootGPX for GPX generation")
                try:
                    # Prepare arguments for KomootGPX command
                    args = ["komootgpx"]
                    
                    # Authentication
                    if not anonymous and self.user_id and self.token:
                        args.extend(["-m", "dummy@example.com", "-p", self.token])
                    else:
                        args.append("-n")  # Anonymous mode
                        
                    # Tour ID
                    args.extend(["-d", str(tour_id)])
                    
                    # Output directory
                    args.extend(["-o", output_dir])
                    
                    # POI option
                    if not include_poi:
                        args.append("-e")
                        
                    # Description length
                    if max_desc_length >= 0:
                        args.extend(["--max-desc-length", str(max_desc_length)])
                        
                    # Title length
                    if max_title_length >= 0:
                        args.extend(["--max-title-length", str(max_title_length)])
                        
                    # Add date
                    if add_date:
                        args.append("-D")
                        
                    # Execute KomootGPX
                    logger.info(f"Running KomootGPX with args: {' '.join(args)}")
                    result = subprocess.run(args, capture_output=True, text=True)
                    
                    if result.returncode != 0:
                        logger.error(f"KomootGPX failed: {result.stderr}")
                        raise Exception("KomootGPX execution failed, falling back to built-in method")
                    
                    # Extract filename from output
                    output = result.stdout
                    filename_match = re.search(r"GPX file written to ['\"](.+?)['\"]", output)
                    if filename_match:
                        full_path = filename_match.group(1)
                        self.last_filename = os.path.basename(full_path)
                        logger.info(f"GPX file written to {full_path}")
                        
                        # Save tour data for reference
                        if not tour_base:
                            self.last_tour = self.fetch_tour(tour_id, anonymous=anonymous)
                        else:
                            self.last_tour = tour_base
                            
                        # Return GPX content if requested
                        if return_content:
                            with open(full_path, 'r', encoding='utf-8') as f:
                                return f.read()
                                
                        return True
                    
                except Exception as e:
                    logger.error(f"KomootGPX failed: {str(e)}")
                    logger.error(f"KomootGPX failed: KomootGPX execution failed, falling back to built-in method")
            
            # Fallback to direct API or our own implementation
            if anonymous:
                # For anonymous mode, try direct GPX API first
                try:
                    logger.info(f"Trying direct GPX API for tour {tour_id}")
                    gpx_url = f"https://www.komoot.com/api/v007/tours/{tour_id}/gpx"
                    response = requests.get(gpx_url)
                    
                    if response.status_code == 200:
                        gpx_content = response.text
                        
                        # Create filename
                        if max_title_length == 0:
                            filename = f"{tour_id}.gpx"
                        else:
                            # Try to get tour name
                            if not tour_base:
                                try:
                                    tour_base = self._scrape_tour_page(tour_id)
                                except:
                                    pass
                                    
                            # Set default name
                            tour_name = f"Tour_{tour_id}" if not tour_base else tour_base.get('name', f"Tour_{tour_id}")
                            tour_name = sanitize_filename(tour_name)
                            
                            # Apply length limit if needed
                            if max_title_length > 0 and len(tour_name) > max_title_length:
                                tour_name = tour_name[:max_title_length]
                                
                            filename = f"{tour_name}-{tour_id}.gpx"
                        
                        # Add date prefix if requested
                        if add_date and tour_base and 'date' in tour_base:
                            date_str = tour_base['date'][:10]
                            filename = f"{date_str}_{filename}"
                            
                        # Full path to file
                        output_path = os.path.join(output_dir, filename)
                        
                        # Save the file
                        with open(output_path, 'w', encoding='utf-8') as f:
                            f.write(gpx_content)
                            
                        # Store filename and tour data
                        self.last_filename = filename
                        self.last_tour = tour_base
                        
                        logger.info(f"GPX file written to {output_path}")
                        
                        # Return GPX content if requested
                        if return_content:
                            return gpx_content
                            
                        return True
                        
                except Exception as e:
                    logger.error(f"Direct GPX API failed: {str(e)}")
            
            tour = None
            if tour_base is None:
                logger.info(f"Fetching tour {tour_id} details")
                tour_base = self.fetch_tour(str(tour_id), anonymous=anonymous)
                tour = tour_base
                
            # Save the last tour for reference
            self.last_tour = tour_base
            
            # Example date: 2022-01-02T12:26:41.795+01:00
            # :10 extracts "2022-01-02" from this.
            date_str = ""
            if 'date' in tour_base and add_date:
                date_str = tour_base['date'][:10] + '_'
            
            # Create filename
            filename = sanitize_filename(tour_base['name'])
            if max_title_length == 0:
                filename = f"{tour_id}"
            elif max_title_length > 0 and len(filename) > max_title_length:
                filename = f"{filename[:max_title_length]}-{tour_id}"
            else:
                filename = f"{filename}-{tour_id}"
            
            # Full path
            path = os.path.join(output_dir, f"{date_str}{filename}.gpx")
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
                tour = self.fetch_tour(str(tour_id), anonymous=anonymous)
            
            # Create GPX using Python's gpxpy library if available
            if GPXPY_AVAILABLE:
                # Create basic GPX document
                gpx = gpxpy.gpx.GPX()
                gpx.name = tour['name']
                if tour.get('type') == "tour_recorded":
                    gpx.name = gpx.name + " (Completed)"
                
                # Add metadata
                distance_km = tour.get('distance', 0) / 1000.0 if 'distance' in tour else 0
                duration_hours = tour.get('duration', 0) / 3600.0 if 'duration' in tour else 0
                elevation_up = tour.get('elevation_up', 0) if 'elevation_up' in tour else 0
                elevation_down = tour.get('elevation_down', 0) if 'elevation_down' in tour else 0
                
                gpx.description = f"Distance: {distance_km:.2f}km, " \
                                f"Estimated duration: {duration_hours:.2f}h, " \
                                f"Elevation up: {elevation_up}m, " \
                                f"Elevation down: {elevation_down}m"
                
                if "difficulty" in tour:
                    gpx.description = gpx.description + f", Grade: {tour['difficulty']['grade']}"
                
                # Add author if available
                if '_embedded' in tour and 'creator' in tour['_embedded']:
                    creator = tour['_embedded']['creator']
                    gpx.author_name = creator.get('display_name', 'Komoot User')
                    if 'username' in creator:
                        gpx.author_link = f"https://www.komoot.de/user/{creator['username']}"
                        gpx.author_link_text = f"View {gpx.author_name}'s Profile on Komoot"
                
                gpx.link = f"https://www.komoot.de/tour/{tour_id}"
                gpx.link_text = "View tour on Komoot"
                
                # Create track
                track = gpxpy.gpx.GPXTrack()
                track.name = gpx.name
                track.description = gpx.description
                track.link = gpx.link
                track.link_text = gpx.link_text
                
                gpx.tracks.append(track)
                
                # Create segment
                segment = gpxpy.gpx.GPXTrackSegment()
                track.segments.append(segment)
                
                # Add points
                augment_timestamp = False
                start_date = None
                
                if "_embedded" in tour and "coordinates" in tour["_embedded"] and "items" in tour["_embedded"]["coordinates"]:
                    route = []
                    for coord in tour["_embedded"]["coordinates"]["items"]:
                        point = {}
                        if "lat" in coord and "lng" in coord:
                            point['lat'] = coord["lat"]
                            point['lng'] = coord["lng"]
                            if "alt" in coord:
                                point['alt'] = coord["alt"]
                            if "t" in coord:
                                point['time'] = coord["t"]
                            route.append(point)
                    
                    if route and 'time' in route[0] and route[0]['time'] == 0:
                        augment_timestamp = True
                        start_date = datetime.strptime(tour['date'], "%Y-%m-%dT%H:%M:%S.%f%z")
                    
                    # Add track points
                    for coord in route:
                        point = gpxpy.gpx.GPXTrackPoint(coord['lat'], coord['lng'])
                        
                        if 'alt' in coord:
                            point.elevation = coord['alt']
                            
                        if 'time' in coord:
                            if augment_timestamp:
                                try:
                                    point.time = start_date + timedelta(seconds=coord['time'] / 1000)
                                except:
                                    pass
                            else:
                                try:
                                    point.time = datetime.fromtimestamp(coord['time'] / 1000)
                                except:
                                    pass
                                
                        segment.points.append(point)
                
                # Add POIs/Highlights if not disabled
                if include_poi:
                    # When we have POIs, process them
                    if "_embedded" in tour and "timeline" in tour["_embedded"] and "_embedded" in tour["_embedded"]["timeline"]:
                        for item in tour["_embedded"]["timeline"]["_embedded"]["items"]:
                            if item["type"] != "poi" and item["type"] != "highlight":
                                continue
                            
                            ref = item["_embedded"]["reference"]
                            
                            wp = None
                            
                            if item["type"] == "poi":
                                # Handle regular POI
                                name = "Unknown POI"
                                location = {}
                                details = ""
                                
                                if "name" in ref:
                                    name = ref["name"]
                                    
                                if "lat" in ref and "lng" in ref:
                                    location['lat'] = ref["lat"]
                                    location['lng'] = ref["lng"]
                                    if "alt" in ref:
                                        location['alt'] = ref["alt"]
                                        
                                if "notes" in ref and "text" in ref["notes"]:
                                    details = ref["notes"]["text"]
                                    
                                if location:
                                    # Create waypoint
                                    wp = gpxpy.gpx.GPXWaypoint(location['lat'], location['lng'])
                                    wp.name = name
                                    if 'alt' in location:
                                        wp.elevation = location['alt']
                                    if details:
                                        wp.description = details
                            
                            elif item["type"] == "highlight":
                                # Handle highlight
                                name = "Unknown Highlight"
                                location = {}
                                details = ""
                                
                                if "name" in ref:
                                    name = ref["name"]
                                    
                                if "lat" in ref and "lng" in ref:
                                    location['lat'] = ref["lat"]
                                    location['lng'] = ref["lng"]
                                    if "alt" in ref:
                                        location['alt'] = ref["alt"]
                                        
                                # Try to get highlight description
                                if "description" in ref:
                                    details = ref["description"]
                                    
                                # Try to fetch additional tips (comments)
                                if "id" in ref:
                                    highlight_id = ref["id"]
                                    try:
                                        tips = self.fetch_highlight_tips(highlight_id)
                                        if "_embedded" in tips and "items" in tips["_embedded"]:
                                            comments = []
                                            for tip in tips["_embedded"]["items"]:
                                                user = self.extract_user_from_tip(tip)
                                                
                                                if "text" in tip:
                                                    comments.append(user + tip["text"])
                                            
                                            if comments:
                                                details = "\n――――――――――\n".join(comments)
                                    except Exception as e:
                                        logger.warning(f"Error fetching highlight tips: {str(e)}")
                                
                                # Crop description if needed
                                if max_desc_length == 0:
                                    details = ""
                                elif max_desc_length > 0 and details and len(details) > max_desc_length:
                                    details = details[:max_desc_length-3] + "..."
                                
                                if location:
                                    # Create waypoint
                                    wp = gpxpy.gpx.GPXWaypoint(location['lat'], location['lng'])
                                    wp.name = name
                                    if 'alt' in location:
                                        wp.elevation = location['alt']
                                    if details:
                                        wp.description = details
                                    
                            # Add waypoint if valid
                            if wp:
                                gpx.waypoints.append(wp)
                
                # Generate final XML
                gpx_content = gpx.to_xml()
            else:
                # Fallback to basic XML if gpxpy is not available
                logger.warning("gpxpy not available, using basic XML template")
                gpx_content = f"""<?xml version="1.0" encoding="UTF-8"?>
                <gpx version="1.1" creator="Komoot Collection Scraper">
                <metadata>
                    <name>{tour.get('name', 'Unknown Tour')}</name>
                    <time>{tour.get('date', datetime.now().isoformat())}</time>
                </metadata>
                <trk>
                    <name>{tour.get('name', 'Unknown Tour')}</name>
                </trk>
                </gpx>
                """
            
            # Create directory if needed
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            
            # Write to file
            logger.info(f"Writing GPX to file: {path}")
            with open(path, "w", encoding="utf-8") as f:
                f.write(gpx_content)
            
            # Return content if requested
            if return_content:
                return gpx_content
            return True
            
        except Exception as e:
            logger.error(f"Error generating GPX: {str(e)}")
            raise Exception(f"Failed to generate GPX: {str(e)}")
    
    def export_collection_to_json(self, collection, output_dir='static/exports/collections', enhance_tours=True, max_enhanced_tours=20):
        """Export a collection to JSON format with enhanced tour data
        
        Args:
            collection: The collection to export
            output_dir: Directory to save the JSON file
            enhance_tours: Whether to fetch full details for tours (default: True)
            max_enhanced_tours: Maximum number of tours to enhance to avoid long processing
            
        Returns:
            Path to the exported JSON file
        """
        try:
            # Create output directory if it doesn't exist
            os.makedirs(output_dir, exist_ok=True)
            
            # Create filename from collection name
            filename = sanitize_filename(collection['name'])
            if not filename:
                filename = f"collection_{collection['id']}"
            
            # Make a deep copy of the collection to avoid modifying the original
            import copy
            collection_to_export = copy.deepcopy(collection)
            
            # Ensure we have a tours list
            if 'tours' not in collection_to_export or collection_to_export['tours'] is None:
                collection_to_export['tours'] = []
                logger.warning(f"Collection {collection_to_export.get('id', 'unknown')} had no tours array, adding empty one")
            
            # Enhance tours with full data if requested
            if enhance_tours and collection_to_export['tours']:
                logger.info(f"Enhancing tours for collection {collection_to_export['name']}")
                
                # Limit the number of tours to enhance if needed
                tours_to_enhance = collection_to_export['tours']
                if max_enhanced_tours > 0 and len(tours_to_enhance) > max_enhanced_tours:
                    logger.info(f"Limiting tour enhancement to first {max_enhanced_tours} of {len(tours_to_enhance)} tours")
                    tours_to_enhance = tours_to_enhance[:max_enhanced_tours]
                
                # Enhance each tour with additional details
                enhanced_tours = []
                for i, tour in enumerate(tours_to_enhance):
                    tour_id = tour['id']
                    try:
                        logger.info(f"Enhancing tour {i+1}/{len(tours_to_enhance)}: {tour_id}")
                        
                        # Try to get full tour data through different methods
                        full_tour = None
                        
                        # Method 1: Try HTML scraping as it's faster
                        try:
                            full_tour = self._scrape_tour_page(tour_id)
                            if full_tour:
                                logger.info(f"Retrieved tour data via scraping for {tour_id}")
                        except Exception as scrape_err:
                            logger.warning(f"Error scraping tour page: {str(scrape_err)}")
                        
                        # Method 2: If scraping failed or returned minimal data, try API call
                        if not full_tour or full_tour.get('name') == f"Tour {tour_id}":
                            try:
                                full_tour = self.fetch_tour(tour_id, anonymous=True)
                                if full_tour:
                                    logger.info(f"Retrieved tour data via API for {tour_id}")
                            except Exception as api_err:
                                logger.warning(f"Error fetching tour via API: {str(api_err)}")
                        
                        # Enhance the tour object with the retrieved data
                        if full_tour:
                            # Start with original tour data and update with new details
                            enhanced_tour = {**tour}
                            
                            # Copy important fields from the full tour data
                            for key in ['name', 'sport', 'distance', 'distance_km', 'duration', 
                                        'elevation_up', 'elevation_down', 'date', 'type']:
                                if key in full_tour and full_tour[key]:
                                    enhanced_tour[key] = full_tour[key]
                            
                            # Calculate derived fields if needed
                            if 'distance' in full_tour and full_tour['distance'] and 'distance_km' not in enhanced_tour:
                                enhanced_tour['distance_km'] = full_tour['distance'] / 1000
                            
                            if 'duration' in full_tour and full_tour['duration'] and 'duration_hours' not in enhanced_tour:
                                enhanced_tour['duration_hours'] = full_tour['duration'] / 3600
                            
                            # Add to enhanced tours list
                            enhanced_tours.append(enhanced_tour)
                        else:
                            # If enhancement failed, keep original tour data
                            enhanced_tours.append(tour)
                            
                    except Exception as e:
                        logger.error(f"Error enhancing tour {tour_id}: {str(e)}")
                        enhanced_tours.append(tour)  # Keep original tour data
                
                # Add any remaining unenhanced tours
                if max_enhanced_tours > 0 and len(collection_to_export['tours']) > max_enhanced_tours:
                    enhanced_tours.extend(collection_to_export['tours'][max_enhanced_tours:])
                
                # Update collection with enhanced tours
                collection_to_export['tours'] = enhanced_tours
                
                # Add enhancement metadata
                collection_to_export['enhanced'] = True
                collection_to_export['enhanced_timestamp'] = datetime.now().isoformat()
                collection_to_export['enhanced_count'] = len(tours_to_enhance)
            
            # Ensure we have all required collection fields
            if 'id' not in collection_to_export:
                collection_to_export['id'] = str(hash(collection_to_export['name']))
            
            if 'name' not in collection_to_export:
                collection_to_export['name'] = f"Collection {collection_to_export['id']}"
            
            # Save to file
            path = os.path.join(output_dir, f"{filename}-{collection_to_export['id']}.json")
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(collection_to_export, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Exported collection '{collection_to_export['name']}' to {path}")
            return path
            
        except Exception as e:
            logger.error(f"Error exporting collection to JSON: {str(e)}")
            return None
    
    def export_collection_to_csv(self, collection, output_dir='static/exports/collections', enhance_tours=True, max_enhanced_tours=20):
        """Export collection's tours to CSV format with enhanced tour data
        
        Args:
            collection: The collection to export
            output_dir: Directory to save the CSV file 
            enhance_tours: Whether to fetch full details for tours (default: True)
            max_enhanced_tours: Maximum number of tours to enhance to avoid long processing
            
        Returns:
            Path to the exported CSV file
        """
        try:
            os.makedirs(output_dir, exist_ok=True)
            
            # Create filename from collection name
            filename = sanitize_filename(collection['name'])
            if not filename:
                filename = f"collection_{collection['id']}"
            
            # Enhance tours with full data if requested
            collection_to_export = copy.deepcopy(collection)
            if enhance_tours and collection_to_export.get('tours'):
                # Limit the number of tours to enhance for performance
                tours_to_enhance = collection_to_export['tours'][:max_enhanced_tours]
                enhanced_count = 0
                
                for i, tour in enumerate(tours_to_enhance):
                    try:
                        # Only enhance if it looks like basic data (name is just "Tour ID")
                        tour_id = tour['id']
                        if tour['name'].startswith(f"Tour {tour_id}"):
                            logger.info(f"Enhancing tour {i+1}/{len(tours_to_enhance)}: {tour_id}")
                            tour_data = self._scrape_tour_page(tour_id)
                            if tour_data and 'name' in tour_data and tour_data['name'] != f"Tour {tour_id}":
                                # Update with enhanced data
                                for key, value in tour_data.items():
                                    if key not in tour or not tour[key]:
                                        tour[key] = value
                                enhanced_count += 1
                    except Exception as e:
                        logger.error(f"Error enhancing tour {tour.get('id')}: {str(e)}")
                        
                logger.info(f"Enhanced {enhanced_count}/{len(tours_to_enhance)} tours in collection")
            
            # Current timestamp for all records
            current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # CSV file path
            csv_path = os.path.join(output_dir, f"{filename}.csv")
            
            # Define fields to include in the CSV - with bikepacking.com format in mind
            fieldnames = [
                "id", "timestamp", "name", "distance_km", "distance_mi", "duration",
                "unpaved_percentage", "singletrack_percentage", "rideable_percentage",
                "total_ascent", "total_descent", "high_point", "climbing_intensity",
                "country", "region", "collection_name", "collection_id", 
                "sport_type", "description", "url", "gpx_url", "image_url", 
                "collection_cover_image", "date_created"
            ]
            
            # Process tours to CSV format
            csv_tours = []
            for tour in collection_to_export.get('tours', []):
                try:
                    # Handle distance - convert from meters to km if needed
                    distance_km = 0
                    if tour.get('distance_km') is not None:
                        distance_km = float(tour.get('distance_km'))
                    elif tour.get('distance') is not None:
                        distance_km = float(tour.get('distance')) / 1000
                    
                    # Handle duration - convert from seconds to hours if needed
                    duration_hours = 0
                    if tour.get('duration_hours') is not None:
                        duration_hours = float(tour.get('duration_hours'))
                    elif tour.get('duration') is not None:
                        duration_hours = float(tour.get('duration')) / 3600
                        
                    # Create standardized tour object
                    csv_tour = {
                        "id": tour.get('id', ''),
                        "timestamp": current_timestamp,
                        "name": tour.get('name', f"Tour {tour.get('id', 'unknown')}"),
                        "distance_km": f"{distance_km:.1f}" if distance_km else "",
                        "distance_mi": f"{distance_km * 0.621371:.1f}" if distance_km else "",
                        "duration": f"{duration_hours:.1f}" if duration_hours else "",
                        "unpaved_percentage": tour.get('unpaved_percentage', ''),
                        "singletrack_percentage": tour.get('singletrack_percentage', ''),
                        "rideable_percentage": tour.get('rideable_percentage', ''),
                        "total_ascent": tour.get('elevation_up', ''),
                        "total_descent": tour.get('elevation_down', ''),
                        "high_point": tour.get('high_point', ''),
                        "country": tour.get('country', ''),
                        "region": collection_to_export.get('region', ''),
                        "collection_name": collection_to_export.get('name', ''),
                        "collection_id": collection_to_export.get('id', ''),
                        "sport_type": tour.get('sport', ''),
                        "description": tour.get('description', ''),
                        "url": tour.get('url', f"https://www.komoot.com/tour/{tour.get('id', '')}"),
                        "gpx_url": tour.get('gpx_url', ''),
                        "image_url": tour.get('image_url', ''),
                        "collection_cover_image": collection_to_export.get('cover_image_url', ''),
                        "date_created": tour.get('date', '')[:10] if tour.get('date') else ''
                    }
                    
                    # Add calculated fields
                    if tour.get('elevation_up') and distance_km:
                        try:
                            elevation_up = float(tour.get('elevation_up'))
                            # Calculate meters climbed per kilometer
                            meters_per_km = elevation_up / distance_km
                            csv_tour["climbing_intensity"] = f"{meters_per_km:.1f}"
                        except (ValueError, TypeError):
                            csv_tour["climbing_intensity"] = ""
                    else:
                        csv_tour["climbing_intensity"] = ""
                        
                    csv_tours.append(csv_tour)
                except Exception as e:
                    logger.error(f"Error processing tour {tour.get('id', 'unknown')} for CSV: {str(e)}")
            
            # Only include fields that have data
            used_fields = set()
            for tour in csv_tours:
                for field, value in tour.items():
                    if value not in (None, ""):
                        used_fields.add(field)
            
            # Always include essential fields
            essential_fields = ["id", "name", "url", "timestamp"]
            for field in essential_fields:
                used_fields.add(field)
                
            # Filter fieldnames to only include fields that have data
            filtered_fieldnames = [f for f in fieldnames if f in used_fields or f in essential_fields]
            
            # Write CSV file
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=filtered_fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(csv_tours)
                
            logger.info(f"Exported {len(csv_tours)} tours from collection '{collection_to_export.get('name', 'Unknown')}' to {csv_path}")
            return csv_path
            
        except Exception as e:
            logger.error(f"Error exporting collection to CSV: {str(e)}")
            return None
    
    def get_last_filename(self):
        """Get the filename from the last GPX generation"""
        return self.last_filename
        
    def get_last_tour(self):
        """Get the tour data from the last GPX generation"""
        return self.last_tour

    def enhance_collection_tours(self, collection, max_tours=None):
        """
        Enhance tours in a collection with better metadata
        
        Args:
            collection: The collection object to enhance
            max_tours: Maximum number of tours to enhance (None for all)
            
        Returns:
            Enhanced collection object
        """
        try:
            # Make a deep copy to avoid modifying the original
            import copy
            enhanced_collection = copy.deepcopy(collection)
            
            # Ensure tours array exists
            if 'tours' not in enhanced_collection or enhanced_collection['tours'] is None:
                enhanced_collection['tours'] = []
                
            # Return early if no tours
            if not enhanced_collection['tours']:
                logger.warning(f"No tours to enhance in collection {enhanced_collection.get('name', 'Unknown')}")
                return enhanced_collection
                
            # Limit tours to process if specified
            tours = enhanced_collection['tours']
            if max_tours is not None:
                tours = tours[:max_tours]
            
            logger.info(f"Enhancing {len(tours)} tours in collection {enhanced_collection.get('name', 'Unknown')}")
            
            # Process each tour
            enhanced_count = 0
            for i, tour in enumerate(tours):
                try:
                    # Only enhance if tour appears to need it (generic name like "Tour 12345")
                    tour_id = tour['id']
                    if tour['name'].startswith(f"Tour {tour_id}"):
                        logger.info(f"Enhancing tour {i+1}/{len(tours)}: {tour_id}")
                        
                        # Try HTML scraping for tour details
                        tour_data = self._scrape_tour_page(tour_id)
                        if tour_data and 'name' in tour_data and tour_data['name'] != f"Tour {tour_id}":
                            # Update tour with better data
                            for key, value in tour_data.items():
                                if key not in tour or not tour[key]:
                                    tour[key] = value
                            enhanced_count += 1
                            
                except Exception as e:
                    logger.error(f"Error enhancing tour {tour.get('id', 'unknown')}: {str(e)}")
                    
            logger.info(f"Enhanced {enhanced_count}/{len(tours)} tours in collection {enhanced_collection.get('name', 'Unknown')}")
            return enhanced_collection
            
        except Exception as e:
            logger.error(f"Error enhancing collection tours: {str(e)}")
            return collection  # Return original if enhancement failed