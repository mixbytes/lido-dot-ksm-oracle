from os.path import exists
from substrateinterface import SubstrateInterface
from web3 import Web3
from web3.exceptions import ABIFunctionNotFound
from websocket._exceptions import WebSocketAddressException
from websockets.exceptions import InvalidStatusCode

import json
import logging
import socket
import sys
import threading as th
import time
import urllib

logger = logging.getLogger(__name__)

LOG_LEVELS = (
    'DEBUG',
    'INFO',
    'WARNING',
    'ERROR',
    'CRITICAL',
)


def stop_signal_handler(sig: int, frame, substrate: SubstrateInterface = None, timer: th.Timer = None):
    """Handle signal, close substrate interface websocket connection, if it is open, stop timer thread and terminate the process"""
    logger.debug(f"Receiving signal: {sig}")
    if substrate is not None:
        logger.debug("Closing substrate interface websocket connection")
        try:
            substrate.websocket.sock.shutdown(socket.SHUT_RDWR)
        except (
            AttributeError,
            OSError,
        ) as exc:
            logger.warning(exc)
        else:
            logger.debug(f"Connection to relaychain node {substrate.url} is closed")

    if timer is not None:
        try:
            if timer.is_alive():
                timer.cancel()

        except Exception as exc:
            logger.warning(exc)

    sys.exit()


def create_provider(urls: list, timeout: int = 60, undesirable_urls: set = set()) -> Web3:
    """Create web3 websocket provider with one of the nodes given in the list"""
    provider = None
    tried_all = False

    while True:
        for url in urls:
            if url in undesirable_urls and not tried_all:
                logger.info(f"Skipping undesirable url: {url}")
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
                logger.warning(f"Failed to connect to {url}: {exc}")

            except ConnectionRefusedError:
                logger.warning(f"Failed to connect to {url}: provider is not connected")

            else:
                logger.info(f"Successfully connected to {url}")
                return w3

        tried_all = True

        logger.error("Failed to connect to any node")
        logger.info(f"Timeout: {timeout} seconds")
        time.sleep(timeout)


def create_interface(
        urls: list, ss58_format: int = 2,
        type_registry_preset: str = 'kusama',
        timeout: int = 60, undesirable_urls: set = set(),
        recovering: bool = False, substrate: SubstrateInterface = None,
) -> SubstrateInterface:
    """Create Substrate interface with the first node that comes along, if there is no undesirable one"""
    tried_all = False

    while True:
        for url in urls:
            if url in undesirable_urls and not tried_all:
                logger.info(f"Skipping undesirable url: {url}")
                continue

            try:
                if recovering:
                    substrate.websocket.close()
                    substrate.websocket.connect(url)
                else:
                    substrate = SubstrateInterface(
                            url=url,
                            ss58_format=ss58_format,
                            type_registry_preset=type_registry_preset,
                        )
                    substrate.update_type_registry_presets()

            except (
                ConnectionRefusedError,
                InvalidStatusCode,
                ValueError,
                WebSocketAddressException,
            ) as exc:
                logger.warning(f"Failed to connect to {url}: {exc}")
                if isinstance(exc.args[0], str) and exc.args[0].find("Unsupported type registry preset") != -1:
                    raise ValueError(exc.args[0])

            else:
                logger.info(f"The connection was made at the address: {url}")

                return substrate

        tried_all = True

        logger.error("Failed to connect to any node")
        logger.info(f"Timeout: {timeout} seconds")
        time.sleep(timeout)


def is_invalid_urls(urls: [str]) -> bool:
    """Check if invalid urls are in the list"""
    for url in urls:
        parsed_url = urllib.parse.urlparse(url)
        try:
            assert parsed_url.scheme in ("ws", "wss")
            assert parsed_url.params == ""
            assert parsed_url.fragment == ""
            assert parsed_url.hostname is not None
        except AssertionError:
            return True

    return False


def get_abi(abi_path: str) -> list:
    """Get ABI from file"""
    with open(abi_path, 'r') as f:
        return json.load(f)


def check_contract_address(w3: Web3, contract_addr: str):
    """Check whether the correct contract address is provided"""
    contract_code = w3.eth.get_code(contract_addr)
    if len(contract_code) < 3:
        raise ValueError("Incorrect contract address or the contract is not deployed")


def check_abi(w3: Web3, contract_addr: str, abi: list, oracle_addr: str):
    """Check the provided ABI by checking JSON file and calling the contract methods"""
    contract = w3.eth.contract(address=contract_addr, abi=abi)
    try:
        if not hasattr(contract.functions, 'reportRelay'):
            raise ABIFunctionNotFound("The contract does not contain the 'reportRelay' function")

        contract.functions.reportRelay(0, {
            'stashAccount': '',
            'controllerAccount': '',
            'stakeStatus': 0,
            'activeBalance': 0,
            'totalBalance': 0,
            'unlocking': [],
            'claimedRewards': [],
            'stashBalance': 0,
            'slashingSpans': 0,
        }).call()

        if not hasattr(contract.functions, 'getStashAccounts'):
            raise ABIFunctionNotFound("The contract does not contain the 'getStashAccounts' function")

        contract.functions.getStashAccounts().call()

    except ValueError:
        pass


def check_log_level(log_level: str):
    """Check the logger level based on the default list"""
    if log_level not in LOG_LEVELS:
        raise ValueError(f"Valid 'LOG_LEVEL_STDOUT' values: {LOG_LEVELS}")


def check_abi_path(abi_path: str):
    """Check the path to the ABI"""
    if not exists(abi_path):
        raise FileNotFoundError(f"The file with the ABI was not found: {abi_path}")


def get_parachain_address(_para_id: int) -> str:
    """Get parachain address using parachain id with ss58 format provided"""
    prefix = b'para'
    para_addr = bytearray(prefix)
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)

    return para_addr.ljust(32, b'\0').hex()
