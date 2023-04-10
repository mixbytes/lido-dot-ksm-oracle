import logging
import os
import sys
import utils

from eth_account.account import Account
from eth_typing import ChecksumAddress
from pathlib import Path
from substrateinterface import SubstrateInterface
from threading import Lock
from web3 import Web3


DEFAULT_REST_API_IP_ADDRESS = '0.0.0.0'
DEFAULT_REST_API_PORT = 8000
DEFAULT_TIMEOUT = 60

DEFAULT_ABI_PATH = Path(__file__).parent.parent.as_posix() + '/assets/oracle.json'
DEFAULT_ERA_DELAY_TIME = 600
DEFAULT_ERA_DURATION_IN_BLOCKS = 30
DEFAULT_ERA_DURATION_IN_SECONDS = 180
DEFAULT_ERA_UPDATE_DELAY = 360
DEFAULT_FREQUENCY_OF_REQUESTS = 180
DEFAULT_GAS_LIMIT = 10000000
DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS = 10
DEFAULT_MAX_PRIORITY_FER_PER_GAS = 0
DEFAULT_SS58_FORMAT = 42
DEFAULT_TYPE_REGISTRY_PRESET = 'kusama'
DEFAULT_WAITING_TIME_BEFORE_SHUTDOWN = 600

MAX_ATTEMPTS_TO_RECONNECT = 20

logger = logging.getLogger(__name__)


