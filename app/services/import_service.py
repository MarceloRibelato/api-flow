import json
import re
import shlex
from urllib.parse import urlparse

class ImportService:
    @staticmethod
    def parse_curl(curl_command: str) -> dict:
        """
        Parses a cURL command string into a dictionary representing the API request.
        """
        curl_command = curl_command.strip()
        if not curl_command.lower().startswith('curl'):
            raise ValueError("Invalid cURL command")

        # Basic parsing using shlex to handle quotes correctly
        try:
            tokens = shlex.split(curl_command)
        except Exception:
             # Fallback for simple split if shlex fails (e.g. unclosed quotes)
            tokens = curl_command.split()

        method = "GET"
        url = ""
        headers = {}
        body = None
        
        i = 1 # Skip 'curl'
        while i < len(tokens):
            token = tokens[i]
            
            if token in ["-X", "--request"] and i + 1 < len(tokens):
                method = tokens[i+1].upper()
                i += 2
            elif token in ["-H", "--header"] and i + 1 < len(tokens):
                header_parts = tokens[i+1].split(":", 1)
                if len(header_parts) == 2:
                    headers[header_parts[0].strip()] = header_parts[1].strip()
                i += 2
            elif token in ["-d", "--data", "--data-raw", "--data-binary"] and i + 1 < len(tokens):
                body = tokens[i+1]
                if method == "GET": method = "POST" # Default to POST if data is present
                i += 2
            elif token.startswith("-"):
                 # Skip other flags for now
                 i += 1
            else:
                # Assume it's the URL if it looks like one and we haven't found one yet
                if not url and "http" in token:
                    url = token
                i += 1

        # Cleanup URL if it has quotes (shlex should handle this, but just in case)
        url = url.strip("'\"")

        return {
            "method": method,
            "url": url,
            "headers": [{"key": k, "value": v} for k, v in headers.items()],
            "body": body,
            "params": [] # Parse query params from URL if needed later
        }

    @staticmethod
    def parse_postman(collection_data: dict) -> list:
        """
        Parses a Postman Collection JSON (v2.0 and v2.1) into a list of API request objects.
        Robustly handles errors and various body formats.
        """
        requests = []
        
        def extract_items(items):
            if not isinstance(items, list):
                return

            for item in items:
                try:
                    if "item" in item and isinstance(item["item"], list):
                        # Folder (recursive)
                        extract_items(item["item"])
                    elif "request" in item:
                        # Request
                        req = item["request"]
                        
                        method = req.get("method", "GET")
                        
                        # URL Parsing
                        url_obj = req.get("url", {})
                        url = ""
                        if isinstance(url_obj, str):
                            # v2.0 often uses string for URL
                            url = url_obj
                        elif isinstance(url_obj, dict):
                            url = url_obj.get("raw", "")
                            # Fallback: construct from protocol, host, path if raw is missing
                            if not url and "host" in url_obj:
                                protocol = url_obj.get("protocol", "http")
                                host = ".".join(url_obj["host"]) if isinstance(url_obj["host"], list) else url_obj["host"]
                                path = "/".join(url_obj["path"]) if isinstance(url_obj["path"], list) else url_obj.get("path", "")
                                url = f"{protocol}://{host}/{path}"

                        # Headers
                        headers = []
                        if isinstance(req.get("header"), list):
                            for h in req.get("header", []):
                                headers.append({"key": h.get("key", ""), "value": h.get("value", "")})
                            
                        # Body
                        body = None
                        body_data = req.get("body", {})
                        body_mode = body_data.get("mode")
                        
                        if body_mode == "raw":
                            body = body_data.get("raw", "")
                        elif body_mode == "urlencoded":
                            # Convert urlencoded list to JSON-like object or string?
                            # Flow usually expects JSON body for API inputs if it's structured.
                            # For compatibility, let's try to make it a JSON object of keys/values.
                            urlencoded_data = body_data.get("urlencoded", [])
                            if isinstance(urlencoded_data, list):
                                body_dict = {}
                                for param in urlencoded_data:
                                    if not param.get("disabled"):
                                        body_dict[param.get("key")] = param.get("value")
                                body = json.dumps(body_dict)
                        elif body_mode == "formdata":
                             # Similar approach for formdata if needed
                             form_data = body_data.get("formdata", [])
                             if isinstance(form_data, list):
                                body_dict = {}
                                for param in form_data:
                                    if not param.get("disabled"):
                                        body_dict[param.get("key")] = param.get("value")
                                body = json.dumps(body_dict)

                        requests.append({
                            "name": item.get("name", "New Request"),
                            "description": item.get("request", {}).get("description", ""),
                            "method": method,
                            "url": url,
                            "headers": headers,
                            "body": body,
                            "params": [] # Could extract query params from URL object too
                        })
                except Exception as e:
                    print(f"Error parsing Postman item '{item.get('name', 'unknown')}': {e}")
                    # Continue to next item

        if "item" in collection_data:
            extract_items(collection_data["item"])
        elif "requests" in collection_data:
            # Handle Legacy Postman (v1)
            for req in collection_data["requests"]:
                try:
                    url = req.get("url", "")
                    method = req.get("method", "GET")
                    name = req.get("name", "Imported Request")
                    description = req.get("description", "")
                    
                    # Headers are string "key: value\nkey2: value2"
                    headers = []
                    header_str = req.get("headers", "")
                    if header_str:
                        for line in header_str.split("\n"):
                            if ":" in line:
                                key, value = line.split(":", 1)
                                headers.append({"key": key.strip(), "value": value.strip()})

                    # Body
                    body = None
                    data_mode = req.get("dataMode")
                    if data_mode == "raw":
                        body = req.get("rawModeData", "")
                    elif data_mode == "params":
                        data = req.get("data", [])
                        body_dict = {}
                        for d in data:
                            if d.get("enabled", True): # v1 might use 'enabled' boolean? Default true
                                body_dict[d.get("key")] = d.get("value")
                        body = json.dumps(body_dict)

                    requests.append({
                        "name": name,
                        "description": description,
                        "method": method,
                        "url": url,
                        "headers": headers,
                        "body": body,
                        "params": [] 
                    })
                except Exception as e:
                    print(f"Error parsing Legacy Postman item '{req.get('name', 'unknown')}': {e}")
            
        return requests

import_service = ImportService()
