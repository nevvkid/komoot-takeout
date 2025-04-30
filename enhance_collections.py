#!/usr/bin/env python3
"""
enhance_collections.py - Script to enhance tour names in Komoot collection exports

This script loads collection JSON files and enhances tour data to provide proper 
descriptive names instead of generic "Tour ID" format.
"""

import os
import json
import sys
import argparse
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Check if current directory is in sys.path
if not os.getcwd() in sys.path:
    sys.path.append(os.getcwd())

try:
    from komoot_adapter import KomootAdapter
    logger.info("Successfully imported KomootAdapter")
except ImportError as e:
    logger.error(f"Error importing KomootAdapter: {e}")
    print("Error: Could not import KomootAdapter. Make sure you're running this script from the root directory of the project.")
    sys.exit(1)

def enhance_collection_file(file_path):
    """
    Enhance tour data in a collection JSON file
    
    Args:
        file_path: Path to the JSON collection file
    """
    try:
        # Make sure file exists
        if not os.path.exists(file_path):
            logger.error(f"File does not exist: {file_path}")
            return False
            
        logger.info(f"Processing collection file: {file_path}")
        
        # Load the JSON data
        with open(file_path, 'r', encoding='utf-8') as f:
            collections = json.load(f)
            
        if not collections:
            logger.warning(f"No collections found in file: {file_path}")
            return False
            
        # Create adapter instance
        adapter = KomootAdapter()
        
        # Process each collection
        enhanced_collections = []
        total_enhanced_tours = 0
        
        for collection in collections:
            logger.info(f"Enhancing collection: {collection.get('name', 'Unknown')}")
            
            try:
                # Use the enhance_collection_tours method to improve tour data
                enhanced_collection = adapter.enhance_collection_tours(collection, max_tours=None)
                
                # Count how many tours were actually enhanced (name changed)
                enhanced_count = 0
                if 'tours' in enhanced_collection:
                    for tour in enhanced_collection['tours']:
                        if not tour['name'].startswith(f"Tour {tour['id']}"):
                            enhanced_count += 1
                            
                logger.info(f"Enhanced {enhanced_count} tour names in collection '{collection.get('name', 'Unknown')}'")
                total_enhanced_tours += enhanced_count
                
                enhanced_collections.append(enhanced_collection)
                
            except Exception as e:
                logger.error(f"Error enhancing collection {collection.get('name', 'Unknown')}: {e}")
                # Keep the original collection if enhancement fails
                enhanced_collections.append(collection)
        
        # Create a backup of the original file
        backup_path = f"{file_path}.bak"
        logger.info(f"Creating backup at: {backup_path}")
        with open(backup_path, 'w', encoding='utf-8') as f:
            with open(file_path, 'r', encoding='utf-8') as original:
                f.write(original.read())
        
        # Save the enhanced collections back to the file
        logger.info(f"Saving enhanced collections to: {file_path}")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(enhanced_collections, f, indent=2, ensure_ascii=False)
            
        logger.info(f"Successfully enhanced {total_enhanced_tours} tour names across {len(enhanced_collections)} collections")
        return True
        
    except Exception as e:
        logger.error(f"Error processing file {file_path}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Enhance tour names in Komoot collection exports')
    parser.add_argument('file', type=str, help='Path to the collection JSON file to enhance')
    args = parser.parse_args()
    
    # Resolve the file path
    file_path = os.path.abspath(args.file)
    
    if not os.path.exists(file_path):
        print(f"Error: File does not exist: {file_path}")
        return 1
        
    if enhance_collection_file(file_path):
        print(f"\nSuccess! Enhanced tour names in: {file_path}")
        print("A backup of the original file was created with .bak extension.")
        return 0
    else:
        print(f"\nError: Failed to enhance tour names in: {file_path}")
        return 1

if __name__ == "__main__":
    sys.exit(main())