class ServiceParameters:
    account: Account
    contract_address: ChecksumAddress
    abi: list
    gas_limit: int

    era_duration_in_blocks: int
    era_duration_in_seconds: int
    era_update_delay: int
    era_delay_time: int

    debug_mode: bool
    frequency_of_requests: int
    max_number_of_failure_requests: int
    oracle_status_lock: Lock
    timeout: int
    waiting_time_before_shutdown: int

    rest_api_ip_address: str
    rest_api_port: int

    ss58_format: int
    type_registry_preset: str
    ws_urls_relay: list
    ws_urls_para: list

    substrate: SubstrateInterface
    w3: Web3

    def __init__(self, oracle_status_lock: Lock):
        self.oracle_status_lock = oracle_status_lock

        logger.info("Checking the configuration parameters")

        self.rest_api_ip_address = os.getenv('REST_API_SERVER_IP_ADDRESS', DEFAULT_REST_API_IP_ADDRESS)
        self.rest_api_port = int(os.getenv('REST_API_SERVER_PORT', DEFAULT_REST_API_PORT))
        assert self.rest_api_port > 0, "The 'REST_API_SERVER_PORT' parameter must be non-negative integer"

        logger.info("Checking URLs")
        self.ws_urls_relay = os.getenv('WS_URL_RELAY').split(',')
        assert not utils.is_invalid_urls(self.ws_urls_relay), "Invalid urls were found in the 'WS_URL_RELAY' parameter"

        self.ws_urls_para = os.getenv('WS_URL_PARA').split(',')
        assert not utils.is_invalid_urls(self.ws_urls_para), "Invalid urls were found in the 'WS_URL_PARA' parameter"
        logger.info("URLs checked")

        logger.info("Getting ss58 format and registry type")
        self.ss58_format = int(os.getenv('SS58_FORMAT', DEFAULT_SS58_FORMAT))
        self.type_registry_preset = os.getenv('TYPE_REGISTRY_PRESET', DEFAULT_TYPE_REGISTRY_PRESET)

        logger.info("Checking the path to the ABI")
        abi_path = os.getenv('ABI_PATH', DEFAULT_ABI_PATH)
        utils.check_abi_path(abi_path)
        logger.info("The path to the ABI is checked")

        self.max_priority_fee_per_gas = int(os.getenv('MAX_PRIORITY_FEE_PER_GAS', DEFAULT_MAX_PRIORITY_FER_PER_GAS))
        assert self.max_priority_fee_per_gas >= 0, "The 'MAX_PRIORITY_FEE_PER_GAS' parameter must be non-negative integer"

        self.gas_limit = int(os.getenv('GAS_LIMIT', DEFAULT_GAS_LIMIT))
        assert self.gas_limit > 0, "The 'GAS_LIMIT' parameter must be positive integer"

        self.era_delay_time = int(os.getenv('ERA_DELAY_TIME', DEFAULT_ERA_DELAY_TIME))
        assert self.era_delay_time >= 0, "The 'ERA_DELAY_TIME' parameter must be non-negative integer"

        self.max_number_of_failure_requests = int(os.getenv(
            'MAX_NUMBER_OF_FAILURE_REQUESTS',
            DEFAULT_MAX_NUMBER_OF_FAILURE_REQUESTS,
        ))
        assert self.max_number_of_failure_requests > 0,\
            "The 'MAX_NUMBER_OF_FAILURE_REQUESTS' parameter must be positive integer"

        self.timeout = int(os.getenv('TIMEOUT', DEFAULT_TIMEOUT))
        assert self.timeout > 0, "The 'TIMEOUT' parameter must be positive integer"

        self.waiting_time_before_shutdown = int(os.getenv(
            'WAITING_TIME_BEFORE_SHUTDOWN',
            DEFAULT_WAITING_TIME_BEFORE_SHUTDOWN,
        ))
        assert self.waiting_time_before_shutdown >= 0, \
            "The 'WAITING_TIME_BEFORE_SHUTDOWN' parameter must be non-negative integer"

        self.era_duration_in_blocks = int(os.getenv('ERA_DURATION_IN_BLOCKS', DEFAULT_ERA_DURATION_IN_BLOCKS))
        assert self.era_duration_in_blocks > 0, "The 'ERA_DURATION_IN_BLOCKS' parameter must be positive integer"

        self.era_duration_in_seconds = int(os.getenv('ERA_DURATION_IN_SECONDS', DEFAULT_ERA_DURATION_IN_SECONDS))
        assert self.era_duration_in_seconds > 0, "The 'ERA_DURATION_IN_SECONDS' parameter must be positive integer"

        self.era_update_delay = int(os.getenv('ERA_UPDATE_DELAY', DEFAULT_ERA_UPDATE_DELAY))
        assert self.era_update_delay > 0, "The 'ERA_UPDATE_DELAY' parameter must be positive integer"

        self.frequency_of_requests = int(os.getenv('FREQUENCY_OF_REQUESTS', DEFAULT_FREQUENCY_OF_REQUESTS))
        assert self.frequency_of_requests > 0, "The 'FREQUENCY_OF_REQUESTS' parameter must be positive integer"

        self.debug_mode = True if os.getenv('ORACLE_MODE') == 'DEBUG' else False
        if self.debug_mode:
            logger.info("Oracle is running in debug mode")

        logger.info("Creating a Web3 object")
        self.w3 = self._create_provider_forcibly(self.ws_urls_para)
        logger.info("Creating a SubstrateInterface object")
        self.substrate = self._create_interface_forcibly(self.ws_urls_relay)

        oracle_private_key_path = os.getenv('ORACLE_PRIVATE_KEY_PATH')
        oracle_private_key = utils.get_private_key(oracle_private_key_path, os.getenv('ORACLE_PRIVATE_KEY'))
        assert oracle_private_key, "Failed to parse private key"
        # Check private key. Throws an exception if the length is not 32 bytes
        self.w3.eth.account.from_key(oracle_private_key)

        logger.info("Checking the contract address")
        contract_address = os.getenv('CONTRACT_ADDRESS')
        assert contract_address, "The contract address is not provided"
        self.contract_address = self.w3.to_checksum_address(contract_address)
        utils.check_contract_address(self.w3, self.contract_address)
        logger.info("The contract address is checked")

        self.account = self.w3.eth.account.from_key(oracle_private_key)
        logger.info("Checking the ABI")
        self.abi = utils.get_abi(abi_path)
        utils.check_abi(self.w3, self.contract_address, self.abi)
        logger.info("The ABI is checked")

        logger.info("Successfully checked configuration parameters")

    def _create_provider_forcibly(self, ws_urls: list) -> Web3:
        """Force attempt to create a Web3 object"""
        for _ in range(0, MAX_ATTEMPTS_TO_RECONNECT):
            try:
                w3 = utils.create_provider(ws_urls, self.timeout)

            except Exception as exc:
                exc_type = type(exc)
                if exc_type in utils.EXPECTED_NETWORK_EXCEPTIONS:
                    logger.warning(f"Error: {exc}")
                else:
                    logger.error(f"Error: {exc}")

            else:
                return w3

        sys.exit("Failed to create a Web3 object")

    def _create_interface_forcibly(self, ws_urls: list) -> SubstrateInterface:
        """Force attempt to create a SubstrateInterface object"""
        for _ in range(0, MAX_ATTEMPTS_TO_RECONNECT):
            try:
                substrate = utils.create_interface(
                    urls=ws_urls,
                    ss58_format=self.ss58_format,
                    type_registry_preset=self.type_registry_preset,
                    timeout=self.timeout,
                )

            except Exception as exc:
                exc_type = type(exc)
                if exc_type in utils.EXPECTED_NETWORK_EXCEPTIONS:
                    logger.warning(f"Error: {exc}")
                else:
                    logger.error(f"Error: {exc}")

            else:
                return substrate

        sys.exit("Failed to create a SubstrateInterface object")
