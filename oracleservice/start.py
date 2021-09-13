#!/usr/bin/env python3
from functools import partial
from log import init_log
from oracle import Oracle
from prometheus_client import start_http_server
from service_parameters import ServiceParameters
from substrateinterface.exceptions import BlockNotFound
from substrate_interface_utils import SubstrateInterfaceUtils
from utils import create_provider, get_abi, remove_invalid_urls, stop_signal_handler
from utils import check_abi, check_contract_address, check_log_level, perform_sanity_checks
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, TimeExhausted, ValidationError
from websocket._exceptions import WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage

import logging
import os
import signal
import sys


logger = logging.getLogger(__name__)

DEFAULT_ERA_DURATION_IN_BLOCKS = 30
DEFAULT_ERA_DURATION_IN_SECONDS = 180
DEFAULT_GAS_LIMIT = 10000000
DEFAULT_INITIAL_BLOCK_NUMBER = 1
DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS = 10
DEFAULT_PROMETHEUS_METRICS_PORT = 8000
DEFAULT_TIMEOUT = 60
DEFAULT_WATCHDOG_DELAY = 5


def main():
    try:
        log_level = os.getenv('LOG_LEVEL_STDOUT', 'INFO')
        check_log_level(log_level)
        init_log(stdout_level=log_level)

        prometheus_metrics_port = int(os.getenv('PROMETHEUS_METRICS_PORT', DEFAULT_PROMETHEUS_METRICS_PORT))
        logger.info(f"Starting the prometheus server on port {prometheus_metrics_port}")
        start_http_server(prometheus_metrics_port)

        ws_url_relay = os.getenv('WS_URL_RELAY', 'ws://localhost:9951/').split(',')
        ws_url_para = os.getenv('WS_URL_PARA', 'ws://localhost:10055/').split(',')
        ss58_format = int(os.getenv('SS58_FORMAT', 2))
        type_registry_preset = os.getenv('TYPE_REGISTRY_PRESET', 'kusama')
        para_id = int(os.getenv('PARA_ID'))

        contract_address = os.getenv('CONTRACT_ADDRESS')
        if contract_address is None:
            sys.exit("No contract address provided")

        abi_path = os.getenv('ABI_PATH', 'oracleservice/abi.json')

        gas_limit = int(os.getenv('GAS_LIMIT', DEFAULT_GAS_LIMIT))
        max_number_of_failure_requests = int(os.getenv(
            'MAX_NUMBER_OF_FAILURE_REQUESTS',
            DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS,
        ))
        timeout = int(os.getenv('TIMEOUT', DEFAULT_TIMEOUT))
        watchdog_delay = int(os.getenv('WATCHDOG_DELAY', DEFAULT_WATCHDOG_DELAY))

        era_duration_in_blocks = int(os.getenv('ERA_DURATION_IN_BLOCKS', DEFAULT_ERA_DURATION_IN_BLOCKS))
        era_duration_in_seconds = int(os.getenv('ERA_DURATION_IN_SECONDS', DEFAULT_ERA_DURATION_IN_SECONDS))
        initial_block_number = int(os.getenv('INITIAL_BLOCK_NUMBER', DEFAULT_INITIAL_BLOCK_NUMBER))

        oracle_private_key = os.getenv('ORACLE_PRIVATE_KEY')
        if oracle_private_key is None:
            sys.exit("Failed to parse oracle private key")

        perform_sanity_checks(
            abi_path=abi_path,
            contract_address=contract_address,
            era_duration_in_blocks=era_duration_in_blocks,
            era_duration_in_seconds=era_duration_in_seconds,
            gas_limit=gas_limit,
            initial_block_number=initial_block_number,
            max_number_of_failure_requests=max_number_of_failure_requests,
            para_id=para_id,
            private_key=oracle_private_key,
            timeout=timeout,
            watchdog_delay=watchdog_delay,
            ws_url_para=ws_url_para,
            ws_url_relay=ws_url_relay,
        )

        abi = get_abi(abi_path)
        ws_url_para = remove_invalid_urls(ws_url_para)
        ws_url_relay = remove_invalid_urls(ws_url_relay)

        w3 = create_provider(ws_url_para, timeout)
        substrate = SubstrateInterfaceUtils.create_interface(ws_url_relay, ss58_format, type_registry_preset)

        check_contract_address(w3, contract_address)
        oracle = w3.eth.account.from_key(oracle_private_key)
        check_abi(w3, contract_address, abi, oracle.address)

        service_params = ServiceParameters(
            abi=abi,
            contract_address=contract_address,
            era_duration_in_blocks=era_duration_in_blocks,
            era_duration_in_seconds=era_duration_in_seconds,
            gas_limit=gas_limit,
            initial_block_number=initial_block_number,
            max_num_of_failure_reqs=max_number_of_failure_requests,
            para_id=para_id,
            ss58_format=ss58_format,
            substrate=substrate,
            timeout=timeout,
            type_registry_preset=type_registry_preset,
            watchdog_delay=watchdog_delay,
            ws_urls_relay=ws_url_relay,
            ws_urls_para=ws_url_para,
            w3=w3,
        )

        oracle = Oracle(account=oracle, service_params=service_params)

    except (
        ABIFunctionNotFound,
        FileNotFoundError,
        InvalidMessage,
        IsADirectoryError,
        OverflowError,
        ValueError,
    ) as exc:
        sys.exit(exc)

    except KeyboardInterrupt:
        sys.exit()

    signal.signal(signal.SIGTERM, partial(stop_signal_handler, substrate=substrate, timer=oracle.watchdog))
    signal.signal(signal.SIGINT, partial(stop_signal_handler, substrate=substrate, timer=oracle.watchdog))

    while True:
        try:
            oracle.start_default_mode()

        except (
            ABIFunctionNotFound,
            AssertionError,
        ) as exc:
            sys.exit(f"Error: {exc}")

        except (
            BadFunctionCallOutput,
            BlockNotFound,
            BrokenPipeError,
            ConnectionClosedError,
            ConnectionRefusedError,
            ConnectionResetError,
            InvalidMessage,
            KeyError,
            TimeExhausted,
            ValidationError,
            ValueError,
            WebSocketConnectionClosedException,
        ) as exc:
            logger.warning(f"Error: {exc}")
            oracle.start_recovery_mode()


if __name__ == '__main__':
    main()
