import logging
import os
import hashlib
import requests
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ulauncher.api import Extension, Result
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.internals.effects import set_query
from raindropio import Raindrop, CollectionRef


def create_retry_session(retries=3, backoff_factor=0.5, 
                        status_forcelist=(500, 502, 504, 107)):
    """Create a requests Session with retry strategy"""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

class SearchCache:
    """Cache class for storing search results with enhanced features"""
    
    def __init__(self, cache_dir="search_cache", ttl_minutes=5):
        self.cache_dir = cache_dir
        self.ttl = timedelta(minutes=ttl_minutes)
        
        # Create cache directory if it doesn't exist
        extension_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache_dir = os.path.join(extension_base_dir, self.cache_dir)
        Path(self.cache_dir).mkdir(exist_ok=True)
        
        # Add cache statistics
        self.stats = {
            'hits': 0,
            'misses': 0
        }
        
        # Add dynamic TTL adjustment
        self.dynamic_ttl_enabled = True
        self.min_ttl = timedelta(minutes=1)
        self.max_ttl = timedelta(minutes=30)
    
    def _get_cache_key(self, query_type, query):
        """Generate cache key from query type and query"""
        key = f"{query_type}:{query}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key):
        """Get full path for cache file"""
        return os.path.join(self.cache_dir, f"{cache_key}.cache")
    
    def get_by_prefix(self, query_type, query_prefix):
        """Try to get results for a query starting with prefix (for debouncing)"""
        # Look for cached files that match this prefix pattern
        try:
            cache_files = Path(self.cache_dir).glob(f"{query_type}:*.cache")
            
            for cache_file in cache_files:
                try:
                    with open(cache_file, 'rb') as f:
                        import pickle
                        cached_data = pickle.load(f)
                    
                    # Check if cache is expired
                    cache_time = cached_data.get('timestamp')
                    if cache_time and (datetime.now() - cache_time) > self.ttl:
                        cache_file.unlink()
                        continue
                    
                    # We found a valid cached entry
                    # This helps provide faster response for prefix matches
                    results = cached_data.get('results', [])
                    if results:
                        self.stats['hits'] += 1
                        return results
                except:
                    pass
            
            self.stats['misses'] += 1
            return None
        except:
            return None
    
    def get(self, query_type, query):
        """Get cached results if available and not expired"""
        cache_key = self._get_cache_key(query_type, query)
        cache_path = self._get_cache_path(cache_key)
        
        if not os.path.exists(cache_path):
            self.stats['misses'] += 1
            return None
        
        try:
            # Read cache file
            with open(cache_path, 'rb') as f:
                import pickle
                cached_data = pickle.load(f)
                
            # Check if cache is expired
            cache_time = cached_data.get('timestamp')
            if cache_time and (datetime.now() - cache_time) > self.ttl:
                os.remove(cache_path)
                self.stats['misses'] += 1
                return None
            
            self.stats['hits'] += 1
            return cached_data.get('results')
        except:
            # If any error occurs, remove cache file and return None
            if os.path.exists(cache_path):
                os.remove(cache_path)
            self.stats['misses'] += 1
            return None
    
    def set(self, query_type, query, results):
        """Store results in cache"""
        cache_key = self._get_cache_key(query_type, query)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            cached_data = {
                'timestamp': datetime.now(),
                'results': results
            }
            
            with open(cache_path, 'wb') as f:
                import pickle
                pickle.dump(cached_data, f)
        except:
            # Silently fail if caching fails
            pass
    
    def clear(self):
        """Clear all cached results"""
        try:
            for cache_file in Path(self.cache_dir).glob("*.cache"):
                cache_file.unlink()
        except:
            pass
        
        # Reset statistics
        self.stats = {
            'hits': 0,
            'misses': 0
        }
    
    def get_stats(self):
        """Get cache statistics"""
        return self.stats
    
    def adjust_ttl(self):
        """Adjust TTL based on cache hit/miss ratio"""
        if not self.dynamic_ttl_enabled:
            return
        
        total = self.stats['hits'] + self.stats['misses']
        if total == 0:
            return
        
        hit_ratio = self.stats['hits'] / total
        
        # If hit ratio is high, increase TTL
        if hit_ratio > 0.8:
            self.ttl = min(self.ttl * 2, self.max_ttl)
        # If hit ratio is low, decrease TTL
        elif hit_ratio < 0.2:
            self.ttl = max(self.ttl / 2, self.min_ttl)


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
    """Get local path for favicon from cache, or return default icon (non-blocking)"""
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
    
    # If we have a domain, check cache (but don't download)
    if domain:
        # Create cache directory if it doesn't exist
        extension_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cache_path_dir = os.path.join(extension_base_dir, cache_dir)
        Path(cache_path_dir).mkdir(exist_ok=True)
        
        # Try multiple favicon services in order (check cache for each)
        favicon_services = [
            f"https://icons.duckduckgo.com/ip3/{domain}.ico",  # DuckDuckGo (usually fastest)
            f"https://www.google.com/s2/favicons?domain={domain}",  # Google
            f"https://www.google.com/s2/favicons?sz=32&domain={domain}",  # Google 32px
        ]
        
        for favicon_url in favicon_services:
            url_hash = hashlib.md5(favicon_url.encode()).hexdigest()
            cache_path = os.path.join(cache_path_dir, f"{url_hash}.png")
            
            # Return cached file if it exists
            if os.path.exists(cache_path):
                return cache_path
    
    # Use default icon if favicon not cached (non-blocking)
    return "images/icon.png"

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


