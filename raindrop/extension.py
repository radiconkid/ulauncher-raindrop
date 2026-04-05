import logging
import os
import hashlib
import requests
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from collections import OrderedDict

from ulauncher.api.client.Extension import Extension
from ulauncher.api.shared.event import KeywordQueryEvent, PreferencesEvent, PreferencesUpdateEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.CopyToClipboardAction import CopyToClipboardAction
from raindrop.preferences import PreferencesEventListener, PreferencesUpdateEventListener
from raindropio import Raindrop, CollectionRef
from raindrop.query_listener import KeywordQueryEventListener


class EnhancedSearchCache:
    """Enhanced cache class with memory and disk tiers, LRU eviction"""
    
    def __init__(self, cache_dir="search_cache", ttl_minutes=5, max_memory_items=50):
        self.cache_dir = cache_dir
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_memory_items = max_memory_items
        
        # Memory cache (LRU - most recently used at end)
        self.memory_cache = OrderedDict()
        self.memory_cache_lock = threading.Lock()
        
        # Create cache directory if it doesn't exist
        extension_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache_dir = os.path.join(extension_base_dir, self.cache_dir)
        Path(self.cache_dir).mkdir(exist_ok=True)
        
        # Cache statistics
        self.stats = {
            'memory_hits': 0,
            'disk_hits': 0,
            'misses': 0,
            'memory_evictions': 0
        }
    
    def _get_cache_key(self, query_type, query):
        """Generate cache key from query type and query"""
        key = f"{query_type}:{query}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key):
        """Get full path for cache file"""
        return os.path.join(self.cache_dir, f"{cache_key}.cache")
    
    def _evict_memory_cache_if_needed(self):
        """Evict least recently used items if memory cache is full"""
        with self.memory_cache_lock:
            # Simple eviction: just check if we're over the limit
            if len(self.memory_cache) > self.max_memory_items:
                # Evict just one item to avoid long lock holding
                self.memory_cache.popitem(last=False)
                self.stats['memory_evictions'] += 1
    
    def get(self, query_type, query):
        """Get cached results if available and not expired"""
        cache_key = self._get_cache_key(query_type, query)
        
        try:
            # First try memory cache
            with self.memory_cache_lock:
                if cache_key in self.memory_cache:
                    cached_data = self.memory_cache[cache_key]
                    # Move to end to mark as recently used
                    self.memory_cache.move_to_end(cache_key)
                    self.stats['memory_hits'] += 1
                    
                    # Check if cache is expired
                    if (datetime.now() - cached_data['timestamp']) > self.ttl:
                        self.memory_cache.pop(cache_key, None)
                        return None
                    
                    return cached_data['results']
            
            # Try disk cache if not in memory
            cache_path = self._get_cache_path(cache_key)
            
            if not os.path.exists(cache_path):
                self.stats['misses'] += 1
                return None
            
            # Read cache file with timeout
            try:
                with open(cache_path, 'rb') as f:
                    import pickle
                    cached_data = pickle.load(f)
                    
                # Check if cache is expired
                cache_time = cached_data.get('timestamp')
                if cache_time and (datetime.now() - cache_time) > self.ttl:
                    try:
                        os.remove(cache_path)
                    except:
                        pass
                    self.stats['misses'] += 1
                    return None
                
                # Add to memory cache for future access
                with self.memory_cache_lock:
                    self.memory_cache[cache_key] = cached_data
                    self.memory_cache.move_to_end(cache_key)
                    self._evict_memory_cache_if_needed()
                
                self.stats['disk_hits'] += 1
                return cached_data.get('results')
            except Exception as e:
                logger.debug(f"Failed to read cache from disk: {str(e)}")
                # If any error occurs, remove cache file and return None
                try:
                    if os.path.exists(cache_path):
                        os.remove(cache_path)
                except:
                    pass
                self.stats['misses'] += 1
                return None
        except Exception as e:
            logger.error(f"Cache get operation failed: {str(e)}")
            self.stats['misses'] += 1
            return None
    
    def set(self, query_type, query, results):
        """Store results in cache"""
        cache_key = self._get_cache_key(query_type, query)
        cache_path = self._get_cache_path(cache_key)
        
        # Create cached data
        cached_data = {
            'timestamp': datetime.now(),
            'results': results
        }
        
        # Add to memory cache
        with self.memory_cache_lock:
            self.memory_cache[cache_key] = cached_data
            self.memory_cache.move_to_end(cache_key)
        
        # Handle eviction after releasing the lock
        self._evict_memory_cache_if_needed()
        
        # Write to disk cache synchronously but with timeout protection
        try:
            import pickle
            with open(cache_path, 'wb') as f:
                pickle.dump(cached_data, f)
        except Exception as e:
            # Log error but don't block main operation
            logger.warning(f"Failed to write cache to disk: {str(e)}")
    
    def clear(self):
        """Clear all cached results"""
        with self.memory_cache_lock:
            self.memory_cache.clear()
        
        try:
            for cache_file in Path(self.cache_dir).glob("*.cache"):
                cache_file.unlink()
        except:
            pass
        
        # Reset statistics
        self.stats = {
            'memory_hits': 0,
            'disk_hits': 0,
            'misses': 0,
            'memory_evictions': 0
        }
    
    def get_stats(self):
        """Get cache statistics"""
        with self.memory_cache_lock:
            return {
                **self.stats,
                'memory_cache_size': len(self.memory_cache),
                'disk_cache_size': len(list(Path(self.cache_dir).glob("*.cache")))
            }


