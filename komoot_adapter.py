import os
import io
from komootgpx.api import KomootApi
from komootgpx.gpxcompiler import GpxCompiler

class KomootAdapter:
    """
    Adapter class to integrate KomootGPX with Flask
    """
    def __init__(self):
        self.api = KomootApi()
        self.last_filename = None
        self.last_tour = None
        
    def login(self, email, password):
        """Login to Komoot"""
        self.api.login(email, password)
    
    def get_display_name(self):
        """Get the display name of the logged-in user"""
        if hasattr(self.api, 'user_display_name'):
            return self.api.user_display_name
        return "Unknown User"
    
    def fetch_tours(self, tour_type="all", silent=False):
        """Fetch tours from Komoot"""
        return self.api.fetch_tours(tour_type, silent)
        
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
        tour = None
        if tour_base is None:
            tour_base = self.api.fetch_tour(str(tour_id))
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
        
        # Check if file already exists
        if skip_existing and os.path.exists(path):
            if return_content:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            return None
        
        # Fetch tour if not already fetched
        if tour is None:
            tour = self.api.fetch_tour(str(tour_id))
            
        # Create GPX
        gpx = GpxCompiler(tour, self.api, not include_poi, max_desc_length)
        gpx_content = gpx.generate()
        
        # Create directory if needed
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        
        # Write to file
        with open(path, "w", encoding="utf-8") as f:
            f.write(gpx_content)
        
        # Return content if requested
        if return_content:
            return gpx_content
        return None
    
    def get_last_filename(self):
        """Get the last generated filename"""
        return self.last_filename
        
    def get_last_tour(self):
        """Get the last processed tour"""
        return self.last_tour