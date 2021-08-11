#!/usr/bin/env python3
from log import init_log
from oracle import Oracle
from service_parameters import ServiceParameters
from substrateinterface import Keypair
from substrateinterface.exceptions import BlockNotFound
from substrate_interface_utils import SubstrateInterfaceUtils
from utils import create_provider, get_abi
from web3.exceptions import ABIFunctionNotFound, TimeExhausted
from websockets.exceptions import ConnectionClosedError

import logging
import os
import sys

logger = logging.getLogger(__name__)

DEFAULT_GAS_LIMIT = 10000000
DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS = 10
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
    max_number_of_failure_requests = int(os.getenv(
        'MAX_NUMBER_OF_FAILURE_REQUESTS',
        DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS,
    ))
    timeout = int(os.getenv('TIMEOUT', DEFAULT_TIMEOUT))

    era_duration = int(os.getenv('ERA_DURATION', DEFAULT_ERA_DURATION))
    initial_block_number = int(os.getenv('INITIAL_BLOCK_NUMBER', DEFAULT_INITIAL_BLOCK_NUMBER))

    w3 = create_provider(ws_url_para, timeout)
    substrate = SubstrateInterfaceUtils().create_interface(ws_url_relay, ss58_format, type_registry_preset)
    if substrate is None:
        sys.exit('Failed to create substrate-interface')

    stash = os.getenv('STASH_ACCOUNTS').split(',')
    stash_accounts = [Keypair(ss58_address=acc, ss58_format=ss58_format).public_key for acc in stash]
    if stash_accounts is None:
        sys.exit('Failed to parse stash accounts list')

    oracle_private_key = os.getenv('ORACLE_PRIVATE_KEY')
    if oracle_private_key is None:
        sys.exit('Failed to parse oracle private key')

    service_params = ServiceParameters(
        abi=abi,
        contract_address=contract_address,
        era_duration=era_duration,
        gas=gas,
        initial_block_number=initial_block_number,
        max_number_of_failure_requests=max_number_of_failure_requests,
        para_id=para_id,
        stash_accounts=stash_accounts,
        ss58_format=ss58_format,
        substrate=substrate,
        timeout=timeout,
        type_registry_preset=type_registry_preset,
        ws_urls_relay=ws_url_relay,
        ws_urls_para=ws_url_para,
        w3=w3,
    )

    oracle = Oracle(priv_key=oracle_private_key, service_params=service_params)

    while True:
        try:
            oracle.start_default_mode()

        except ABIFunctionNotFound as exc:
            sys.exit(f"Error: {exc}")

        except (
            BlockNotFound,
            ConnectionClosedError,
            ConnectionRefusedError,
            TimeExhausted,
            ValueError,
        ) as exc:
            logging.warning(f"Error: {exc}")
            oracle.start_recovery_mode()


if __name__ == "__main__":
    main()
