from dataclasses import dataclass, field
from service_parameters import ServiceParameters
from substrateinterface.exceptions import SubstrateRequestException
from substrateinterface.utils.ss58 import ss58_decode
from substrate_interface_utils import SubstrateInterfaceUtils
from websocket._exceptions import WebSocketConnectionClosedException

import logging


logger = logging.getLogger(__name__)


@dataclass
class Oracle:
    """A class that contains all the logic of the oracle's work"""
    priv_key: str
    service_params: ServiceParameters

    account = None
    default_mode_started: bool = False
    failure_requests_count: dict = field(default_factory=dict)
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
        self.failure_requests_count[self.service_params.substrate.url] = 0
        self._start_era_monitoring()

    def start_recovery_mode(self):
        '''
        Start of the Oracle recovery mode.
        The current era id (CEI) from relay chain and oracle report era id (ORED)
        from parachain are being compared. If CEI equals ORED, then do not send a report.
        If failure requests counter exceeds the allowed value, reconnect to another
        node.
        '''
        logger.info("Starting recovery mode")
        self.default_mode_started = False

        if self.failure_requests_count[self.service_params.substrate.url] > self.service_params.max_number_of_failure_requests:
            self.undesirable_urls.add(self.service_params.substrate.url)
            self.service_params.substrate = SubstrateInterfaceUtils.create_interface(
                urls=self.service_params.ws_urls_relay,
                ss58_format=self.service_params.ss58_format,
                type_registry_preset=self.service_params.type_registry_preset,
                timeout=self.service_params.timeout,
                undesirable_urls=self.undesirable_urls,
            )

        while True:
            try:
                current_era = SubstrateInterfaceUtils.get_active_era(self.service_params.substrate)
                self.last_era_reported = self._get_oracle_report_era()
                break
            except (
                ConnectionRefusedError,
                WebSocketConnectionClosedException,
            ) as e:
                logger.warning(f"Error: {e}")
                self.service_params.substrate = SubstrateInterfaceUtils.create_interface(
                    urls=self.service_params.ws_urls_relay,
                    ss58_format=self.service_params.ss58_format,
                    type_registry_preset=self.service_params.type_registry_preset,
                    timeout=self.service_params.timeout,
                    undesirable_urls=self.undesirable_urls,
                )

        if self.last_era_reported > current_era.value['index']:
            logger.info("CEI less than ORED: waiting for the next era")
        else:
            logger.info("CEI equals or greater than ORED: create report for the current era")

        logger.info("Recovery mode is completed")

    def _get_oracle_report_era(self):
        # TODO update SC function signature
        '''
        return self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
               ).functions.ORED().call()
        '''
        # TODO remove
        return 0

    def _start_era_monitoring(self):
        self.service_params.substrate.query(
            module='Staking',
            storage_function='ActiveEra',
            subscription_handler=self._handle_era_change,
        )

    def _handle_era_change(self, era, update_nr, subscription_id):
        '''
        Read the staking parameters from the block where the era value is changed,
        generate the transaction body, sign and send to the parachain.
        '''
        if self.last_era_reported == -1:
            try:
                self.last_era_reported = self._get_oracle_report_era()
            except (
                ConnectionRefusedError,
                WebSocketConnectionClosedException,
            ) as e:
                logger.warning(f"Error: {e}")
                raise e

        if era.value['index'] < self.last_era_reported:
            logger.info("CEI less than ORED: waiting for the next era")
            return

        logger.info(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")
        block_hash = self._find_start_block(era.value['index'])
        if block_hash is None:
            logger.error("Can't find the required block")
            return
        logger.info(f"Block hash: {block_hash}")

        parachain_balance = SubstrateInterfaceUtils.get_parachain_balance(
            self.service_params.substrate,
            self.service_params.para_id,
            block_hash,
        )
        staking_parameters = self._read_staking_parameters(block_hash)
        if not staking_parameters:
            logger.warning("No staking parameters found")
            return

        logger.debug(';'.join([
            f"era: {era.value['index'] - 1}",
            f"parachain_balance: {parachain_balance}",
            f"staking parameters: {staking_parameters}",
            f"failure requests counter: {self.failure_requests_count[self.service_params.substrate.url]}",
        ]))

        tx = self._create_tx(era.value['index'], parachain_balance, staking_parameters)
        self._sign_and_send_to_para(tx)
        logger.info("Waiting for the next era")

    def _find_start_block(self, era_id):
        """Find the hash of the block at which the era change occurs"""
        block_number = era_id * self.service_params.era_duration + self.service_params.initial_block_number

        try:
            return self.service_params.substrate.get_block_hash(block_number)
        except SubstrateRequestException:
            return None

    def _read_staking_parameters(self, block_hash=None):
        """Read staking parameters from specific block or from the head"""
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        staking_ledger_result = self._get_ledger_data(block_hash)

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

        stash_balances = self._get_stash_balances()

        stash_statuses = self._get_stash_statuses(
            staking_ledger_result,
            session_validators_result,
            staking_nominators_result,
        )

        staking_parameters = []

        for controller, controller_info in staking_ledger_result.items():
            unlocking_values = [{'balance': elem['value'], 'era': elem['era']} for elem in controller_info.value['unlocking']]

            stash_addr = '0x' + ss58_decode(controller_info.value['stash'])
            controller_addr = '0x' + ss58_decode(controller)

            staking_parameters.append({
                'stash': stash_addr,
                'controller': controller_addr,
                'stake_status': stash_statuses[controller_info.value['stash']],
                'active_balance': controller_info.value['active'],
                'total_balance': controller_info.value['total'],
                'unlocking': unlocking_values,
                'claimed_rewards': controller_info.value['claimedRewards'],
                'stash_balance': stash_balances[stash_addr],
            })

        staking_parameters.sort(key=lambda e: e['stash'])
        return staking_parameters

    def _get_ledger_data(self, block_hash):
        """Get ledger data using stash accounts list"""
        ledger_data = {}

        for stash in self.service_params.stash_accounts:
            controller = self.service_params.substrate.query(
                module='Staking',
                storage_function='Bonded',
                params=[stash],
                block_hash=block_hash,
            )

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

    def _get_stash_balances(self):
        """Get stash accounts free balances"""
        balances = {}

        for stash in self.service_params.stash_accounts:
            result = self.service_params.substrate.query(
                module='System',
                storage_function='Account',
                params=[stash]
            )

            balances[stash] = result.value['data']['free']

        return balances

    def _get_stash_statuses(self, controllers_, validators_, nominators_):
        '''
        Get stash accounts statuses.
        0 - Chill, 1 - Nominator, 2 - Validator
        '''
        statuses = {}
        nominators = set(nominator.value for nominator, _ in nominators_)
        validators = set(validator for validator in validators_.value)

        for controller_info in controllers_.values():
            stash_account = controller_info.value['stash']
            if stash_account in nominators:
                statuses[stash_account] = 1
                continue

            if stash_account in validators:
                statuses[stash_account] = 2
                continue

            statuses[stash_account] = 0

        return statuses

    def _create_tx(self, era_id, parachain_balance, staking_parameters):
        """Create a transaction body using the staking parameters, era id and parachain balance"""
        nonce = self.service_params.w3.eth.getTransactionCount(self.account.address)

        return self.service_params.w3.eth.contract(
                address=self.service_params.contract_address,
                abi=self.service_params.abi
               ).functions.reportRelay(
                era_id,
                {'parachain_balance': parachain_balance, 'stake_ledger': staking_parameters},
               ).buildTransaction({'gas': self.service_params.gas_limit, 'nonce': nonce})

    def _sign_and_send_to_para(self, tx):
        """Sign transaction and send to parachain"""
        tx_signed = self.service_params.w3.eth.account.signTransaction(tx, private_key=self.priv_key)
        self.failure_requests_count[self.service_params.substrate.url] += 1
        tx_hash = self.service_params.w3.eth.sendRawTransaction(tx_signed.rawTransaction)
        tx_receipt = self.service_params.w3.eth.waitForTransactionReceipt(tx_hash)

        if tx_receipt.status == 1:
            logger.debug(f"tx_hash: {tx_hash.hex()}")
            logger.info("The report was sent successfully. Resetting failure requests counter")
            self.failure_requests_count[self.service_params.substrate.url] = 0
            if self.service_params.substrate.url in self.undesirable_urls:
                self.undesirable_urls.remove(self.service_params.substrate.url)
        else:
            logger.warning("Failed to send transaction")
            logger.debug(f"tx_receipt: {tx_receipt}")
