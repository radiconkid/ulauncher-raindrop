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
    """Get local path for favicon, downloading if necessary"""
    favicon_url = get_favicon_url(drop)
    if not favicon_url:
        return "images/icon.png"
    
    # Create cache directory if it doesn't exist
    Path(cache_dir).mkdir(exist_ok=True)
    
    # Generate filename from URL hash
    url_hash = hashlib.md5(favicon_url.encode()).hexdigest()
    cache_path = os.path.join(cache_dir, f"{url_hash}.png")
    
    # Return cached file if it exists
    if os.path.exists(cache_path):
        return cache_path
    
    # Download favicon
    try:
        response = requests.get(favicon_url, timeout=5)
        if response.status_code == 200:
            with open(cache_path, 'wb') as f:
                f.write(response.content)
            return cache_path
    except Exception as e:
        logger.error(f"Failed to download favicon: {e}")
    
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
        for drop in drops:
            icon_path = get_favicon_path(drop)
            items.append(
                ExtensionResultItem(icon=icon_path,
                                    name=drop.title,
                                    description=drop.excerpt,
                                    on_enter=OpenUrlAction(drop.link)))
        return RenderResultListAction(items)

    def unsorted(self, query):
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
        for drop in drops:
            icon_path = get_favicon_path(drop)
            items.append(
                ExtensionResultItem(icon=icon_path,
                                    name=drop.title,
                                    description=drop.excerpt,
                                    on_enter=OpenUrlAction(drop.link)))
        return RenderResultListAction(items)
