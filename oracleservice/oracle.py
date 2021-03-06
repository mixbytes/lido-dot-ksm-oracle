import asyncio
import logging
import time

from dataclasses import dataclass, field
from prometheus_metrics import metrics_exporter
from report_parameters_reader import ReportParametersReader
from service_parameters import ServiceParameters
from socket import gaierror
from substrateinterface.exceptions import BlockNotFound, SubstrateRequestException
from substrateinterface import Keypair
from utils import cache, create_interface, create_provider
from web3.exceptions import BadFunctionCallOutput
from web3 import Account
from websocket._exceptions import WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage, InvalidStatusCode


logger = logging.getLogger(__name__)


@dataclass
class Oracle:
    """A class that contains all the logic of the oracle's work"""
    account: Account
    service_params: ServiceParameters

    default_mode_started: bool = False
    failure_reqs_count: dict = field(default_factory=dict)
    last_era_reported: dict = field(default_factory=dict)
    previous_active_era_id: int = -1
    undesirable_urls: set = field(default_factory=set)
    was_recovered: bool = False

    def __post_init__(self):
        self.report_parameters_reader = ReportParametersReader(self.service_params)

    def start_default_mode(self):
        """Start of the Oracle default mode"""
        with self.service_params.oracle_status_lock:
            cache.set('oracle_status', 'starting')

        if not self.default_mode_started:
            self.default_mode_started = True
        else:
            logger.warning("The default oracle mode is already working")

        logger.info("Starting default mode")

        metrics_exporter.agent.info({'relay_chain_node_address': self.service_params.substrate.url})
        if self.service_params.substrate.url not in self.failure_reqs_count:
            self.failure_reqs_count[self.service_params.substrate.url] = 0
        if self.service_params.w3.provider.endpoint_uri not in self.failure_reqs_count:
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0

        self.failure_reqs_count[self.service_params.substrate.url] += 1
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1

        with metrics_exporter.para_exceptions_count.count_exceptions():
            self._restore_state()

        balance = self.service_params.w3.eth.get_balance(self.account.address)
        metrics_exporter.oracle_balance.labels(self.account.address).set(balance)

        while True:
            logger.debug(f"Getting active era. Previous active era id: {self.previous_active_era_id}")
            with self.service_params.oracle_status_lock:
                cache.set('oracle_status', 'monitoring')
            active_era = self.service_params.substrate.query(
                module='Staking',
                storage_function='ActiveEra',
            )

            active_era_id = active_era.value['index']
            if active_era_id > self.previous_active_era_id:
                self._handle_era_change(active_era_id, active_era.value['start'])
            elif self.was_recovered:
                logger.info(f"Era {active_era_id - 1} has already been processed. Waiting for the next era")
                self.was_recovered = False

            with self.service_params.oracle_status_lock:
                cache.set('oracle_status', 'monitoring')
            logger.debug(f"Sleep for {self.service_params.frequency_of_requests} seconds until the next request")
            time.sleep(self.service_params.frequency_of_requests)

    def start_recovery_mode(self):
        """Start of the Oracle recovery mode."""
        logger.info("Starting recovery mode")
        with self.service_params.oracle_status_lock:
            cache.set('oracle_status', 'recovering')
        metrics_exporter.is_recovery_mode_active.set(True)
        self.default_mode_started = False

        self.was_recovered = True
        self._recover_connection_to_relaychain()
        self._recover_connection_to_parachain()

        metrics_exporter.is_recovery_mode_active.set(False)
        logger.info("Recovery mode is completed")

    def _recover_connection_to_relaychain(self):
        """
        Recover connection to relaychain.
        If failure requests counter exceeds the allowed value, reconnect to another node.
        """
        while True:
            try:
                if self.failure_reqs_count[self.service_params.substrate.url] > self.service_params.max_num_of_failure_reqs:
                    self.undesirable_urls.add(self.service_params.substrate.url)
                    self.service_params.substrate.websocket.shutdown()
                    self.service_params.substrate = create_interface(
                        urls=self.service_params.ws_urls_relay,
                        ss58_format=self.service_params.ss58_format,
                        type_registry_preset=self.service_params.type_registry_preset,
                        timeout=self.service_params.timeout,
                        undesirable_urls=self.undesirable_urls,
                        substrate=self.service_params.substrate,
                    )
                metrics_exporter.agent.info({'relay_chain_node_address': self.service_params.substrate.url})
                break

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

                if self.service_params.substrate.url in self.failure_reqs_count:
                    self.failure_reqs_count[self.service_params.substrate.url] += 1
                else:
                    self.failure_reqs_count[self.service_params.substrate.url] = 1

    def _recover_connection_to_parachain(self):
        """
        Recover connection to parachain.
        If failure requests counter exceeds the allowed value, reconnect to another node.
        """
        while True:
            try:
                if self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] > self.service_params.max_num_of_failure_reqs:
                    self.undesirable_urls.add(self.service_params.w3.provider.endpoint_uri)
                    self.service_params.w3 = create_provider(
                        urls=self.service_params.ws_urls_para,
                        timeout=self.service_params.timeout,
                        undesirable_urls=self.undesirable_urls,
                    )
                break

            except Exception as exc:
                exc_type = type(exc)
                if exc_type in [
                    asyncio.exceptions.TimeoutError,
                    BadFunctionCallOutput,
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

                if self.service_params.w3.provider.endpoint_uri in self.failure_reqs_count:
                    self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
                else:
                    self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 1

            except KeyError:
                if self.service_params.w3.provider.endpoint_uri in self.failure_reqs_count:
                    self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
                else:
                    self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 1

    def _restore_state(self):
        """Restore the state after starting the default mode"""
        stash_accounts = self.service_params.w3.eth.contract(
            address=self.service_params.contract_address,
            abi=self.service_params.abi,
        ).functions.getStashAccounts().call()
        for stash_acc in stash_accounts:
            (era_id, is_reported) = self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
            ).functions.isReportedLastEra(self.account.address, stash_acc).call()

            stash = Keypair(public_key=stash_acc, ss58_format=self.service_params.ss58_format)
            self.last_era_reported[stash.public_key] = era_id if is_reported else era_id - 1

    def _wait_in_two_blocks(self, tx_receipt: dict):
        """Wait for two blocks based on information from web3"""
        if 'blockNumber' not in tx_receipt:
            logger.error("The block number in transaction receipt was not found")
            return

        logger.debug("Waiting in two blocks")
        while True:
            current_block = self.service_params.w3.eth.get_block('latest')
            if current_block is not None and 'number' in current_block:
                if current_block['number'] > tx_receipt['blockNumber']:
                    break
            time.sleep(1)

    def _wait_until_finalizing(self, block_hash: str, block_number: int):
        """Wait until the block is finalized"""
        logger.debug(f"Waiting until the block {block_number} is finalized")
        finalised_head = self.service_params.substrate.get_chain_finalised_head()
        finalised_head_number = self.service_params.substrate.get_block_header(finalised_head)['header']['number']

        while finalised_head_number < block_number:
            time.sleep(self.service_params.era_duration_in_seconds / self.service_params.era_duration_in_blocks)
            finalised_head = self.service_params.substrate.get_chain_finalised_head()
            finalised_head_number = self.service_params.substrate.get_block_header(finalised_head)['header']['number']

        block_current = self.service_params.substrate.get_block_header(block_number=block_number)['header']['hash']
        if block_current != block_hash:
            raise BlockNotFound

    def _handle_era_change(self, active_era_id: int, era_start_timestamp: int):
        """
        Read the staking parameters for each stash account separately from the block where
        the era value is changed, generate the transaction body, sign and send to the parachain.
        """
        with self.service_params.oracle_status_lock:
            cache.set('oracle_status', 'processing')

        logger.info(f"Active era index: {active_era_id}, start timestamp: {era_start_timestamp}")
        metrics_exporter.active_era_id.set(active_era_id)
        metrics_exporter.total_stashes_free_balance.set(0)

        self.failure_reqs_count[self.service_params.substrate.url] += 1
        with metrics_exporter.para_exceptions_count.count_exceptions():
            stash_accounts = self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi,
            ).functions.getStashAccounts().call()
        self.failure_reqs_count[self.service_params.substrate.url] -= 1
        if not stash_accounts:
            logger.info("No stash accounts found: waiting for the next era")
            self.previous_active_era_id = active_era_id
            return

        with metrics_exporter.relay_exceptions_count.count_exceptions():
            block_hash, block_number = self._find_last_block(active_era_id)
            self._wait_until_finalizing(block_hash, block_number)
        metrics_exporter.previous_era_change_block_number.set(block_number)

        for stash_acc in stash_accounts:
            self.failure_reqs_count[self.service_params.substrate.url] += 1

            stash = Keypair(public_key=stash_acc, ss58_format=self.service_params.ss58_format)

            if self.last_era_reported.get(stash.public_key, 0) >= active_era_id - 1:
                logger.info(f"The report has already been sent for stash {stash.ss58_address}")
                continue

            staking_parameters = self.report_parameters_reader.get_stash_staking_parameters(stash, block_hash)
            self.failure_reqs_count[self.service_params.substrate.url] -= 1

            logger.info('; '.join([
                "The parameters are read. Preparing the transaction body",
                f"stash: {stash.ss58_address}",
                f"era: {active_era_id - 1}",
                f"staking parameters: {staking_parameters}",
            ]))
            logger.debug('; '.join([
                f"Relay chain failure requests counter: {self.failure_reqs_count[self.service_params.substrate.url]}",
                f"Parachain failure requests counter: {self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri]}",
            ]))

            with metrics_exporter.para_exceptions_count.count_exceptions():
                tx = self._create_tx(active_era_id - 1, staking_parameters)
                if not self.service_params.debug_mode:
                    self._sign_and_send_to_para(tx, stash, active_era_id - 1)
                else:
                    logger.info(f"Skipping sending the transaction for stash {stash.ss58_address}: oracle is running in debug mode")
                balance = self.service_params.w3.eth.get_balance(self.account.address)
                metrics_exporter.oracle_balance.labels(self.account.address).set(balance)
            self.last_era_reported[stash.public_key] = active_era_id - 1

        logger.info("Waiting for the next era")
        metrics_exporter.last_era_reported.set(active_era_id - 1)
        self.previous_active_era_id = active_era_id

        self.failure_reqs_count[self.service_params.substrate.url] = 0
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0
        if self.service_params.w3.provider.endpoint_uri in self.undesirable_urls:
            self.undesirable_urls.remove(self.service_params.w3.provider.endpoint_uri)

    def _find_last_block(self, era_id: int) -> (str, int):
        """Find the last block of the previous era"""
        try:
            current_block_hash = self.service_params.substrate.get_chain_head()
            current_block_number = self.service_params.substrate.get_block_number(current_block_hash)
            start = 0
            if current_block_number - self.service_params.era_duration_in_blocks > 0:
                start = current_block_number - self.service_params.era_duration_in_blocks

            end = current_block_number
            while start <= end:
                mid = (start + end) // 2
                block_hash = self.service_params.substrate.get_block_hash(mid)
                era = self.service_params.substrate.query(
                    module='Staking',
                    storage_function='ActiveEra',
                    block_hash=block_hash,
                )

                if era.value['index'] < era_id:
                    start = mid + 1
                else:
                    end = mid - 1

            if era.value['index'] == era_id:
                block_number = mid - 1
                block_hash = self.service_params.substrate.get_block_hash(block_number)
            else:
                block_number = mid

        except SubstrateRequestException:
            logger.error("Can't find the required block")
            raise BlockNotFound

        logger.info(f"Block hash: {block_hash}. Block number: {block_number}")
        return block_hash, block_number

    def _create_tx(self, era_id: int, staking_parameters: dict) -> dict:
        """Create a transaction body using the staking parameters, era id and parachain balance"""
        nonce = self.service_params.w3.eth.get_transaction_count(self.account.address)

        return self.service_params.w3.eth.contract(
            address=self.service_params.contract_address,
            abi=self.service_params.abi
        ).functions.reportRelay(
            era_id,
            staking_parameters,
        ).buildTransaction({'from': self.account.address, 'gas': self.service_params.gas_limit, 'nonce': nonce})

    def _sign_and_send_to_para(self, tx: dict, stash: Keypair, era_id: int) -> bool:
        """Sign transaction and send to parachain"""
        try:
            self.service_params.w3.eth.call(dict((k, v) for k, v in tx.items() if v))

            del tx['from']
        except ValueError as exc:
            msg = exc.args[0]["message"] if isinstance(exc.args[0], dict) else str(exc)

            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
            logger.warning(f"Report for '{stash.ss58_address}' era {era_id} probably will fail with {msg}")
            metrics_exporter.last_failed_era.set(era_id)
            metrics_exporter.tx_revert.observe(1)
            return False

        tx_signed = self.service_params.w3.eth.account.sign_transaction(tx, private_key=self.account.privateKey)
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
        logger.info(f"Sending a transaction for stash {stash.ss58_address}")
        tx_hash = self.service_params.w3.eth.send_raw_transaction(tx_signed.rawTransaction)
        logger.info(f"Transaction hash: {tx_hash.hex()}")
        tx_receipt = self.service_params.w3.eth.wait_for_transaction_receipt(tx_hash)

        logger.debug(f"Transaction receipt: {tx_receipt}")

        if tx_receipt.status == 1:
            logger.info(f"The report for stash '{stash.ss58_address}' era {era_id} was sent successfully")
            metrics_exporter.tx_success.observe(1)
            metrics_exporter.time_elapsed_until_last_report.set(time.time())
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] -= 1
            self._wait_in_two_blocks(tx_receipt)
            return True
        else:
            logger.warning(f"Transaction status for stash '{stash.ss58_address}' era {era_id} is reverted")
            metrics_exporter.last_failed_era.set(era_id)
            metrics_exporter.tx_revert.observe(1)
            return False