def get_favicon_url(drop):
    """Extract favicon URL from Raindrop object"""
    if not drop or not hasattr(drop, 'media'):
        return None
    
    if drop.media:
        # Look for favicon in media array
        for media in drop.media:
            if media.get('type') == 'image/favicon':
                return media.get('link')
            # Sometimes favicon might be in 'image/png' or other image types
            if media.get('type') and media.get('type').startswith('image/'):
                return media.get('link')
    
    # If no favicon found in media, try to construct from domain
    if hasattr(drop, 'domain') and drop.domain:
        return f"https://www.google.com/s2/favicons?domain={drop.domain}"
    
    return None


def get_favicon_path(drop, cache_dir="favicon_cache"):
    """Get local path for favicon, using simple approach"""
    # For now, just use Google's favicon service for all sites
    # This is the simplest approach that should work for most sites
    
    domain = None
    
    # Try to get domain from drop.domain
    if hasattr(drop, 'domain') and drop.domain:
        domain = drop.domain
    
    # Try to get domain from drop.link if domain not found
    if not domain and hasattr(drop, 'link') and drop.link:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(drop.link)
            domain = parsed.netloc
        except:
            pass
    
    # If we have a domain, try to get favicon
    if domain:
        favicon_url = f"https://www.google.com/s2/favicons?domain={domain}"
        
        # Create cache directory if it doesn't exist
        import os
        from pathlib import Path
        extension_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_dir = os.path.join(extension_base_dir, cache_dir)
        Path(cache_dir).mkdir(exist_ok=True)
        
        # Generate filename from URL hash
        import hashlib
        url_hash = hashlib.md5(favicon_url.encode()).hexdigest()
        cache_path = os.path.join(cache_dir, f"{url_hash}.png")
        
        # Return cached file if it exists
        if os.path.exists(cache_path):
            return cache_path
        
        # Try to download favicon (simple approach)
        try:
            import requests
            response = requests.get(favicon_url, timeout=3)
            if response.status_code == 200:
                with open(cache_path, 'wb') as f:
                    f.write(response.content)
                return cache_path
        except:
            pass  # Silently fail and use default icon
    
    # Use default icon if favicon not available
    return "images/icon.png"

# Configure simple logging to avoid potential deadlocks
logging.basicConfig(
    level=logging.WARNING,  # Start with WARNING level to reduce noise
    format='%(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def with_timeout(timeout_seconds, default=None):
    """Decorator to add timeout to function execution"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            import threading
            import queue
            
            result_queue = queue.Queue()
            
            def worker():
                try:
                    result = func(*args, **kwargs)
                    result_queue.put(result)
                except Exception as e:
                    result_queue.put(e)
            
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(timeout=timeout_seconds)
            
            if thread.is_alive():
                logger.warning(f"Function {func.__name__} timed out after {timeout_seconds} seconds")
                return default
            
            result = result_queue.get()
            if isinstance(result, Exception):
                raise result
            return result
        return wrapper
    return decorator


def with_retry(max_retries=2, retry_delay=0.5):
    """Decorator to add retry logic to function execution"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = retry_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Attempt {attempt + 1} failed, retrying in {current_delay} seconds: {str(e)}")
                        import time
                        time.sleep(current_delay)
                        # Exponential backoff
                        current_delay *= 2
            
            logger.error(f"Function {func.__name__} failed after {max_retries} attempts: {str(last_exception)}")
            raise last_exception
        return wrapper
    return decorator


