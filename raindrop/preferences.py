""" Extension preferences Listeners """

import os
import shutil
from pathlib import Path
from ulauncher.api.client.EventListener import EventListener
from raindropio import API


class PreferencesEventListener(EventListener):
    """ Handles preferences initialization event """
    def on_event(self, event, extension):
        """ Handle event """
        extension.rd_client = API(event.preferences["access_token"])


class PreferencesUpdateEventListener(EventListener):
    """ Handles Preferences Update event """
    def on_event(self, event, extension):
        if event.id == 'access_token':
            extension.rd_client = API(event.new_value)
        elif event.id == 'show_favicons':
            # Handle favicon setting change
            # Clear cache if favicons are disabled
            if not event.new_value:
                extension_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cache_dir = os.path.join(extension_base_dir, 'favicon_cache')
                
                # Delete cache directory if it exists
                if os.path.exists(cache_dir):
                    try:
                        shutil.rmtree(cache_dir)
                    except Exception as e:
                        # Log error but don't crash
                        print(f"Error clearing favicon cache: {e}")
