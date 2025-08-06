import json
import sys
from urllib import request, parse

class LibreTranslateAPI:
    
    """Connect to the LibreTranslate API"""

    DEFAULT_URL = "https://libretranslate.com/"

    def __init__(self, url=None, api_key=None):
        """Create a LibreTranslate API connection.

        Args:
            url (str): The url of the LibreTranslate endpoint.
            api_key (str): The API key.
        """
        self.url = LibreTranslateAPI.DEFAULT_URL if url is None else url
        self.api_key = api_key

        # Add trailing slash
        assert len(self.url) > 0
        if self.url[-1] != "/":
            self.url += "/"

    def translate(self, q, source="en", target="es", format="html"):
        """Translate string

        Args:
            q (str): The text to translate
            source (str): The source language code (ISO 639)
            target (str): The target language code (ISO 639)

        Returns:
            str: The translated text
        """
        url = self.url + "translate"
        params = {
            "q": q,
            "source": source,
            "target": target,
            "format": format
        }
        
        if self.api_key is not None:
            params["api_key"] = self.api_key
            
        url_params = parse.urlencode(params)
        req = request.Request(url, data=url_params.encode())
        response = request.urlopen(req)
        response_str = response.read().decode()
        return json.loads(response_str)["translatedText"]

    def detect(self, q):
        """Detect the language of a single text.

        Args:
            q (str): Text to detect

        Returns:
            The detected languages ex: [{"confidence": 0.6, "language": "en"}]
        """
        url = self.url + "detect"
        params = {"q": q}
        if self.api_key is not None:
            params["api_key"] = self.api_key
        url_params = parse.urlencode(params)
        req = request.Request(url, data=url_params.encode())
        response = request.urlopen(req)
        response_str = response.read().decode()
        return json.loads(response_str)

    def languages(self):
        """Retrieve list of supported languages.

        Returns:
            A list of available languages ex: [{"code":"en", "name":"English"}]
        """
        url = self.url + "languages"
        params = dict()
        if self.api_key is not None:
            params["api_key"] = self.api_key
        url_params = parse.urlencode(params)
        req = request.Request(url, data=url_params.encode(), method="GET")
        response = request.urlopen(req)
        response_str = response.read().decode()
        return json.loads(response_str)