class RaindropExtension(Extension):
    """ Main Extension Class  """

    def __init__(self):
        """ Initializes the extension """
        super(RaindropExtension, self).__init__()
        
        # Initialize rd_client with access token from preferences
        access_token = self.preferences.get('access_token')
        if access_token:
            from raindropio import API
            self.rd_client = API(access_token)
        else:
            self.rd_client = None
        
        # Initialize search cache
        self.search_cache = SearchCache()
        
        # Load version from manifest.json
        self._load_version()
        
        # Store the keyword prefix when showing tags (e.g., "rt " or "rd:tag ")
        # This is used to reconstruct the full query when a tag is selected
        self._tag_search_keyword_prefix = ''
        
        # Lock for favicon download threads to avoid race conditions
        self._favicon_download_lock = threading.Lock()
        # Track ongoing favicon downloads to avoid duplicate downloads
        self._favicon_downloads = {}  # {url_hash: thread}
        
        # Debounce optimization: track in-flight requests
        self._search_requests_lock = threading.Lock()
        self._in_flight_searches = {}  # {(trigger_id, query): timestamp}
        self._search_request_debounce_ms = 50  # Skip requests within 50ms
        
        # Favicon download thread pool (max concurrent downloads)
        self._favicon_executor = None  # Lazy init if needed
    
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

    def on_preferences_update(self, id, value, previous_value):
        """Handle preference updates"""
        if id == 'access_token':
            from raindropio import API
            self.rd_client = API(value) if value else None
        elif id == 'show_favicons':
            # Handle favicon setting change
            # Clear cache if favicons are disabled
            if not value:
                cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'favicon_cache')
                if os.path.exists(cache_dir):
                    try:
                        import shutil
                        shutil.rmtree(cache_dir)
                    except Exception as e:
                        logging.error(f"Error clearing favicon cache: {e}")
    
    def _download_favicon_async(self, drop, cache_dir="favicon_cache"):
        """Download favicon in background thread with multi-service fallback"""
        try:
            domain = None
            
            # Extract domain
            if hasattr(drop, 'domain') and drop.domain:
                domain = drop.domain
            elif hasattr(drop, 'link') and drop.link:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(drop.link)
                    domain = parsed.netloc
                except:
                    pass
            
            if not domain:
                return
            
            # Try multiple favicon services with fallback (fastest first)
            favicon_services = [
                f"https://icons.duckduckgo.com/ip3/{domain}.ico",  # DuckDuckGo (usually fastest)
                f"https://www.google.com/s2/favicons?domain={domain}",  # Google
                f"https://www.google.com/s2/favicons?sz=32&domain={domain}",  # Google 32px
            ]
            
            extension_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_path_dir = os.path.join(extension_base_dir, cache_dir)
            
            # Try to download from one of the services
            for favicon_url in favicon_services:
                url_hash = None
                try:
                    url_hash = hashlib.md5(favicon_url.encode()).hexdigest()
                    
                    # Check if already downloading or cached
                    with self._favicon_download_lock:
                        if url_hash in self._favicon_downloads:
                            return  # Already downloading
                        
                        cache_path = os.path.join(cache_path_dir, f"{url_hash}.png")
                        
                        if os.path.exists(cache_path):
                            return  # Already cached
                        
                        # Mark as downloading
                        self._favicon_downloads[url_hash] = threading.current_thread()
                    
                    # Create cache directory
                    Path(cache_path_dir).mkdir(exist_ok=True)
                    
                    # Download favicon (with short timeout)
                    try:
                        response = requests.get(favicon_url, timeout=1)
                        if response.status_code == 200 and len(response.content) > 0:
                            with open(cache_path, 'wb') as f:
                                f.write(response.content)
                            logging.debug(f"Downloaded favicon for {domain} from {favicon_url.split('/')[2]}: {cache_path}")
                            return  # Success!
                    except requests.exceptions.Timeout:
                        logging.debug(f"Timeout downloading favicon from {favicon_url}")
                        continue
                    except requests.exceptions.ConnectionError:
                        logging.debug(f"Connection error downloading favicon from {favicon_url}")
                        continue
                except Exception as e:
                    logging.debug(f"Failed to download favicon from {favicon_url}: {e}")
                    continue  # Try next service
                
                finally:
                    # Remove from download tracking for this service
                    if url_hash:
                        with self._favicon_download_lock:
                            self._favicon_downloads.pop(url_hash, None)
            
            logging.debug(f"Could not download favicon for {domain} from any service")
        
        except Exception as e:
            logging.error(f"Error in _download_favicon_async: {e}")
    
    def _queue_favicon_downloads(self, drops, cache_dir="favicon_cache"):
        """Queue favicon downloads for multiple bookmarks in background threads"""
        if not drops or len(drops) == 0:
            return
        
        # Start download threads for all bookmarks (max 20 concurrent)
        max_threads = min(20, len(drops))
        for i, drop in enumerate(drops):
            # Stagger thread creation slightly to avoid thundering herd
            if i < max_threads:
                thread = threading.Thread(
                    target=self._download_favicon_async,
                    args=(drop, cache_dir),
                    daemon=True
                )
                thread.start()
            else:
                # For remaining items, start after a small delay
                thread = threading.Thread(
                    target=lambda d=drop, cd=cache_dir: (
                        time.sleep(0.01),
                        self._download_favicon_async(d, cd)
                    ),
                    daemon=True
                )
                thread.start()

    def get_keyword_id(self, keyword):
        # In API v3, keywords are stored in triggers
        if hasattr(self, 'triggers') and 'keywords' in self.triggers:
            for kw in self.triggers['keywords']:
                if kw.get('default_keyword') == keyword:
                    return kw.get('id')
        return ""

    def _get_trigger_keyword(self, trigger_id):
        """Get the actual keyword for a trigger ID, accounting for user customization"""
        # Try to get from extension's triggers (these reflect user customization)
        if hasattr(self, 'triggers'):
            logging.debug(f"self.triggers available: {self.triggers}")
            if trigger_id in self.triggers:
                trigger_data = self.triggers[trigger_id]
                logging.debug(f"Trigger data for {trigger_id}: {trigger_data}")
                keyword = trigger_data.get('keyword', trigger_data.get('default_keyword', ''))
                logging.debug(f"Keyword: {keyword}")
                return keyword
        
        # Fallback: try to get from manifest
        try:
            import json
            manifest_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'manifest.json')
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
                triggers = manifest.get('triggers', {})
                if trigger_id in triggers:
                    keyword = triggers[trigger_id].get('keyword', triggers[trigger_id].get('default_keyword', ''))
                    logging.debug(f"Fallback keyword from manifest: {keyword}")
                    return keyword
        except Exception as e:
            logging.error(f"Error reading manifest: {e}")
        
        logging.warning(f"Could not find keyword for trigger_id: {trigger_id}")
        return ''

    def on_input(self, input_text, trigger_id):
        """ Handle user input """
        query = input_text or ""
        
        # Skip kw_open and kw_unsorted as they don't need debouncing
        if trigger_id == 'kw_open':
            return self.show_open_app_menu()

        if trigger_id == 'kw_unsorted':
            return self.unsorted(query)

        # Debounce optimization: check for duplicate requests
        search_key = (trigger_id, query)
        current_time = time.time()
        
        with self._search_requests_lock:
            if search_key in self._in_flight_searches:
                last_request_time = self._in_flight_searches[search_key]
                # If a request for this query was made within debounce window, return cached result
                if (current_time - last_request_time) * 1000 < self._search_request_debounce_ms:
                    logging.debug(f"Debounced duplicate request for {search_key}")
                    # Check cache instead of making new request
                    if trigger_id == 'kw_tag':
                        cached = self.search_cache.get("tag", query)
                        if cached:
                            return cached
                    else:  # kw
                        cached = self.search_cache.get("search", query)
                        if cached:
                            return cached
            
            # Mark this request as in-flight
            self._in_flight_searches[search_key] = current_time
        
        try:
            if trigger_id == 'kw_tag':
                return self.search_by_tag(query, trigger_id)
            else:  # kw
                return self.search(query)
        finally:
            # Clean up old in-flight entries (keep last 100)
            with self._search_requests_lock:
                if len(self._in_flight_searches) > 100:
                    # Remove oldest entries
                    sorted_keys = sorted(self._in_flight_searches.items(), 
                                       key=lambda x: x[1])
                    for key, _ in sorted_keys[:50]:
                        self._in_flight_searches.pop(key, None)

    def show_open_app_menu(self):
        """ Shows the menu to Open Raindrop website """
        cache_stats = self.search_cache.get_stats()
        cache_info = f"Cache: {cache_stats['hits']} hits, {cache_stats['misses']} misses"
        ttl_info = f"Cache TTL: {int(self.search_cache.ttl.total_seconds / 60)} minutes"
        
        return [
            Result(
                icon='images/icon.png',
                name='Open Raindrop Website',
                on_enter=OpenUrlAction('https://app.raindrop.io')),
            Result(
                icon='images/icon.png',
                name=f'Raindrop Extension v{self.version}',
                description='Current extension version',
                highlightable=False),
            Result(
                icon='images/icon.png',
                name='Cache Statistics',
                description=cache_info,
                highlightable=False),
            Result(
                icon='images/icon.png',
                name='Cache TTL',
                description=ttl_info,
                highlightable=False)
        ]

    def search(self, query):
        # Try to get cached results first
        cache_key = f"search:{query}"
        cached_results = self.search_cache.get("search", query)
        
        if cached_results is not None:
            return cached_results
        
        # Fallback: try to find results for similar prefix queries (helps during typing)
        if query and len(query) > 2:
            prefix_results = self.search_cache.get_by_prefix("search", query[:len(query)-1])
            if prefix_results and len(prefix_results) > 0:
                logging.debug(f"Using prefix cache for query: {query}")
                return prefix_results
        
        # Check if rd_client is initialized
        if not self.rd_client:
            return [
                Result(
                    icon='images/icon.png',
                    name='Raindrop access token not set. Please configure the extension.',
                    highlightable=False)
            ]
        
        try:
            drops = Raindrop.search(
                self.rd_client,
                word=query,
                perpage=100,
                collection=CollectionRef({"$id": 0}),
            )

            if len(drops) == 0:
                return [
                    Result(
                        icon='images/icon.png',
                        name='No results found matching your criteria',
                        highlightable=False)
                ]

            items = []
            # Get favicon setting from preferences
            show_favicons = self.preferences.get('show_favicons', True)
            
            for drop in drops:
                # Use favicon if enabled, otherwise use default icon
                # This now returns cached favicon or default icon (non-blocking)
                icon_path = get_favicon_path(drop) if show_favicons else "images/icon.png"
                items.append(
                    Result(icon=icon_path,
                           name=drop.title,
                           description=drop.excerpt,
                           on_enter=OpenUrlAction(drop.link)))
            
            # Queue favicon downloads in background threads (non-blocking)
            if show_favicons:
                self._queue_favicon_downloads(drops)
            
            # Cache the results
            self.search_cache.set("search", query, items)
            
            # Adjust TTL based on cache hit/miss ratio
            self.search_cache.adjust_ttl()
            
            return items
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection error during search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Connection error: Unable to reach Raindrop',
                    description='Please check your internet connection and try again',
                    highlightable=False)
            ]
        except requests.exceptions.Timeout as e:
            logging.error(f"Timeout error during search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Request timeout',
                    description='Raindrop API is taking too long to respond',
                    highlightable=False)
            ]
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error during search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Network error: {type(e).__name__}',
                    description='Please check your internet connection',
                    highlightable=False)
            ]
        except Exception as e:
            logging.error(f"Error searching: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Error searching: {type(e).__name__}',
                    description='An unexpected error occurred',
                    highlightable=False)
            ]


    def on_item_enter(self, data):
        """Handle tag selection from tag list"""
        try:
            # data is a dict with tag_name and trigger_id
            if isinstance(data, dict):
                tag_name = data.get('tag_name', '')
                logging.debug(f"on_item_enter: tag_name={tag_name}")
                
                # Directly return search results for the selected tag
                return self.search_by_tag(tag_name, 'kw_tag')
                
            else:
                # Fallback for string data
                logging.warning(f"on_item_enter received non-dict data: {data}")
                return []
        except Exception as e:
            logging.error(f"Error in on_item_enter: {e}", exc_info=True)
            return []

    def show_available_tags(self, trigger_id='kw_tag'):
        """ Show list of available tags from Raindrop """
        try:
            # Get all tags with documents count
            # Using 0 for collectionId to get tags from all collections
            response = self.rd_client.get(
                'https://api.raindrop.io/rest/v1/tags/0'
            )
            response.raise_for_status()
            tags_response = response.json()
            
            logging.debug(f"Tags API response: {tags_response}")
            
            if not tags_response or not tags_response.get('result'):
                logging.debug(f"API error: result=false or missing")
                return [
                    Result(
                        icon='images/icon.png',
                        name='No tags found',
                        highlightable=False)
                ]
            
            tags = tags_response.get('items', [])
            if not tags:
                return [
                    Result(
                        icon='images/icon.png',
                        name='No tags found',
                        highlightable=False)
                ]
            
            # Create results for each tag
            results = []
            for tag_item in tags:
                tag_name = tag_item.get('_id', '')
                tag_count = tag_item.get('count', 0)
                if tag_name:
                    # Pass both tag_name and trigger_id to on_item_enter
                    custom_data = {'tag_name': tag_name, 'trigger_id': trigger_id}
                    results.append(
                        Result(
                            icon='images/icon.png',
                            name=tag_name,
                            description=f'{tag_count} bookmark{"s" if tag_count != 1 else ""}',
                            on_enter=ExtensionCustomAction(custom_data)
                        )
                    )
            
            if not results:
                return [
                    Result(
                        icon='images/icon.png',
                        name='No tags found',
                        highlightable=False)
                ]
            
            return results[:50]  # Limit to 50 tags to avoid UI slowdown
        
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection error fetching tags: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Connection error: Unable to reach Raindrop',
                    description='Please check your internet connection',
                    highlightable=False)
            ]
        except requests.exceptions.Timeout as e:
            logging.error(f"Timeout error fetching tags: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Request timeout',
                    description='Raindrop API is taking too long to respond',
                    highlightable=False)
            ]
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error fetching tags: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Network error: {type(e).__name__}',
                    description='Please check your internet connection',
                    highlightable=False)
            ]
        except Exception as e:
            logging.error(f"Error fetching tags: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Error fetching tags: {type(e).__name__}',
                    description='Failed to retrieve tags from Raindrop',
                    highlightable=False)
            ]


    def search_by_tag(self, tag, trigger_id='kw_tag'):
        """ Search bookmarks by tag or show available tags if no tag specified """
        # Check if rd_client is initialized
        if not self.rd_client:
            return [
                Result(
                    icon='images/icon.png',
                    name='Raindrop access token not set. Please configure the extension.',
                    highlightable=False)
            ]
        
        # If tag is not provided, show available tags
        if not tag or tag.strip() == "":
            return self.show_available_tags(trigger_id)
        
        # Try to get cached results first
        cached_results = self.search_cache.get("tag", tag)
        if cached_results is not None:
            return cached_results
        
        # Search by tag using Raindrop API
        try:
            from raindropio import Raindrop, CollectionRef
            # Apply timeout to API call for better responsiveness
            def _search_with_timeout():
                return Raindrop.search(
                    self.rd_client,
                    tag=tag,
                    perpage=100,
                    collection=CollectionRef({"$id": 0}),
                )
            
            search_func = with_timeout(3)(_search_with_timeout)  # Optimized timeout: 3 seconds
            drops = search_func()

            if len(drops) == 0:
                return [
                    Result(
                        icon='images/icon.png',
                        name=f'No bookmarks found with tag: {tag}',
                        highlightable=False)
                ]

            items = []
            # Get favicon setting from preferences
            show_favicons = self.preferences.get('show_favicons', True)
            
            for drop in drops:
                # Use favicon if enabled, otherwise use default icon
                # This now returns cached favicon or default icon (non-blocking)
                icon_path = get_favicon_path(drop) if show_favicons else "images/icon.png"
                items.append(
                    Result(icon=icon_path,
                           name=drop.title,
                           description=drop.excerpt,
                           on_enter=OpenUrlAction(drop.link)))
            
            # Queue favicon downloads in background threads (non-blocking)
            if show_favicons:
                self._queue_favicon_downloads(drops)
            
            # Cache the results
            self.search_cache.set("tag", tag, items)
            
            # Adjust TTL based on cache hit/miss ratio
            self.search_cache.adjust_ttl()
            
            return items
            
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection error during tag search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Connection error: Unable to reach Raindrop',
                    description='Please check your internet connection and try again',
                    highlightable=False)
            ]
        except requests.exceptions.Timeout as e:
            logging.error(f"Timeout error during tag search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Request timeout',
                    description='Raindrop API is taking too long to respond',
                    highlightable=False)
            ]
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error during tag search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Network error: {type(e).__name__}',
                    description='Please check your internet connection',
                    highlightable=False)
            ]
        except Exception as e:
            logging.error(f"Error searching by tag: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Error searching by tag: {type(e).__name__}',
                    description='An unexpected error occurred',
                    highlightable=False)
            ]


    def unsorted(self, query):
        # Check if rd_client is initialized
        if not self.rd_client:
            return [
                Result(
                    icon='images/icon.png',
                    name='Raindrop access token not set. Please configure the extension.',
                    highlightable=False)
            ]
        
        # Apply timeout to API call for better responsiveness
        def _search_with_timeout():
            return Raindrop.search(
                self.rd_client,
                word=query,
                perpage=100,
                collection=CollectionRef({"$id": -1}),
            )
        
        try:
            search_func = with_timeout(3)(_search_with_timeout)  # Optimized timeout: 3 seconds
            drops = search_func()

            if len(drops) == 0:
                return [
                    Result(
                        icon='images/icon.png',
                        name='No results found matching your criteria',
                        highlightable=False)
                ]

            items = []
            # Get favicon setting from preferences
            show_favicons = self.preferences.get('show_favicons', True)
            
            for drop in drops:
                # Use favicon if enabled, otherwise use default icon
                # This now returns cached favicon or default icon (non-blocking)
                icon_path = get_favicon_path(drop) if show_favicons else "images/icon.png"
                items.append(
                    Result(icon=icon_path,
                           name=drop.title,
                           description=drop.excerpt,
                           on_enter=OpenUrlAction(drop.link)))
            
            # Queue favicon downloads in background threads (non-blocking)
            if show_favicons:
                self._queue_favicon_downloads(drops)
            
            return items
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection error during unsorted search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Connection error: Unable to reach Raindrop',
                    description='Please check your internet connection and try again',
                    highlightable=False)
            ]
        except requests.exceptions.Timeout as e:
            logging.error(f"Timeout error during unsorted search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name='Request timeout',
                    description='Raindrop API is taking too long to respond',
                    highlightable=False)
            ]
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error during unsorted search: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Network error: {type(e).__name__}',
                    description='Please check your internet connection',
                    highlightable=False)
            ]
        except Exception as e:
            logging.error(f"Error searching unsorted: {e}", exc_info=True)
            return [
                Result(
                    icon='images/icon.png',
                    name=f'Error searching: {type(e).__name__}',
                    description='An unexpected error occurred',
                    highlightable=False)
            ]
