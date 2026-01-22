import gzip
import base64
import json
from sqlalchemy import TypeDecorator, Text

class GzippedText(TypeDecorator):
    """
    TypeDecorator that compresses strings using GZIP + Base64 before saving to the database,
    and decompresses them when retrieving.
    Includes backward compatibility for uncompressed text.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        
        # Determine if value is object (dict/list) - serialize first
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
            
        if not isinstance(value, str):
            value = str(value)

        # Compress
        try:
            compressed = gzip.compress(value.encode('utf-8'))
            return base64.b64encode(compressed).decode('utf-8')
        except Exception:
            # Fallback to original if compression fails for some reason
            return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None

        # Try to decompress
        try:
            # Check if it *might* be base64 (simple heuristic or just try)
            decoded = base64.b64decode(value)
            decompressed = gzip.decompress(decoded)
            return decompressed.decode('utf-8')
        except (OSError, ValueError, base64.binascii.Error):
            # Not a valid gzip/base64 string -> assume it is legacy plain text
            return value