class RaindropExtension(Extension):
    """ Main Extension Class  """

    def __init__(self):
        """ Initializes the extension """
        super(RaindropExtension, self).__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())

        self.subscribe(PreferencesEvent, PreferencesEventListener())
        self.subscribe(PreferencesUpdateEvent,
                       PreferencesUpdateEventListener())
        
        # Initialize rd_client as None, will be set in PreferencesEventListener
        self.rd_client = None
        
        # Initialize enhanced search cache with longer TTL (30 minutes)
        self.search_cache = EnhancedSearchCache(
            cache_dir="search_cache", 
            ttl_minutes=30, 
            max_memory_items=150
        )
        
        # Load version from manifest.json
        self._load_version()
    
    def _load_version(self):
        """Load version from manifest.json"""
        try:
            import json
            manifest_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'manifest.json')
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                self.version = manifest.get('version', 'unknown')
        except:
            self.version = 'unknown'

    def get_keyword_id(self, keyword):
        for key, value in self.preferences.items():
            if value == keyword:
                return key

        return ""

    def show_open_app_menu(self):
        """ Shows the menu to Open Raindrop website """
        cache_stats = self.search_cache.get_stats()
        cache_info = f"Cache: {cache_stats['memory_hits']} mem hits, {cache_stats['disk_hits']} disk hits, {cache_stats['misses']} misses"
        
        return RenderResultListAction([
            ExtensionResultItem(
                icon='images/icon.png',
                name='Open Raindrop Website',
                on_enter=OpenUrlAction('https://app.raindrop.io')),
            ExtensionResultItem(
                icon='images/icon.png',
                name=f'Raindrop Extension v{self.version}',
                description='Current extension version',
                highlightable=False),
            ExtensionResultItem(
                icon='images/icon.png',
                name='Cache Statistics',
                description=cache_info,
                highlightable=False),

        ])

    def search(self, query):
        # Try to get cached results first
        cache_key = f"search:{query}"
        cached_results = self.search_cache.get("search", query)
        
        if cached_results is not None:
            return RenderResultListAction(cached_results)
        
        # Get access token from preferences
        access_token = self.preferences.get('access_token')
        
        # Check if access token is available
        if not access_token:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name='Raindrop access token not set. Please configure the extension.',
                    highlightable=False)
            ])
        
        # Initialize or update rd_client if needed
        if not self.rd_client or (hasattr(self, '_last_token') and self._last_token != access_token):
            from raindropio import API
            self.rd_client = API(access_token)
            self._last_token = access_token
        
        try:
            # Use timeout for the actual API call
            import functools
            
            # Create a timeout wrapper for the Raindrop.search call
            def _search_with_timeout():
                return Raindrop.search(
                    self.rd_client,
                    word=query,
                    perpage=10,
                    collection=CollectionRef({"$id": 0}),
                )
            
            # Apply retry and timeout to the API call with more aggressive settings
            search_func = with_retry(max_retries=1)(_search_with_timeout)  # Reduced retries
            search_func_with_timeout = with_timeout(5)(search_func)  # Reduced timeout
            drops = search_func_with_timeout()

            # If API call failed or returned None due to timeout
            if drops is None:
                # Try to return cached results for similar queries as fallback
                similar_cached_results = None
                if len(query) > 3:  # Only try similar queries for longer search terms
                    # Try to find cached results for shorter versions of the query
                    for i in range(min(3, len(query)-1)):
                        shorter_query = query[:-i-1]  # Remove last i+1 characters
                        similar_cached_results = self.search_cache.get("search", shorter_query)
                        if similar_cached_results is not None:
                            return RenderResultListAction([
                                ExtensionResultItem(
                                    icon='images/icon.png',
                                    name=f'Showing cached results for: {shorter_query}',
                                    description='Raindrop API is slow. Showing similar cached results.',
                                    highlightable=False)
                            ] + similar_cached_results)
                
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name='Search timed out',
                        description='Raindrop API is slow or unavailable. Try again later.',
                        highlightable=False)
                ])

            if len(drops) == 0:
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name='No results found matching your criteria',
                        highlightable=False)
                ])

            items = []
            # Get favicon setting from preferences
            show_favicons = self.preferences.get('show_favicons', True)
            
            for drop in drops:
                # Use favicon if enabled, otherwise use default icon
                icon_path = get_favicon_path(drop) if show_favicons else "images/icon.png"
                items.append(
                    ExtensionResultItem(icon=icon_path,
                                        name=drop.title,
                                        description=drop.excerpt,
                                        on_enter=OpenUrlAction(drop.link)))
            
            # Cache the results
            self.search_cache.set("search", query, items)
            
            return RenderResultListAction(items)
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name=f'Error searching: {str(e)}',
                    highlightable=False)
            ])

    def search_by_tag(self, tag):
        """ Search bookmarks by tag """
        # Try to get cached results first
        cached_results = self.search_cache.get("tag", tag)
        
        if cached_results is not None:
            return RenderResultListAction(cached_results)
        
        # Get access token from preferences
        access_token = self.preferences.get('access_token')
        
        # Check if access token is available
        if not access_token:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name='Raindrop access token not set. Please configure the extension.',
                    highlightable=False)
            ])
        
        # Check if tag is provided
        if not tag:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name='Please provide a tag to search for',
                    highlightable=False)
            ])
        
        # Initialize or update rd_client if needed
        if not self.rd_client or (hasattr(self, '_last_token') and self._last_token != access_token):
            from raindropio import API
            self.rd_client = API(access_token)
            self._last_token = access_token
        
        # Search by tag using Raindrop API
        try:
            from raindropio import Raindrop, CollectionRef
            
            # Use timeout for the API call
            def _search_by_tag_with_timeout():
                return Raindrop.search(
                    self.rd_client,
                    tag=tag,
                    perpage=100,
                    collection=CollectionRef({"$id": 0}),
                )
            
            # Apply retry and timeout to the API call with more aggressive settings
            search_func = with_retry(max_retries=1)(_search_by_tag_with_timeout)  # Reduced retries
            search_func_with_timeout = with_timeout(8)(search_func)  # Reduced timeout
            drops = search_func_with_timeout()

            # If API call failed or returned None due to timeout
            if drops is None:
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name='Tag search timed out',
                        description='Raindrop API is slow or unavailable. Try again later.',
                        highlightable=False)
                ])

            if len(drops) == 0:
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name=f'No bookmarks found with tag: {tag}',
                        highlightable=False)
                ])

            items = []
            # Get favicon setting from preferences
            show_favicons = self.preferences.get('show_favicons', True)
            
            for drop in drops:
                # Use favicon if enabled, otherwise use default icon
                icon_path = get_favicon_path(drop) if show_favicons else "images/icon.png"
                items.append(
                    ExtensionResultItem(icon=icon_path,
                                        name=drop.title,
                                        description=drop.excerpt,
                                        on_enter=OpenUrlAction(drop.link)))
            
            # Cache the results
            self.search_cache.set("tag", tag, items)
            
            return RenderResultListAction(items)
            
        except Exception as e:
            logger.error(f"Tag search failed: {str(e)}")
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name=f'Error searching by tag: {str(e)}',
                    highlightable=False)
            ])

    def unsorted(self, query):
        # Get access token from preferences
        access_token = self.preferences.get('access_token')
        
        # Check if access token is available
        if not access_token:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name='Raindrop access token not set. Please configure the extension.',
                    highlightable=False)
            ])
        
        # Initialize or update rd_client if needed
        if not self.rd_client or (hasattr(self, '_last_token') and self._last_token != access_token):
            from raindropio import API
            self.rd_client = API(access_token)
            self._last_token = access_token
        
        drops = Raindrop.search(
            self.rd_client,
            word=query,
            perpage=10,
            collection=CollectionRef({"$id": -1}),
        )

        if len(drops) == 0:
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name='No results found matching your criteria',
                    highlightable=False)
            ])

        items = []
        # Get favicon setting from preferences
        show_favicons = self.preferences.get('show_favicons', True)
        
        for drop in drops:
            # Use favicon if enabled, otherwise use default icon
            icon_path = get_favicon_path(drop) if show_favicons else "images/icon.png"
            items.append(
                ExtensionResultItem(icon=icon_path,
                                    name=drop.title,
                                    description=drop.excerpt,
                                    on_enter=OpenUrlAction(drop.link)))
        return RenderResultListAction(items)
