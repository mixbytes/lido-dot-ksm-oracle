#!/usr/bin/env python3
import asyncio
import logging
import os
import signal
import sys
import utils

from flask import Flask, jsonify
from functools import partial
from log import init_log
from oracle import Oracle
from pathlib import Path
from prometheus_client import make_wsgi_app
from server_thread import ServerThread
from service_parameters import ServiceParameters
from socket import gaierror
from substrateinterface import SubstrateInterface
from substrateinterface.exceptions import BlockNotFound, SubstrateRequestException
from threading import Lock
from web3 import Web3
from web3.exceptions import ABIFunctionNotFound, BadFunctionCallOutput, TimeExhausted, ValidationError
from websocket._exceptions import WebSocketAddressException, WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage, InvalidStatusCode
from werkzeug.middleware.dispatcher import DispatcherMiddleware


logger = logging.getLogger(__name__)
flask_app = Flask(__name__)
flask_app.wsgi_app = DispatcherMiddleware(flask_app.wsgi_app, {
    '/metrics': make_wsgi_app(),
})

DEFAULT_LOG_LEVEL_STDOUT = 'INFO'
DEFAULT_REST_API_SERVER_IP_ADDRESS = '0.0.0.0'
DEFAULT_REST_API_SERVER_PORT = 8000
DEFAULT_TIMEOUT = 60

DEFAULT_ABI_PATH = Path(__file__).parent.parent.as_posix() + '/assets/oracle.json'
DEFAULT_ERA_DURATION_IN_BLOCKS = 30
DEFAULT_ERA_DURATION_IN_SECONDS = 180
DEFAULT_FREQUENCY_OF_REQUESTS = 180
DEFAULT_GAS_LIMIT = 10000000
DEFAULT_INITIAL_BLOCK_NUMBER = 1
DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS = 10
DEFAULT_PARA_ID = 999

MAX_ATTEMPTS_TO_RECONNECT = 20

utils.cache.init_app(app=flask_app, config={'CACHE_TYPE': 'SimpleCache'})
utils.cache.set('oracle_status', 'not working')
oracle_status_lock = Lock()


def main():
    try:
        log_level = os.getenv('LOG_LEVEL_STDOUT', DEFAULT_LOG_LEVEL_STDOUT)
        utils.check_log_level(log_level)
        init_log(stdout_level=log_level)

        logger.info("Checking the configuration parameters")

        rest_api_server_ip_address = os.getenv('REST_API_SERVER_IP_ADDRESS', DEFAULT_REST_API_SERVER_IP_ADDRESS)
        rest_api_server_port = int(os.getenv('REST_API_SERVER_PORT', DEFAULT_REST_API_SERVER_PORT))
        rest_api_server = ServerThread(flask_app, rest_api_server_port, rest_api_server_ip_address)
        logger.info(f"Starting the REST API server on port {rest_api_server_port}")
        rest_api_server.start()

        ws_urls_relay = os.getenv('WS_URL_RELAY', 'ws://localhost:9951/').split(',')
        assert not utils.is_invalid_urls(ws_urls_relay), "Invalid urls were found in 'WS_URL_RELAY' parameter"

        ws_urls_para = os.getenv('WS_URL_PARA', 'ws://localhost:10055/').split(',')
        assert not utils.is_invalid_urls(ws_urls_para), "Invalid urls were found in 'WS_URL_PARA' parameter"

        ss58_format = int(os.getenv('SS58_FORMAT', 2))
        type_registry_preset = os.getenv('TYPE_REGISTRY_PRESET', 'kusama')

        para_id = int(os.getenv('PARA_ID', DEFAULT_PARA_ID))
        assert para_id >= 0, "'PARA_ID' parameter must be non-negative integer"

        contract_address = os.getenv('CONTRACT_ADDRESS')
        if contract_address is None:
            sys.exit("No contract address provided")

        abi_path = os.getenv('ABI_PATH', DEFAULT_ABI_PATH)
        utils.check_abi_path(abi_path)

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

        abi = utils.get_abi(abi_path)

        logger.info("Creating a Web3 object")
        w3 = create_provider_forcibly(ws_urls_para, timeout)
        logger.info("Creating a SubstrateInterface object")
        substrate = create_interface_forcibly(ws_urls_relay, ss58_format, type_registry_preset)

        oracle_private_key_path = os.getenv('ORACLE_PRIVATE_KEY_PATH')
        oracle_private_key = utils.get_private_key(oracle_private_key_path, os.getenv('ORACLE_PRIVATE_KEY'))
        if oracle_private_key is None:
            sys.exit("Failed to parse oracle private key")
        # Check private key. Throws an exception if the length is not 32 bytes
        w3.eth.account.from_key(oracle_private_key)

        utils.check_contract_address(w3, contract_address)
        oracle = w3.eth.account.from_key(oracle_private_key)
        logger.info("Checking ABI")
        utils.check_abi(w3, contract_address, abi, oracle.address)

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
            oracle_status_lock=oracle_status_lock,
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

    signal.signal(signal.SIGTERM, partial(utils.stop_signal_handler, substrate=substrate, rest_api_server=rest_api_server))
    signal.signal(signal.SIGINT, partial(utils.stop_signal_handler, substrate=substrate, rest_api_server=rest_api_server))

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


@flask_app.route('/healthcheck', methods=['GET'])
def healthcheck():
    with oracle_status_lock:
        oracle_status = utils.cache.get('oracle_status')

    body = {'oracle_status': oracle_status}
    response = jsonify(body)
    response.status = 200

    return response


def create_provider_forcibly(ws_urls_para: list, timeout: int) -> Web3:
    """Force attempt to create a Web3 object"""
    for _ in range(0, MAX_ATTEMPTS_TO_RECONNECT):
        try:
            w3 = utils.create_provider(ws_urls_para, timeout)

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

    sys.exit("Failed to create a Web3 object")


def create_interface_forcibly(ws_urls_relay: list, ss58_format: int, type_registry_preset: str) -> SubstrateInterface:
    """Force attempt to create a SubstrateInterface object"""
    for _ in range(0, MAX_ATTEMPTS_TO_RECONNECT):
        try:
            substrate = utils.create_interface(ws_urls_relay, ss58_format, type_registry_preset)

        except Exception as exc:
            exc_type = type(exc)
            if exc_type in [
                asyncio.exceptions.TimeoutError,
                BrokenPipeError,
                ConnectionClosedError,
                ConnectionResetError,
                gaierror,
                InvalidMessage,
                OSError,
                TimeoutError,
                WebSocketConnectionClosedException,
            ]:
                logger.warning(f"Error: {exc}")
            else:
                logger.error(f"Error: {exc}")

        else:
            return substrate

    sys.exit("Failed to create a SubstrateInterface object")


if __name__ == '__main__':
    main()
