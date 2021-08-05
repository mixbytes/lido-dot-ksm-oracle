from dataclasses import dataclass
from service_parameters import ServiceParameters
from substrateinterface.exceptions import SubstrateRequestException
from substrateinterface.utils.ss58 import ss58_decode
from utils import change_node, get_active_era, get_parachain_balance
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
    failure_requests_counter: int = 0
    previous_era: int = 0

    def start_default_mode(self):
        """Start of the Oracle default mode"""
        if not self.default_mode_started:
            self.default_mode_started = True
        else:
            logging.warning('The default oracle mode is already working')

        logger.info('Starting default mode')
        self.account = self.service_params.w3.eth.account.from_key(self.priv_key)
        self._start_era_monitoring()

    def start_recovery_mode(self):
        '''
        Start of the Oracle recovery mode.
        The current era id (CEI) from relay chain and oracle report era id (ORED)
        from parachain are being compared. If CEI = ORED, then do not send a report.
        If failure requests counter exceeds the allowed value, reconnect to another
        node.
        '''
        logger.info('Starting recovery mode')
        self.default_mode_started = False

        if self.failure_requests_counter > self.service_params.max_number_of_failure_requests:
            self.service_params.substrate = change_node(
                urls=self.service_params.ws_urls_relay,
                ss58_format=self.service_params.ss58_format,
                type_registry_preset=self.service_params.type_registry_preset,
                timeout=self.service_params.timeout,
                undesirable_url=self.service_params.substrate.url,
            )

        while True:
            try:
                current_era = get_active_era(self.service_params.substrate)
                oracle_report_era = self._get_oracle_report_era()
                break
            except (
                ConnectionRefusedError,
                WebSocketConnectionClosedException,
            ) as e:
                logging.warning(f"Error: {e}")
                self.service_params.substrate = change_node(
                    urls=self.service_params.ws_urls_relay,
                    ss58_format=self.service_params.ss58_format,
                    type_registry_preset=self.service_params.type_registry_preset,
                    timeout=self.service_params.timeout,
                    undesirable_url=self.service_params.substrate.url,
                )

        if oracle_report_era.value['index'] == current_era.value['index']:
            self.previous_era = current_era
        else:
            self.previous_era = oracle_report_era

        logger.info('Recovery mode is completed')
        self.start_default_mode()

    # TODO get last reported era from smart contract
    def _get_oracle_report_era(self):
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
        if era.value['index'] == self.previous_era:
            return
        else:
            self.previous_era = era.value['index']

        logger.info(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")
        block_hash = self._find_start_block(era.value['index'])
        if block_hash is None:
            logging.warning("Can't find the required block")
            return
        logger.info(f"Block hash: {block_hash}")

        parachain_balance = get_parachain_balance(
            self.service_params.substrate,
            self.service_params.para_id,
            block_hash,
        )
        staking_parameters = self._read_staking_parameters(block_hash)
        if not staking_parameters:
            logging.warning('No staking parameters found')
            return

        logger.info(f"parachain_balance: {parachain_balance}")
        logger.info(f"staking parameters: {staking_parameters}")

        tx = self._create_tx(era.value['index'], parachain_balance, staking_parameters)
        self._sign_and_send_to_para(tx)

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
            # filter out validators and leave only nominators
            if stash_statuses[controller_info.value['stash']] != 1:
                continue

            unlocking_values = []
            for elem in controller_info.value['unlocking']:
                unlocking_values.append({'balance': elem['value'], 'era': elem['era']})

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
        nominators = set()
        validators = set()

        for nominator, _ in nominators_:
            nominators.add(nominator.value)
        for validator in validators_.value:
            validators.add(validator)

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
               ).buildTransaction({'gas': self.service_params.gas, 'nonce': nonce})

    def _sign_and_send_to_para(self, tx):
        """Sign transaction and send to parachain"""
        tx_signed = self.service_params.w3.eth.account.signTransaction(tx, private_key=self.priv_key)
        self.failure_requests_counter += 1
        tx_hash = self.service_params.w3.eth.sendRawTransaction(tx_signed.rawTransaction)
        tx_receipt = self.service_params.w3.eth.waitForTransactionReceipt(tx_hash)

        logger.info(f"tx_hash: {tx_hash.hex()}")
        logger.info(f"tx_receipt: {tx_receipt}")
        self.failure_requests_counter = 0
