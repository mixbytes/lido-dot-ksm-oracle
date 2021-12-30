#!/usr/bin/env python3
from functools import partial
from log import init_log
from oracle import Oracle
from pathlib import Path
from prometheus_client import start_http_server
from service_parameters import ServiceParameters
from socket import gaierror
from substrateinterface.exceptions import BlockNotFound, SubstrateRequestException
from utils import create_interface, create_provider, get_abi, is_invalid_urls, stop_signal_handler, check_abi, check_abi_path, check_contract_address, check_log_level  # noqa: E501
from web3 import Web3
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, TimeExhausted, ValidationError
from websocket._exceptions import WebSocketAddressException, WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage, InvalidStatusCode

import asyncio
import logging
import os
import signal
import sys


logger = logging.getLogger(__name__)

DEFAULT_ABI_PATH = Path(__file__).parent.parent.as_posix() + '/assets/oracle.json'
DEFAULT_ERA_DURATION_IN_BLOCKS = 30
DEFAULT_ERA_DURATION_IN_SECONDS = 180
DEFAULT_FREQUENCY_OF_REQUESTS = 180
DEFAULT_GAS_LIMIT = 10000000
DEFAULT_INITIAL_BLOCK_NUMBER = 1
DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS = 10
DEFAULT_PROMETHEUS_METRICS_PORT = 8000
DEFAULT_PARA_ID = 999
DEFAULT_TIMEOUT = 60


