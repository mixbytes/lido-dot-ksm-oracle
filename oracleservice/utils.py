from os.path import exists
from web3 import Web3
from web3.auto import w3
from web3.exceptions import ABIFunctionNotFound
from websocket._exceptions import WebSocketAddressException

import json
import logging
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

NON_NEGATIVE_PARAMETERS = (
    'ERA_DURATION',
    'GAS_LIMIT',
    'INITIAL_BLOCK_NUMBER',
    'MAX_NUMBER_OF_FAILURE_REQUESTS',
    'PARA_ID',
    'TIMEOUT',
)


def create_provider(urls: list, timeout: int = 60, undesirable_urls: set = set()) -> Web3:
    """Create web3 websocket provider with one of the nodes given in the list"""
    provider = None
    tried_all = False

    while True:
        for url in urls:
            if url in undesirable_urls and not tried_all:
                logger.info(f"Skipping undesirable url: {url}")
                continue

            if not url.startswith('ws'):
                logger.warning(f"Unsupported ws provider: {url}")
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


def get_abi(abi_path: str) -> list:
    """Get ABI from file"""
    with open(abi_path, 'r') as f:
        return json.load(f)


def check_contract_address(w3: Web3, contract_addr: str):
    """Check whether the correct contract address is provided"""
    contract_code = w3.eth.get_code(contract_addr)
    if len(contract_code) < 3:
        raise ValueError("Incorrect contract address or the contract is not deployed")


def check_abi(w3: Web3, contract_addr: str, abi: list):
    contract = w3.eth.contract(address=contract_addr, abi=abi)
    try:
        if not hasattr(contract.functions, 'reportRelay'):
            raise ABIFunctionNotFound("The contract does not contain the 'reportRelay' function")

        contract.functions.reportRelay(0, {'parachainBalance': 0, 'stakeLedger': []}).call()

    except ValueError:
        pass


def check_log_level(log_level: str):
    """Check the logger level based on the default list"""
    if log_level not in LOG_LEVELS:
        raise ValueError(f"Valid 'LOG_LEVEL_STDOUT' values: {LOG_LEVELS}")


def remove_invalid_urls(urls: [str]) -> [str]:
    """Remove invalid urls from the list"""
    checked_urls = []

    for url in urls:
        parsed_url = urllib.parse.urlparse(url)
        try:
            assert parsed_url.scheme == "ws"
            assert parsed_url.params == ""
            assert parsed_url.fragment == ""
            assert parsed_url.hostname is not None

            checked_urls.append(url)
        except AssertionError:
            logger.warning(f"Invalid url '{url}' removed from list")

    return checked_urls


def perform_sanity_checks(
    abi_path: str,
    contract_address: str,
    era_duration: int,
    gas_limit: int,
    initial_block_number: int,
    max_number_of_failure_requests: int,
    para_id: int,
    private_key: str,
    timeout: int,
    ws_url_relay: [str],
    ws_url_para: [str],
):
    """Check the parameters passed to the Oracle"""
    try:
        assert era_duration > 0
        assert initial_block_number >= 0
        assert timeout >= 0
        assert gas_limit > 0
        assert max_number_of_failure_requests >= 0
        assert para_id >= 0

    except AssertionError:
        raise ValueError(f"The following parameters must be non-negative: {NON_NEGATIVE_PARAMETERS}")

    if not exists(abi_path):
        raise FileNotFoundError(f"The file with the ABI was not found: {abi_path}")

    # Check private key. Throws an exception if the length is not 32 bytes
    w3.eth.account.from_key(private_key)

    if not len(remove_invalid_urls(ws_url_relay)):
        raise ValueError("No valid 'WS_URL_RELAY' values found")

    if not len(remove_invalid_urls(ws_url_para)):
        raise ValueError("No valid 'WS_URL_PARA' values found")
