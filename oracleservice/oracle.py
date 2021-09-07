from dataclasses import dataclass, field
from service_parameters import ServiceParameters
from substrateinterface.exceptions import BlockNotFound, SubstrateRequestException
from substrateinterface.utils.ss58 import ss58_decode
from substrate_interface_utils import SubstrateInterfaceUtils
from utils import create_provider
from web3.exceptions import BadFunctionCallOutput
from websocket._exceptions import WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage

import logging
import socket
import threading as th
import time


logger = logging.getLogger(__name__)


@dataclass
class Oracle:
    """A class that contains all the logic of the oracle's work"""
    priv_key: str
    service_params: ServiceParameters

    account = None
    default_mode_started: bool = False
    failure_reqs_count: dict = field(default_factory=dict)
    last_era_reported: dict = field(default_factory=dict)
    previous_era_id: int = -1
    undesirable_urls: set = field(default_factory=set)
    watchdog: th.Timer = field(init=False)

    def __post_init__(self):
        self._create_watchdog()

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

        self.account = self.service_params.w3.eth.account.from_key(self.priv_key)

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

    def _close_connection_to_relaychain(self):
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

        exit()

    def _create_watchdog(self):
        self.watchdog = th.Timer(self.service_params.era_duration_in_seconds, self._close_connection_to_relaychain)

    def _wait_in_two_blocks(self, tx_receipt: dict):
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
        era_id = era.value['index']
        if era_id == self.previous_era_id:
            return

        self.failure_reqs_count[self.service_params.substrate.url] += 1
        stash_accounts = self._get_stash_accounts()
        self.failure_reqs_count[self.service_params.substrate.url] -= 1
        if not stash_accounts:
            logger.info("No stake accounts found: waiting for the next era")
            return

        self.watchdog.cancel()
        self._create_watchdog()
        self.watchdog.start()

        block_hash = self._find_start_block(era.value['index'])
        if block_hash is None:
            logger.error("Can't find the required block")
            raise BlockNotFound

        for stash_acc, stash_era_id in stash_accounts:
            self.failure_reqs_count[self.service_params.substrate.url] += 1
            stash_acc = '0x' + stash_acc.hex()
            logger.info(f"Current stash is {stash_acc}; era is {stash_era_id}")
            if era_id < stash_era_id:
                logger.info(f"Current era less than the specified era for stash '{stash_acc}': skipping current era")
                continue

            if stash_acc in self.last_era_reported and self.last_era_reported[stash_acc] >= era.value['index']:
                logger.info(f"The report has already been sent for stash {stash_acc}")
                continue

            staking_parameters = self._read_staking_parameters(stash_acc, block_hash)
            self.failure_reqs_count[self.service_params.substrate.url] -= 1

            logger.info("The parameters are read. Preparing the transaction body.")
            logger.debug(';'.join([
                f"stash: {stash_acc}",
                f"era: {era_id}",
                f"staking parameters: {staking_parameters}",
                f"Relay chain failure requests counter: {self.failure_reqs_count[self.service_params.substrate.url]}",
                f"Parachain failure requests counter: {self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri]}",
            ]))

            tx = self._create_tx(era_id, staking_parameters)
            self._sign_and_send_to_para(tx, stash_acc, era_id)
            self.last_era_reported[stash_acc] = era_id

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

    def _read_staking_parameters(self, stash: str, block_hash: str = None) -> dict:
        """Read staking parameters from specific block or from the head"""
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        stash_free_balance = self._get_stash_free_balance(stash)
        staking_ledger_result = self._get_ledger_data(block_hash, stash)
        if staking_ledger_result is None:
            return {
                'stashAccount': stash,
                'controllerAccount': stash,
                'stakeStatus': 3,  # this value means that stake status is None
                'activeBalance': 0,
                'totalBalance': 0,
                'unlocking': [],
                'claimedRewards': [],
                'stashBalance': stash_free_balance,
            }

        stake_status = self._get_ledger_status(stash, block_hash)

        for controller, controller_info in staking_ledger_result.items():
            unlocking_values = [{'balance': elem['value'], 'era': elem['era']} for elem in controller_info.value['unlocking']]

            return {
                'stashAccount': '0x' + ss58_decode(controller_info.value['stash']),
                'controllerAccount': '0x' + ss58_decode(controller),
                'stakeStatus': stake_status,
                'activeBalance': controller_info.value['active'],
                'totalBalance': controller_info.value['total'],
                'unlocking': unlocking_values,
                'claimedRewards': controller_info.value['claimedRewards'],
                'stashBalance': stash_free_balance,
            }

    def _get_ledger_data(self, block_hash: str, stash: str) -> dict:
        """Get ledger data using stash account address"""
        controller = SubstrateInterfaceUtils.get_controller(self.service_params.substrate, stash, block_hash)
        if controller.value is None:
            return None

        ledger = SubstrateInterfaceUtils.get_ledger(self.service_params.substrate, controller.value, block_hash)

        return {controller.value: ledger}

    def _get_stash_free_balance(self, stash: str) -> dict:
        """Get stash accounts free balances"""
        account = SubstrateInterfaceUtils.get_account(self.service_params.substrate, stash)

        return account.value['data']['free']

    def _get_ledger_status(self, stash: str, block_hash: str = None) -> int:
        """
        Get stash account status.
        0 - Idle, 1 - Nominator, 2 - Validator
        """
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        staking_validators = SubstrateInterfaceUtils.get_validators(self.service_params.substrate, block_hash)
        staking_nominators = SubstrateInterfaceUtils.get_nominators(self.service_params.substrate, block_hash)

        nominators = set(nominator.value for nominator, _ in staking_nominators)
        validators = set(validator for validator in staking_validators.value)

        if stash in nominators:
            return 1

        if stash in validators:
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

    def _sign_and_send_to_para(self, tx: dict, stash: str, era_id: int) -> bool:
        """Sign transaction and send to parachain"""
        try:
            self.service_params.w3.eth.call(dict((k, v) for k, v in tx.items() if v))

            del tx['from']
        except ValueError as exc:
            msg = exc.args[0]["message"] if isinstance(exc.args[0], dict) else str(exc)

            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
            logger.warning(f"Report for '{stash}' era {era_id} probably will fail with {msg}")
            return False

        tx_signed = self.service_params.w3.eth.account.sign_transaction(tx, private_key=self.priv_key)
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
        logger.info(f"Sending a transaction for stash {stash}")
        tx_hash = self.service_params.w3.eth.send_raw_transaction(tx_signed.rawTransaction)
        tx_receipt = self.service_params.w3.eth.wait_for_transaction_receipt(tx_hash)

        logger.debug(f"Transaction receipt: {tx_receipt}")

        if tx_receipt.status == 1:
            logger.info(f"The report for stash '{stash}' era {era_id} was sent successfully")
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] -= 1
            self._wait_in_two_blocks(tx_receipt)
            return True
        else:
            logger.warning(f"Transaction is reverted for stash {stash} with era {era_id}")
            return False
