from websocket._exceptions import WebSocketAddressException
from web3 import Web3

import json
import logging
import time


logger = logging.getLogger(__name__)


def create_provider(urls: list, timeout: int = 60) -> Web3:
    """Create web3 websocket provider with one of the nodes given in the list"""
    provider = None

    while True:
        for url in urls:
            if not url.startswith('ws'):
                logging.warning(f"Unsupported ws provider: {url}")
                continue

            try:
                provider = Web3.WebsocketProvider(url)
                w3 = Web3(provider)
                if not w3.isConnected():
                    raise ConnectionRefusedError

            except (
                ValueError,
                WebSocketAddressException,
            ) as exc:
                logging.warning(f"Failed to connect to {url}: {exc}")

            except ConnectionRefusedError:
                logging.warning(f"Failed to connect to {url}: provider is not connected")

            else:
                logger.info(f"Successfully connected to {url}")
                return w3

        logging.error('Failed to connect to any node')
        logger.info(f"Timeout: {timeout} seconds")
        time.sleep(timeout)


def get_abi(abi_path):
    """Get ABI from file"""
    with open(abi_path, 'r') as f:
        return json.load(f)
