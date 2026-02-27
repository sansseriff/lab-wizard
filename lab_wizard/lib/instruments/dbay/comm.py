import requests
from typing import Any
from lab_wizard.lib.instruments.general.parent_child import Dependency


class Comm(Dependency):
    def __init__(self, ip_address: str, ip_port: int):
        self.ip_address = ip_address
        self.ip_port = ip_port

    def get(self, endpoint: str) -> dict[str, Any]:
        response = requests.get(f"http://{self.ip_address}:{self.ip_port}/{endpoint}")
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to get data from {endpoint}")

    def put(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        response = requests.put(
            f"http://{self.ip_address}:{self.ip_port}/{endpoint}", json=data
        )
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to put data to {endpoint}")
