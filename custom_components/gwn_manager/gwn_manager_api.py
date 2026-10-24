import httpx
import json
import time
import hashlib
from typing import Any, Dict, List

import logging
_LOGGER = logging.getLogger(__name__)

class GWNClient:
    def __init__(self, app_id, secret_key, base_url="https://eu.gwn.cloud"):
        self.app_id = app_id
        self.secret_key = secret_key
        self.base_url = base_url
        self.access_token = None
        self.token_expiry = 0

    def authenticate(self):
        """
        Authenticates with the GWN API to retrieve an access token.
        """
        auth_url = f"{self.base_url}/oauth/token"
        
        payload = {
            "client_id": self.app_id,
            "client_secret": self.secret_key,
            "grant_type": "client_credentials"
        }
        
        try:
            response = httpx.post(auth_url, data=payload, timeout=10.0, verify=False)
            response.raise_for_status()

            data = response.json()
            self.access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self.token_expiry = time.time() + expires_in
            
            print("Successfully authenticated.")
            return True
            
        except httpx.HTTPStatusError as e:
            print(f"Authentication failed: {e}")
            if getattr(e, 'response', None) is not None:
                print(f"Response content: {e.response.text}")
            return False
        except httpx.RequestError as e:
            print(f"Authentication failed: {e}")
            return False

    def get_headers(self):
        if not self.access_token or time.time() >= self.token_expiry:
            if not self.authenticate():
                raise Exception("Could not authenticate")
        
        return {
            # "Authorization": f"Bearer {self.access_token}", # Moving to URL param
            "Content-Type": "application/json"
        }

    def calculate_signature(self, timestamp, body=None):
        """
        Calculates the signature for the request.
        """
        # 1. Collect parameters for signature
        params = {
            "access_token": self.access_token, # Included now
            "appID": self.app_id,
            "secretKey": self.secret_key,
            "timestamp": str(timestamp)
        }
        
        # 2. Sort parameters alphabetically by key
        sorted_keys = sorted(params.keys())
        
        # 3. Construct the parameter string: &key=value&key=value...
        param_str = "&" + "&".join([f"{k}={params[k]}" for k in sorted_keys])
        
        # 4. If body exists, calculate sha256 of body and append
        if body:
            body_str = json.dumps(body, separators=(',', ':'))
            body_hash = hashlib.sha256(body_str.encode('utf-8')).hexdigest()
            param_str += f"&{body_hash}&"
        else:
            param_str += "&"
            
        # 5. Calculate final signature
        signature = hashlib.sha256(param_str.encode('utf-8')).hexdigest()
        return signature

    def make_request(self, method, endpoint, json_data=None, params=None):
        url = f"{self.base_url}{endpoint}"
        timestamp = int(time.time() * 1000)
        
        # Ensure token is available
        if not self.access_token or time.time() >= self.token_expiry:
            if not self.authenticate():
                raise Exception("Could not authenticate")

        # Prepare body string for signature if needed
        if json_data:
            body_str = json.dumps(json_data, separators=(',', ':'))
        else:
            body_str = None
            
        signature = self.calculate_signature(timestamp, json_data)
        
        # Add public parameters to URL query params
        query_params = {
            "access_token": self.access_token, # Added to URL
            "appID": self.app_id,
            "timestamp": timestamp,
            "signature": signature
        }
        
        if params:
            query_params.update(params)
            
        try:
            headers = self.get_headers()
            if method.upper() == "POST":
                response = httpx.post(url, headers=headers, params=query_params, json=json_data, timeout=15.0, verify=False)
            else:
                response = httpx.get(url, headers=headers, params=query_params, timeout=10.0, verify=False)

            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            print(f"Request failed: {e}")
            if getattr(e, 'response', None) is not None:
                print(f"Response content: {e.response.text}")
            return None
        except httpx.RequestError as e:
            print(f"Request failed: {e}")
            return None

    def get_networks(self):
        """
        Retrieves a list of Networks.
        """
        # Using POST as per documentation/testing
        params = {
            "type": "asc",
            "order": "id",
            "search": "",
            "pageNum": 1,
            "pageSize": 5
        }
        return self.make_request("POST", "/oapi/v1.0.0/network/list", json_data=params)

    def get_access_points(self, network_id=None):
        """
        Retrieves a list of Access Points.
        """
        params = {
            "pageNum": 1,
            "pageSize": 100
        }
        if network_id:
            params["networkId"] = network_id
        
        return self.make_request("POST", "/oapi/v1.0.0/ap/list", json_data=params)

    def get_client_List(self, network_id=None):
        """
        Retrieves a list of Access Points.
        """
        params = {
            "pageNum": 1,
            "pageSize": 100,
            "untilNow": 1
        }
        if network_id:
            params["networkId"] = network_id
            
        return self.make_request("POST", "/oapi/v1.0.0/client/list", json_data=params)

    async def get_data(self) -> Dict[str, Any]:
            
            self.authenticate()
            networks = self.get_networks()
            #_LOGGER.warning(networks)
            if networks:
                print(json.dumps(networks, indent=2))

                network_id = networks['data']['result'][0].get('id')
                aps = self.get_access_points(network_id)
                clients = self.get_client_List(network_id)

                """Get data from AP.json and CLIENT.json files."""
                #aps_data = self._load_json_file("AP.json")
                #clients_data = self._load_json_file("CLIENT.json")

                aps_data = aps
                clients_data = clients

                # Build a map of AP MAC -> AP info for easy lookup
                aps_map = {}
                aps_list = aps_data.get("data", {}).get("result", [])
                for ap in aps_list:
                    aps_map[ap.get("mac", "")] = ap

                # Normalize clients to expected format
                clients = []
                clients_list = clients_data.get("data", {}).get("result", [])
                for client in clients_list:
                    ap_id = client.get("apId", "")
                    ap_info = aps_map.get(ap_id, {})

                    normalized_client = {
                        "mac": client.get("clientId", ""),
                        "name": client.get("name", "Unknown"),
                        "ipv4": client.get("ipv4", ""),
                        "ipv6": client.get("ipv6", ""),
                        "ap_mac": ap_id,
                        "ap_name": ap_info.get("name", client.get("apName", "Unknown")),
                        "rssi": client.get("rssi"),
                        "ssid": client.get("ssid", ""),
                        "online": client.get("online", 0),
                        "tx_bytes": client.get("txBytes", 0),
                        "rx_bytes": client.get("rxBytes", 0),
                        "tx_rate": client.get("txRate", 0),
                        "rx_rate": client.get("rxRate", 0),
                    }
                    clients.append(normalized_client)

                #_LOGGER.warning(json.dumps(aps_list, indent=2))
                #_LOGGER.warning(json.dumps(clients, indent=2))
                return {
                    "aps": aps_list,
                    "clients": clients,
                }
