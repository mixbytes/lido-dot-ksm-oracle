from dataclasses import dataclass, field
from service_parameters import ServiceParameters
from substrateinterface.exceptions import BlockNotFound, SubstrateRequestException
from substrateinterface import Keypair
from substrate_interface_utils import SubstrateInterfaceUtils
from utils import create_provider
from web3.exceptions import BadFunctionCallOutput
from web3 import Account
from websocket._exceptions import WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage

import logging
import signal
import socket
import threading as th
import time
import sys


logger = logging.getLogger(__name__)


@dataclass
class Oracle:
    """A class that contains all the logic of the oracle's work"""
    account: Account
    service_params: ServiceParameters

    default_mode_started: bool = False
    failure_reqs_count: dict = field(default_factory=dict)
    last_era_reported: dict = field(default_factory=dict)
    previous_era_id: int = -1
    undesirable_urls: set = field(default_factory=set)
    watchdog: th.Timer = field(init=False)

    def __post_init__(self):
        self._create_watchdog()
        signal.signal(signal.SIGALRM, self._close_connection_to_relaychain)

    def start_default_mode(self):
        """Start of the Oracle default mode"""
        if not self.default_mode_started:
            self.default_mode_started = True
        else:
            logger.warning("The default oracle mode is already working")

        logger.info("Starting default mode")

        if self.service_params.substrate.url not in self.failure_reqs_count:
            self.failure_reqs_count[self.service_params.substrate.url] = 0
        if self.service_params.w3.provider.endpoint_uri not in self.failure_reqs_count:
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0

        self.failure_reqs_count[self.service_params.substrate.url] += 1
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1

        self.nonce = self.service_params.w3.eth.get_transaction_count(self.account.address)

        self._restore_state()
        self._start_era_monitoring()

    def start_recovery_mode(self):
        """Start of the Oracle recovery mode."""
        logger.info("Starting recovery mode")
        self.default_mode_started = False

        self._recover_connection_to_relaychain()
        self._recover_connection_to_parachain()

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
                    self.service_params.substrate = SubstrateInterfaceUtils.create_interface(
                        urls=self.service_params.ws_urls_relay,
                        ss58_format=self.service_params.ss58_format,
                        type_registry_preset=self.service_params.type_registry_preset,
                        timeout=self.service_params.timeout,
                        undesirable_urls=self.undesirable_urls,
                    )
                break

            except (
                BadFunctionCallOutput,
                BrokenPipeError,
                ConnectionClosedError,
                ConnectionRefusedError,
                ConnectionResetError,
                InvalidMessage,
                WebSocketConnectionClosedException,
            ) as exc:
                logger.warning(f"Error: {exc}")
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

            except (
                BadFunctionCallOutput,
                BrokenPipeError,
                ConnectionClosedError,
                ConnectionRefusedError,
                ConnectionResetError,
                InvalidMessage,
                WebSocketConnectionClosedException,
            ) as exc:
                logger.warning(f"Error: {exc}")
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
        stash_accounts = self._get_stash_accounts()
        for stash_acc in stash_accounts:
            (era_id, is_reported) = self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
            ).functions.isReportedLastEra(self.account.address, stash_acc).call()

            stash = Keypair(public_key=stash_acc, ss58_format=self.service_params.ss58_format)
            self.last_era_reported[stash.public_key] = era_id if is_reported else era_id - 1

    def _get_stash_accounts(self) -> list:
        """Get list of stash accounts and the last era reported using 'getStashAccounts' function"""
        return self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
               ).functions.getStashAccounts().call()

    def _start_era_monitoring(self):
        """Start monitoring an era change event"""
        self.service_params.substrate.query(
            module='Staking',
            storage_function='ActiveEra',
            subscription_handler=self._handle_era_change,
        )

    def _handle_watchdog_tick(self):
        """Start the timer for SIGALRM and end the thread"""
        signal.alarm(self.service_params.era_duration_in_seconds + self.service_params.watchdog_delay)
        sys.exit()

    def _close_connection_to_relaychain(self, sig: int = signal.SIGINT, frame=None):
        """Close connection to relaychain node, increase failure requests counter and exit"""
        self.failure_reqs_count[self.service_params.substrate.url] += 1
        logger.debug(f"Closing connection to relaychain node: {self.service_params.substrate.url}")
        try:
            self.service_params.substrate.websocket.sock.shutdown(socket.SHUT_RDWR)
        except (
            AttributeError,
            OSError,
        ) as exc:
            logger.warning(exc)

        if sig == signal.SIGALRM:
            raise BrokenPipeError

        sys.exit()

    def _create_watchdog(self):
        """Create watchdog as a Timer"""
        self.watchdog = th.Timer(1, self._handle_watchdog_tick)

    def _wait_in_two_blocks(self, tx_receipt: dict):
        """Wait for two blocks based on information from web3"""
        if 'blockNumber' not in tx_receipt:
            logger.error("The block number in transaction receipt was not found")
            return

        logger.info("Waiting in two blocks")
        while True:
            current_block = self.service_params.w3.eth.get_block('latest')
            if current_block is not None and 'number' in current_block:
                if current_block['number'] > tx_receipt['blockNumber']:
                    break
            time.sleep(1)

    def _handle_era_change(self, era, update_nr: int, subscription_id: str):
        """
        Read the staking parameters for each stash account separately from the block where
        the era value is changed, generate the transaction body, sign and send to the parachain.
        """
        self.watchdog.cancel()
        self._create_watchdog()
        self.watchdog.start()

        active_era_id = era.value['index']
        if active_era_id <= self.previous_era_id:
            logger.info(f"Skip sporadic new era event {active_era_id}")
            return
        logger.info(f"Active era index: {active_era_id}, start timestamp: {era.value['start']}")

        era_id = active_era_id - 4
        self.nonce = self.service_params.w3.eth.get_transaction_count(self.account.address)
        self.failure_reqs_count[self.service_params.substrate.url] += 1
        stash_accounts = self._get_stash_accounts()
        self.failure_reqs_count[self.service_params.substrate.url] -= 1
        if not stash_accounts:
            logger.info("No stash accounts found: waiting for the next era")
            self.previous_era_id = era_id
            return

        block_hash = self._find_start_block(era.value['index'])
        if block_hash is None:
            logger.error("Can't find the required block")
            raise BlockNotFound

        for stash_acc in stash_accounts:
            self.failure_reqs_count[self.service_params.substrate.url] += 1

            stash = Keypair(public_key=stash_acc, ss58_format=self.service_params.ss58_format)

            if self.last_era_reported.get(stash.public_key, 0) >= era_id:
                logger.info(f"The report has already been sent for stash {stash.ss58_address}")
                continue

            staking_parameters = self._read_staking_parameters(stash, block_hash)
            self.failure_reqs_count[self.service_params.substrate.url] -= 1

            logger.info("The parameters are read. Preparing the transaction body.")
            logger.debug(';'.join([
                f"stash: {stash.ss58_address}",
                f"era: {era_id}",
                f"staking parameters: {staking_parameters}",
                f"Relay chain failure requests counter: {self.failure_reqs_count[self.service_params.substrate.url]}",
                f"Parachain failure requests counter: {self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri]}",
            ]))

            tx = self._create_tx(era_id, staking_parameters)
            self._sign_and_send_to_para(tx, stash, era_id)
            self.last_era_reported[stash.public_key] = era_id

        logger.info("Waiting for the next era")
        self.previous_era_id = era_id

        self.failure_reqs_count[self.service_params.substrate.url] = 0
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0
        if self.service_params.w3.provider.endpoint_uri in self.undesirable_urls:
            self.undesirable_urls.remove(self.service_params.w3.provider.endpoint_uri)

    def _find_start_block(self, era_id: int) -> str:
        """Find the hash of the block at which the era change occurs"""
        block_number = era_id * self.service_params.era_duration_in_blocks + self.service_params.initial_block_number

        try:
            block_hash = self.service_params.substrate.get_block_hash(block_number)
            logger.info(f"Block hash: {block_hash}. Block number: {block_number}")
            return block_hash
        except SubstrateRequestException:
            return None

    def _read_staking_parameters(self, stash: Keypair, block_hash: str = None) -> dict:
        """Read staking parameters from specific block or from the head"""
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        stash_free_balance = self._get_stash_free_balance(stash)
        stake_status = self._get_stake_status(stash, block_hash)

        staking_ledger_result = self._get_ledger_data(block_hash, stash)
        if staking_ledger_result is None:
            return {
                'stashAccount': stash.public_key,
                'controllerAccount': stash.public_key,
                'stakeStatus': 3,  # this value means that stake status is None
                'activeBalance': 0,
                'totalBalance': 0,
                'unlocking': [],
                'claimedRewards': [],
                'stashBalance': stash_free_balance,
            }

        controller = staking_ledger_result['controller']

        return {
            'stashAccount': stash.public_key,
            'controllerAccount': controller.public_key,
            'stakeStatus': stake_status,
            'activeBalance': staking_ledger_result['active'],
            'totalBalance': staking_ledger_result['total'],
            'unlocking': [{'balance': elem['value'], 'era': elem['era']} for elem in staking_ledger_result['unlocking']],
            'claimedRewards': [],  # put aside until storage proof has been implemented // staking_ledger_result['claimedRewards'],
            'stashBalance': stash_free_balance,
        }

    def _get_ledger_data(self, block_hash: str, stash: Keypair) -> dict:
        """Get ledger data using stash account address"""
        controller = SubstrateInterfaceUtils.get_controller(self.service_params.substrate, stash, block_hash)
        if controller.value is None:
            return None

        controller = Keypair(ss58_address=controller.value)

        ledger = SubstrateInterfaceUtils.get_ledger(self.service_params.substrate, controller, block_hash)

        result = {'controller': controller, 'stash': stash}
        result.update(ledger.value)

        return result

    def _get_stash_free_balance(self, stash: Keypair) -> int:
        """Get stash accounts free balances"""
        account = SubstrateInterfaceUtils.get_account(self.service_params.substrate, stash)

        return account.value['data']['free']

    def _get_stake_status(self, stash: Keypair, block_hash: str = None) -> int:
        """
        Get stash account status.
        0 - Idle, 1 - Nominator, 2 - Validator, 3 - None
        """
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        staking_validators = SubstrateInterfaceUtils.get_validators(self.service_params.substrate, block_hash)
        staking_nominators = SubstrateInterfaceUtils.get_nominators(self.service_params.substrate, block_hash)

        nominators = set(nominator.value for nominator, _ in staking_nominators)
        validators = set(validator for validator in staking_validators.value)

        if stash.ss58_address in nominators:
            return 1

        if stash.ss58_address in validators:
            return 2

        return 0

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
            return False

        tx_signed = self.service_params.w3.eth.account.sign_transaction(tx, private_key=self.account.privateKey)
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
        logger.info(f"Sending a transaction for stash {stash.ss58_address}")
        tx_hash = self.service_params.w3.eth.send_raw_transaction(tx_signed.rawTransaction)
        tx_receipt = self.service_params.w3.eth.wait_for_transaction_receipt(tx_hash)

        logger.debug(f"Transaction receipt: {tx_receipt}")

        if tx_receipt.status == 1:
            logger.info(f"The report for stash '{stash.ss58_address}' era {era_id} was sent successfully")
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] -= 1
            self._wait_in_two_blocks(tx_receipt)
            return True
        else:
            logger.warning(f"Transaction status for stash '{stash.ss58_address}' era {era_id} is reverted")
            return False