def main():
    try:
        log_level = os.getenv('LOG_LEVEL_STDOUT', 'INFO')
        check_log_level(log_level)
        init_log(stdout_level=log_level)

        logger.info("Checking the configuration parameters")

        prometheus_metrics_port = int(os.getenv('PROMETHEUS_METRICS_PORT', DEFAULT_PROMETHEUS_METRICS_PORT))
        logger.info(f"Starting the prometheus server on port {prometheus_metrics_port}")
        start_http_server(prometheus_metrics_port)

        ws_urls_relay = os.getenv('WS_URL_RELAY', 'ws://localhost:9951/').split(',')
        assert not is_invalid_urls(ws_urls_relay), "Invalid urls were found in 'WS_URL_RELAY' parameter"

        ws_urls_para = os.getenv('WS_URL_PARA', 'ws://localhost:10055/').split(',')
        assert not is_invalid_urls(ws_urls_para), "Invalid urls were found in 'WS_URL_PARA' parameter"

        ss58_format = int(os.getenv('SS58_FORMAT', 2))
        type_registry_preset = os.getenv('TYPE_REGISTRY_PRESET', 'kusama')

        para_id = int(os.getenv('PARA_ID', DEFAULT_PARA_ID))
        assert para_id >= 0, "'PARA_ID' parameter must be non-negative integer"

        contract_address = os.getenv('CONTRACT_ADDRESS')
        if contract_address is None:
            sys.exit("No contract address provided")

        abi_path = os.getenv('ABI_PATH', DEFAULT_ABI_PATH)
        check_abi_path(abi_path)

        gas_limit = int(os.getenv('GAS_LIMIT', DEFAULT_GAS_LIMIT))
        assert gas_limit > 0, "'GAS_LIMIT' parameter must be positive integer"

        max_number_of_failure_requests = int(os.getenv(
                'MAX_NUMBER_OF_FAILURE_REQUESTS',
                DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS,
            ))
        assert max_number_of_failure_requests > 0, "'MAX_NUMBER_OF_FAILURE_REQUESTS' parameter must be positive integer"

        timeout = int(os.getenv('TIMEOUT', DEFAULT_TIMEOUT))
        assert timeout > 0, "'TIMEOUT' parameter must be positive integer"

        era_duration_in_blocks = int(os.getenv('ERA_DURATION_IN_BLOCKS', DEFAULT_ERA_DURATION_IN_BLOCKS))
        assert era_duration_in_blocks > 0, "'ERA_DURATION_IN_BLOCKS' parameter must be positive integer"

        era_duration_in_seconds = int(os.getenv('ERA_DURATION_IN_SECONDS', DEFAULT_ERA_DURATION_IN_SECONDS))
        assert era_duration_in_seconds > 0, "'ERA_DURATION_IN_SECONDS' parameter must be positive integer"

        initial_block_number = int(os.getenv('INITIAL_BLOCK_NUMBER', DEFAULT_INITIAL_BLOCK_NUMBER))
        assert initial_block_number >= 0, "'INITIAL_BLOCK_NUMBER' parameter must be non-negative integer"

        frequency_of_requests = int(os.getenv('FREQUENCY_OF_REQUESTS', DEFAULT_FREQUENCY_OF_REQUESTS))
        assert frequency_of_requests > 0, "'FREQUENCY_OF_REQUESTS' parameter must be positive integer"

        debug_mode = True if os.getenv('ORACLE_MODE') == 'DEBUG' else False

        abi = get_abi(abi_path)

        w3 = create_w3_provider_forcibly(ws_urls_para, timeout)
        substrate = create_interface(ws_urls_relay, ss58_format, type_registry_preset)

        oracle_private_key = os.getenv('ORACLE_PRIVATE_KEY')
        if oracle_private_key is None:
            sys.exit("Failed to parse oracle private key")
        # Check private key. Throws an exception if the length is not 32 bytes
        w3.eth.account.from_key(oracle_private_key)

        check_contract_address(w3, contract_address)
        oracle = w3.eth.account.from_key(oracle_private_key)
        logger.info("Checking ABI")
        check_abi(w3, contract_address, abi, oracle.address)

        service_params = ServiceParameters(
                abi=abi,
                contract_address=contract_address,
                debug_mode=debug_mode,
                era_duration_in_blocks=era_duration_in_blocks,
                era_duration_in_seconds=era_duration_in_seconds,
                frequency_of_requests=frequency_of_requests,
                gas_limit=gas_limit,
                initial_block_number=initial_block_number,
                max_num_of_failure_reqs=max_number_of_failure_requests,
                para_id=para_id,
                ss58_format=ss58_format,
                substrate=substrate,
                timeout=timeout,
                type_registry_preset=type_registry_preset,
                ws_urls_relay=ws_urls_relay,
                ws_urls_para=ws_urls_para,
                w3=w3,
            )

        oracle = Oracle(account=oracle, service_params=service_params)
        logger.info("Finished checking the configuration parameters")

    except (
        ABIFunctionNotFound,
        AssertionError,
        FileNotFoundError,
        InvalidMessage,
        IsADirectoryError,
        KeyError,
        OSError,
        OverflowError,
        ValueError,
    ) as exc:
        sys.exit(f"{type(exc)}: {exc}")

    except KeyboardInterrupt:
        sys.exit()

    signal.signal(signal.SIGTERM, partial(stop_signal_handler, substrate=substrate))
    signal.signal(signal.SIGINT, partial(stop_signal_handler, substrate=substrate))

    while True:
        try:
            oracle.start_default_mode()

        except Exception as exc:
            exc_type = type(exc)
            if exc_type in [
                ABIFunctionNotFound,
                AssertionError,
                asyncio.exceptions.TimeoutError,
                BadFunctionCallOutput,
                BlockNotFound,
                BrokenPipeError,
                ConnectionClosedError,
                ConnectionRefusedError,
                ConnectionResetError,
                gaierror,
                InvalidMessage,
                InvalidStatusCode,
                KeyError,
                OSError,
                SubstrateRequestException,
                TimeExhausted,
                TimeoutError,
                ValidationError,
                ValueError,
                WebSocketAddressException,
                WebSocketConnectionClosedException,
            ]:
                logger.warning(f"Error: {exc}")
            else:
                logger.error(f"Error: {exc}")
            oracle.start_recovery_mode()


def create_w3_provider_forcibly(ws_urls_para: list, timeout: int) -> Web3:
    """Force attempt to create a Web3 object"""
    while True:
        try:
            w3 = create_provider(ws_urls_para, timeout)

        except Exception as exc:
            exc_type = type(exc)
            if exc_type in [
                asyncio.exceptions.TimeoutError,
                BrokenPipeError,
                ConnectionClosedError,
                ConnectionResetError,
                gaierror,
                InvalidMessage,
                InvalidStatusCode,
                OSError,
                TimeoutError,
                WebSocketConnectionClosedException,
            ]:
                logger.warning(f"Error: {exc}")
            else:
                logger.error(f"Error: {exc}")

        else:
            return w3


if __name__ == '__main__':
    main()
