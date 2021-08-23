from dataclasses import dataclass, field
from service_parameters import ServiceParameters
from substrateinterface.base import QueryMapResult
from substrateinterface.exceptions import BlockNotFound, SubstrateRequestException
from substrateinterface.utils.ss58 import ss58_decode
from substrate_interface_utils import SubstrateInterfaceUtils
from utils import create_provider
from web3.exceptions import BadFunctionCallOutput
from websocket._exceptions import WebSocketConnectionClosedException
from websockets.exceptions import ConnectionClosedError, InvalidMessage

import logging


logger = logging.getLogger(__name__)


@dataclass
class Oracle:
    """A class that contains all the logic of the oracle's work"""
    priv_key: str
    service_params: ServiceParameters

    account = None
    default_mode_started: bool = False
    failure_reqs_count: dict = field(default_factory=dict)
    last_era_reported: int = -1
    undesirable_urls: set = field(default_factory=set)

    def start_default_mode(self):
        """Start of the Oracle default mode"""
        if not self.default_mode_started:
            self.default_mode_started = True
        else:
            logger.warning("The default oracle mode is already working")

        logger.info("Starting default mode")

        self.account = self.service_params.w3.eth.account.from_key(self.priv_key)
        if self.service_params.substrate.url not in self.failure_reqs_count:
            self.failure_reqs_count[self.service_params.substrate.url] = 0
        if self.service_params.w3.provider.endpoint_uri not in self.failure_reqs_count:
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0

        self._start_era_monitoring()

    def start_recovery_mode(self):
        '''
        Start of the Oracle recovery mode.
        The current era id (CEI) from relay chain and oracle report era id (ORED)
        from parachain are being compared. If CEI less than ORED, then do not send a report.
        If failure requests counter exceeds the allowed value, reconnect to another node.
        '''
        logger.info("Starting recovery mode")
        self.default_mode_started = False

        while True:
            try:
                self.failure_reqs_count[self.service_params.substrate.url] += 1
                if self.failure_reqs_count[self.service_params.substrate.url] > self.service_params.max_num_of_failure_reqs:
                    self.undesirable_urls.add(self.service_params.substrate.url)
                    self.service_params.substrate = SubstrateInterfaceUtils.create_interface(
                        urls=self.service_params.ws_urls_relay,
                        ss58_format=self.service_params.ss58_format,
                        type_registry_preset=self.service_params.type_registry_preset,
                        timeout=self.service_params.timeout,
                        undesirable_urls=self.undesirable_urls,
                    )
                if self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] > self.service_params.max_num_of_failure_reqs:
                    self.undesirable_urls.add(self.service_params.w3.provider.endpoint_uri)
                    self.service_params.w3 = create_provider(
                        urls=self.service_params.ws_urls_para,
                        timeout=self.service_params.timeout,
                        undesirable_urls=self.undesirable_urls,
                    )
                self.failure_reqs_count[self.service_params.substrate.url] -= 1
                break

            except (
                BadFunctionCallOutput,
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
                if self.service_params.substrate.url not in self.failure_reqs_count:
                    self.failure_reqs_count[self.service_params.substrate.url] = 0
                else:
                    self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0

        logger.info("Recovery mode is completed")

    def _get_stake_accounts(self) -> list:
        return self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
               ).functions.getStakeAccounts(self.priv_key.address).call()

    def _start_era_monitoring(self):
        self.service_params.substrate.query(
            module='Staking',
            storage_function='ActiveEra',
            subscription_handler=self._handle_era_change,
        )

    def _handle_era_change(self, era, update_nr: int, subscription_id: str):
        '''
        Read the staking parameters from the block where the era value is changed,
        generate the transaction body, sign and send to the parachain.
        '''
        logger.info(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")

        self.failure_reqs_count[self.service_params.substrate.url] += 1
        # TODO extract stake accounts list correct
        stake_accounts = self._get_stake_accounts()
        stash_accounts = stake_accounts['stashAccounts']
        eras = stake_accounts['eraId']

        for idx, stash_acc in enumerate(stash_accounts):
            for era in range(eras[idx], era.value['index'] + 1):
                logger.debug(f"Make report for era: {era}; stash: {stash_acc}")

                # NOTE what eras should I skip?
                if era.value['index'] < self.last_era_reported:
                    logger.info("CEI less than ORED: waiting for the next era")
                    return

                block_hash = self._find_start_block(era)
                if block_hash is None:
                    logger.error("Can't find the required block")
                    raise BlockNotFound
                logger.info(f"Block hash: {block_hash}")

                parachain_balance = SubstrateInterfaceUtils.get_parachain_balance(
                    self.service_params.substrate,
                    self.service_params.para_id,
                    block_hash,
                )
                # TODO update staking_parameters extraction
                staking_parameters = self._read_staking_parameters(block_hash)
                if not staking_parameters:
                    logger.warning("No staking parameters found")
                    return

                self.failure_reqs_count[self.service_params.substrate.url] -= 1
                logger.debug(';'.join([
                    f"stash: {stash_acc}",
                    f"era: {era}",
                    f"parachain_balance: {parachain_balance}",
                    f"staking parameters: {staking_parameters}",
                    f"Relay chain failure requests counter: {self.failure_reqs_count[self.service_params.substrate.url]}",
                    f"Parachain failure requests counter: {self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri]}",
                ]))

                tx = self._create_tx(era, parachain_balance, staking_parameters)
                self._sign_and_send_to_para(tx)
                logger.info("Waiting for the next era")

    def _find_start_block(self, era_id: int) -> str:
        """Find the hash of the block at which the era change occurs"""
        block_number = era_id * self.service_params.era_duration + self.service_params.initial_block_number

        try:
            return self.service_params.substrate.get_block_hash(block_number)
        except SubstrateRequestException:
            return None

    def _read_staking_parameters(self, stash: str, block_hash: str = None) -> dict:
        """Read staking parameters from specific block or from the head"""
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        stash_balance = self._get_stash_balance(stash)
        # TODO change the returned value and take care of sitation
        # if no controller value was found
        staking_ledger_result = self._get_ledger_data(block_hash, stash)
        stake_status = self._get_stake_status(stash, block_hash)


        for controller, controller_info in staking_ledger_result.items():
            unlocking_values = [{'balance': elem['value'], 'era': elem['era']} for elem in controller_info.value['unlocking']]

            staking_parameters = {
                'stash': '0x' + ss58_decode(controller_info.value['stash']),
                'controller': '0x' + ss58_decode(controller),
                'stakeStatus': stake_status,
                'activeBalance': controller_info.value['active'],
                'totalBalance': controller_info.value['total'],
                'unlocking': unlocking_values,
                'claimedRewards': controller_info.value['claimedRewards'],
                'stashBalance': stash_balance,
            })

        return staking_parameters

    def _get_ledger_data(self, block_hash: str, stash: str) -> dict:
        """Get ledger data using stash account address"""
        ledger_data = {}

        controller = self.service_params.substrate.query(
            module='Staking',
            storage_function='Bonded',
            params=[stash],
            block_hash=block_hash,
        )

        # TODO what if not found?
        if controller.value is None:
            continue

        staking_ledger = self.service_params.substrate.query(
            module='Staking',
            storage_function='Ledger',
            params=[controller.value],
            block_hash=block_hash,
        )

        ledger_data[controller.value] = staking_ledger

        return ledger_data

    def _add_not_founded_stashes(self, staking_parameters: list, stash_balances: dict) -> list:
        """Add information about not founded stash accounts to the report"""
        staking_params_set = set(staking_parameters)
        for stash in self.service_params.stash_accounts:
            if stash in staking_params_set:
                continue

            staking_parameters.append({
                'stash': stash,
                'controller': '',
                'stakeStatus': 0,
                'activeBalance': 0,
                'totalBalance': 0,
                'unlocking': [],
                'claimedRewards': [],
                'stashBalance': stash_balances[stash],
            })

        return staking_parameters

    def _get_stash_balance(self, stash: str) -> dict:
        """Get stash accounts free balances"""
        account_info = self.service_params.substrate.query(
            module='System',
            storage_function='Account',
            params=[stash]
        )

        return account_info.value['data']['free']

    def _get_stake_status(self, stash: str, block_hash: str = None) -> int:
        '''
        Get stash account status.
        0 - Chill, 1 - Nominator, 2 - Validator
        '''
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        session_validators_result = self.service_params.substrate.query(
            module='Session',
            storage_function='Validators',
            block_hash=block_hash,
        )

        staking_nominators_result = self.service_params.substrate.query_map(
            module='Staking',
            storage_function='Nominators',
            block_hash=block_hash,
        )

        nominators = set(nominator.value for nominator, _ in nominators_)
        validators = set(validator for validator in validators_.value)

        if stash_account in nominators:
            return 1

        elif stash_account in validators:
            return 2

        return 0 

    def _create_tx(self, era_id: int, parachain_balance: int, staking_parameters: dict) -> dict:
        """Create a transaction body using the staking parameters, era id and parachain balance"""
        nonce = self.service_params.w3.eth.getTransactionCount(self.account.address)

        return self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
               ).functions.reportRelay(
                era_id,
                {'parachainBalance': parachain_balance, 'stakeLedger': staking_parameters},
               ).buildTransaction({'gas': self.service_params.gas_limit, 'nonce': nonce})

    def _sign_and_send_to_para(self, tx: dict):
        """Sign transaction and send to parachain"""
        tx_signed = self.service_params.w3.eth.account.signTransaction(tx, private_key=self.priv_key)
        self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] += 1
        tx_hash = self.service_params.w3.eth.sendRawTransaction(tx_signed.rawTransaction)
        tx_receipt = self.service_params.w3.eth.waitForTransactionReceipt(tx_hash)

        if tx_receipt.status == 1:
            logger.debug(f"tx_hash: {tx_hash.hex()}")
            logger.info("The report was sent successfully. Resetting failure requests counter")
            self.failure_reqs_count[self.service_params.w3.provider.endpoint_uri] = 0
            if self.service_params.w3.provider.endpoint_uri in self.undesirable_urls:
                self.undesirable_urls.remove(self.service_params.w3.provider.endpoint_uri)
        else:
            logger.warning("Failed to send transaction")
            logger.debug(f"tx_receipt: {tx_receipt}")
