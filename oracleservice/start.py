#!/usr/bin/env python3
from default_mode import default_mode
from log import init_log
from recovery_mode import recovery_mode
from substrateinterface.exceptions import BlockNotFound
from utils import create_interface, create_provider, decode_stash_addresses, get_abi
from web3 import Web3
from web3.exceptions import TimeExhausted
from walmanager import WALManager
from websockets.exceptions import ConnectionClosedError

import logging
import os
import sys

logger = logging.getLogger(__name__)

DEFAULT_GAS_LIMIT = 10000000
DEFAULT_MAX_NUMBER_OF_REQUESTS = 10
DEFAULT_TIMEOUT = 60
DEFAULT_ERA_DURATION = 30
DEFAULT_INITIAL_BLOCK_NUMBER = 1


def main():
    init_log(stdout_level=os.getenv('LOG_LEVEL_STDOUT', 'INFO'))

    ws_url_relay = os.getenv('WS_URL_RELAY', 'ws://localhost:9951/').split(',')
    ws_url_para = os.getenv('WS_URL_PARA', 'ws://localhost:10055/').split(',')
    ss58_format = int(os.getenv('SS58_FORMAT', 2))
    type_registry_preset = os.getenv('TYPE_REGISTRY_PRESET', 'kusama')
    para_id = int(os.getenv('PARA_ID'))

    contract_address = os.getenv('CONTRACT_ADDRESS', None)
    if contract_address is None:
        sys.exit('No contract address provided')

    abi_path = os.getenv('ABI_PATH', 'oracleservice/abi.json')
    abi = get_abi(abi_path)

    gas = int(os.getenv('GAS_LIMIT', DEFAULT_GAS_LIMIT))
    max_number_of_requests = int(os.getenv('MAX_NUMBER_OF_REQUESTS', DEFAULT_MAX_NUMBER_OF_REQUESTS))
    timeout = int(os.getenv('TIMEOUT', DEFAULT_TIMEOUT))

    era_duration = int(os.getenv('ERA_DURATION', DEFAULT_ERA_DURATION))
    initial_block_number = int(os.getenv('INITIAL_BLOCK_NUMBER', DEFAULT_INITIAL_BLOCK_NUMBER))

    w3 = Web3(create_provider(ws_url_para))
    if not w3.isConnected():
        sys.exit('Failed to create web3 provider')

    substrate = create_interface(ws_url_relay, ss58_format, type_registry_preset)
    if substrate is None:
        sys.exit('Failed to create substrate-interface')

    stash = os.getenv('STASH_ACCOUNTS').split(',')
    stash_accounts = decode_stash_addresses(stash)
    if stash_accounts is None:
        sys.exit('Failed to parse stash accounts list')

    oracle_private_key = os.getenv('ORACLE_PRIVATE_KEY')
    if oracle_private_key is None:
        sys.exit('Failed to parse oracle private key')

    wal_manager = WALManager()
    if wal_manager.log_exists():
        logger.info('Found WAL file')
        substrate = recovery_mode(w3, substrate, wal_manager, timeout, max_number_of_requests, ws_url_relay)

    while True:
        try:
            default_mode(
                oracle_private_key, w3, substrate,
                wal_manager, para_id, stash_accounts,
                abi, contract_address, gas,
                era_duration, initial_block_number,
            )

        except (
                BlockNotFound,
                ConnectionClosedError,
                ConnectionRefusedError,
                TimeExhausted,
                ValueError,
        ) as e:
            logging.warning(f"Error: {e}")
            substrate = recovery_mode(
                w3, substrate, wal_manager,
                timeout, max_number_of_requests, ws_url_relay,
                is_start=False,
            )


if __name__ == "__main__":
    main()
