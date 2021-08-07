from substrateinterface import SubstrateInterface
from substrateinterface.utils.ss58 import ss58_decode, ss58_encode
from websocket._exceptions import WebSocketAddressException
from web3 import Web3

import json
import logging
import time


SS58_FORMATS = (0, 2, 42)

logger = logging.getLogger(__name__)


def create_interface(
        urls: list, ss58_format: int = 2,
        type_registry_preset: str = 'kusama',
        timeout: int = 60, undesirable_url: str = None) -> SubstrateInterface:
    """Create Substrate interface with the first node that comes along, if there is no undesirable one"""
    substrate = None
    tried_all = False

    if ss58_format not in SS58_FORMATS:
        logging.error("Invalid SS58 format")

        return substrate

    while True:
        for u in urls:
            if u == undesirable_url and not tried_all:
                logging.info(f"Skipping undesirable url: {u}")
                continue

            if not u.startswith('ws'):
                logging.warning(f"Unsupported ws provider: {u}")
                continue

            try:
                substrate = SubstrateInterface(
                    url=u,
                    ss58_format=ss58_format,
                    type_registry_preset=type_registry_preset,
                )

                substrate.update_type_registry_presets()

            except (
                ValueError,
                ConnectionRefusedError,
            ) as exc:
                logging.warning(f"Failed to connect to {u}: {exc}")

            else:
                logging.info(f"The connection was made at the address: {u}")
                return substrate

        tried_all = True
        logging.error('Failed to connect to any node')
        logger.info(f"Timeout: {timeout} seconds")
        time.sleep(timeout)


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


def decode_stash_addresses(accounts):
    """Decode stash addresses from ss58, if required"""
    if not accounts:
        return None

    decoded_accounts = []

    for acc in accounts:
        if not acc.startswith('0x'):
            decoded_accounts.append('0x' + ss58_decode(acc))
        else:
            decoded_accounts.append(ss58_decode(acc))

    return decoded_accounts


def get_parachain_address(_para_id: int, ss58_format: int):
    """Get parachain address using parachain id with ss58 format provided"""
    prefix = b'para'
    para_addr = bytearray(prefix)
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)

    return ss58_encode(para_addr.ljust(32, b'\0'), ss58_format=ss58_format)


def get_parachain_balance(substrate, para_id=1000, block_hash=None):
    """Get parachain balance using parachain id"""
    if not block_hash:
        block_hash = substrate.get_chain_head()

    para_addr = get_parachain_address(para_id, substrate.ss58_format)

    result = substrate.query(
        module='System',
        storage_function='Account',
        params=[para_addr],
    )

    if result is None:
        logging.warning(f"{para_id} is gone")
        return 0

    return result.value['data']['free']


def get_abi(abi_path):
    """Get ABI from file"""
    with open(abi_path, 'r') as f:
        return json.load(f)


def get_active_era(substrate, block_hash=None):
    """Get active era from specific block or head"""
    if block_hash:
        return substrate.query(
            module='Staking',
            storage_function='ActiveEra',
            block_hash=block_hash,
        )

    return substrate.query(
        module='Staking',
        storage_function='ActiveEra',
    )
