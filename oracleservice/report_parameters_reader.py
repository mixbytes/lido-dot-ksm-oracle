from dataclasses import dataclass
from prometheus_metrics import metrics_exporter
from service_parameters import ServiceParameters
from substrateinterface import Keypair

import logging


logger = logging.getLogger(__name__)


@dataclass
class ReportParametersReader:
    """A class that contains all the logic of reading data for the Oracle report"""
    service_params: ServiceParameters

    def get_stash_staking_parameters(self, stash: Keypair, block_hash: str) -> dict:
        """Get staking parameters for specific stash from specific block or from the head"""
        logger.info(f"Reading staking parameters for stash {stash.ss58_address}")
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        with metrics_exporter.relay_exceptions_count.count_exceptions():
            stash_free_balance = self._get_stash_free_balance(stash, block_hash)
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
                'slashingSpans': 0,
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
            'slashingSpans': staking_ledger_result['slashingSpans_number'],
        }

    def _get_ledger_data(self, block_hash: str, stash: Keypair) -> dict:
        """Get ledger data using stash account address"""
        controller = self.service_params.substrate.query(
                module='Staking',
                storage_function='Bonded',
                params=[stash.ss58_address],
                block_hash=block_hash,
            )
        if controller.value is None:
            return None

        controller = Keypair(ss58_address=controller.value)

        ledger = self.service_params.substrate.query(
                module='Staking',
                storage_function='Bonded',
                params=[controller.ss58_address],
                block_hash=block_hash,
            )

        result = {'controller': controller, 'stash': stash}
        result.update(ledger.value)

        slashing_spans = self.service_params.substrate.query(
                module='Staking',
                storage_function='SlashingSpans',
                params=[controller.ss58_address],
                block_hash=block_hash,
            )

        if slashing_spans.value is None:
            result['slashingSpans_number'] = 0
        else:
            result['slashingSpans_number'] = len(slashing_spans.value['prior'])

        return result

    def _get_stash_free_balance(self, stash: Keypair, block_hash: str) -> int:
        """Get stash accounts free balances"""
        account_info = self.service_params.substrate.query(
                module='System',
                storage_function='Account',
                params=[stash.ss58_address],
                block_hash=block_hash,
            )
        metrics_exporter.total_stashes_free_balance.inc(account_info.value['data']['free'])

        return account_info.value['data']['free']

    def _get_stake_status(self, stash: Keypair, block_hash: str = None) -> int:
        """
        Get stash account status.
        0 - Idle, 1 - Nominator, 2 - Validator, 3 - None
        """
        if block_hash is None:
            block_hash = self.service_params.substrate.get_chain_head()

        staking_validators = self.service_params.substrate.query(
                module='Session',
                storage_function='Validators',
                block_hash=block_hash,
            )

        staking_nominators = self.service_params.substrate.query_map(
                module='Staking',
                storage_function='Nominators',
                block_hash=block_hash,
            )

        nominators = set(nominator.value for nominator, _ in staking_nominators)
        validators = set(validator for validator in staking_validators.value)

        if stash.ss58_address in nominators:
            return 1

        if stash.ss58_address in validators:
            return 2

        return 0
