#!/usr/bin/env python3
from default_mode import default_mode
from log import init_log
from recovery_mode import recovery_mode
from utils import create_interface, create_provider, decode_stash_addresses, get_abi
from web3 import Web3
from walmanager import WALManager

import os
import sys


DEFAULT_GAS_LIMIT = 10000000
DEFAULT_MAX_NUMBER_OF_REQUESTS = 10


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
        substrate = recovery_mode(w3, substrate, wal_manager, max_number_of_requests, ws_url_relay)

    while True:
        try:
            default_mode(oracle_private_key, w3, substrate, wal_manager, para_id, stash_accounts, abi, contract_address, gas)

        except ConnectionRefusedError:
            substrate = recovery_mode(w3, substrate, wal_manager, max_number_of_requests, ws_url_relay, is_start=False)


if __name__ == "__main__":
    main()
