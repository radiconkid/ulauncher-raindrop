import logging
import os
import hashlib
import requests
from pathlib import Path

from ulauncher.api.client.Extension import Extension
from ulauncher.api.shared.event import KeywordQueryEvent, PreferencesEvent, PreferencesUpdateEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from raindrop.preferences import PreferencesEventListener, PreferencesUpdateEventListener
from raindropio import Raindrop, CollectionRef
from raindrop.query_listener import KeywordQueryEventListener


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

logger = logging.getLogger(__name__)


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

    def get_keyword_id(self, keyword):
        for key, value in self.preferences.items():
            if value == keyword:
                return key

        return ""

    def show_open_app_menu(self):
        """ Shows the menu to Open Raindrop website """
        return RenderResultListAction([
            ExtensionResultItem(
                icon='images/icon.png',
                name='Open Raindrop Website',
                on_enter=OpenUrlAction('https://app.raindrop.io'))
        ])

    def search(self, query):
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
            collection=CollectionRef({"$id": 0}),
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

    def search_by_tag(self, tag):
        """ Search bookmarks by tag """
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
            drops = Raindrop.search(
                self.rd_client,
                tags=[tag],
                perpage=10,
                collection=CollectionRef({"$id": 0}),
            )

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
            return RenderResultListAction(items)
            
        except Exception as e:
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
