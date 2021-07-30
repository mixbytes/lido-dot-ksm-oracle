from substrateinterface import SubstrateInterface
from substrateinterface.utils.ss58 import ss58_decode, ss58_encode
from websocket._exceptions import WebSocketAddressException
from web3 import Web3

import json
import logging


SS58_FORMATS = (0, 2, 42)


def create_interface(url, ss58_format, type_registry_preset):
    substrate = None

    if ss58_format not in SS58_FORMATS:
        logging.error("Invalid SS58 format")

        return substrate

    for u in url:
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

        except ValueError:
            logging.warning(f"Failed to connect to {u} with type registry preset '{type_registry_preset}'")
        else:
            break

    return substrate


def create_provider(url):
    provider = None

    for u in url:
        if not u.startswith('ws'):
            logging.warning(f"Unsupported ws provider: {u}")
            continue

        try:
            provider = Web3.WebsocketProvider(u)
        except WebSocketAddressException:
            logging.warning(f"Failed to connect to {u}")
        else:
            break

    return provider


def decode_stash_addresses(accounts):
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
    prefix = b'para'
    para_addr = bytearray(prefix)
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)
    _para_id = _para_id >> 8
    para_addr.append(_para_id & 0xFF)

    return ss58_encode(para_addr.ljust(32, b'\0'), ss58_format=ss58_format)


def get_parachain_balance(substrate, para_id=1000, block_hash=None):
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
    with open(abi_path, 'r') as f:
        return json.load(f)


def get_active_era(substrate, block_hash=None):
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
