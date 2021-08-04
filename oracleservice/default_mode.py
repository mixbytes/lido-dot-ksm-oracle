from datetime import datetime
from functools import partial
from substrateinterface.exceptions import SubstrateRequestException
from substrateinterface.utils.ss58 import ss58_decode
from utils import get_active_era, get_parachain_balance

import logging


logger = logging.getLogger(__name__)

previous_era = 0
requests_counter = 0


def create_tx(w3, account, abi, contract_address, gas, era_id, parachain_balance, staking_parameters):
    """Create a transaction body using the staking parameters, era id and parachain balance"""
    nonce = w3.eth.getTransactionCount(account.address)

    return w3.eth.contract(
            address=contract_address,
            abi=abi
           ).functions.reportRelay(
            era_id,
            {'parachain_balance': parachain_balance, 'stake_ledger': staking_parameters},
           ).buildTransaction({'gas': gas, 'nonce': nonce})


def sign_and_send_to_para(w3, priv_key, tx):
    """Sign transaction and send to parachain"""
    global requests_counter

    tx_signed = w3.eth.account.signTransaction(tx, private_key=priv_key)
    requests_counter += 1
    tx_hash = w3.eth.sendRawTransaction(tx_signed.rawTransaction)
    tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

    logger.info(f"tx_hash: {tx_hash.hex()}")
    logger.info(f"tx_receipt: {tx_receipt}")


def find_start_block(substrate, era_id, era_duration, initial_block_number):
    """Find the hash of the block at which the era change occurs"""
    block_number = era_id * era_duration + initial_block_number

    try:
        return substrate.get_block_hash(block_number)
    except SubstrateRequestException:
        return None


def get_stash_statuses(controllers_, validators_, nominators_):
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


def get_stash_balances(substrate, stash_accounts):
    """Get stash accounts free balances"""
    balances = {}

    for stash in stash_accounts:
        result = substrate.query(
            module='System',
            storage_function='Account',
            params=[stash]
        )

        balances[stash] = result.value['data']['free']

    return balances


def get_ledger_data(substrate, block_hash, stash_accounts):
    """Get ledger data using stash accounts list"""
    ledger_data = {}

    for stash in stash_accounts:
        controller = substrate.query(
            module='Staking',
            storage_function='Bonded',
            params=[stash],
            block_hash=block_hash,
        )

        if controller.value is None:
            continue

        staking_ledger = substrate.query(
            module='Staking',
            storage_function='Ledger',
            params=[controller.value],
            block_hash=block_hash,
        )

        ledger_data[controller.value] = staking_ledger

    return ledger_data


def read_staking_parameters(substrate, stash_accounts, block_hash=None):
    """Read staking parameters from specific block or from the head"""
    if block_hash is None:
        block_hash = substrate.get_chain_head()

    staking_ledger_result = get_ledger_data(substrate, block_hash, stash_accounts)

    session_validators_result = substrate.query(
        module='Session',
        storage_function='Validators',
        block_hash=block_hash,
    )

    staking_nominators_result = substrate.query_map(
        module='Staking',
        storage_function='Nominators',
        block_hash=block_hash,
    )

    stash_balances = get_stash_balances(substrate, stash_accounts)

    stash_statuses = get_stash_statuses(
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


def subscription_handler(
        era, update_nr, subscription_id,
        w3, substrate, account, para_id,
        priv_key, abi, contract_address,
        stash_accounts, gas, wal_manager,
        era_duration, initial_block_number,
    ):
    '''
    Read the staking parameters from the block where the era value is changed,
    generate the transaction body, sign and send to the parachain.
    '''
    global previous_era
    global requests_counter

    if era.value['index'] == previous_era:
        return
    else:
        requests_counter = 0
        previous_era = era.value['index']

    logger.info(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")
    block_hash = find_start_block(substrate, era.value['index'], era_duration, initial_block_number)
    if block_hash is None:
        logging.warning("Can't find the required block")
        return
    logger.info(f"Block hash: {block_hash}")

    wal_manager.write(
        f"date={datetime.now()}" + '\n' +
        f"era={era.value['index']}" + '\n' +
        f"block={block_hash}" + '\n'
    )
    parachain_balance = get_parachain_balance(substrate, para_id, block_hash)
    staking_parameters = read_staking_parameters(substrate, stash_accounts, block_hash=block_hash)
    if not staking_parameters:
        logging.warning('No staking parameters found')
        return

    logger.info(f"parachain_balance: {parachain_balance}")
    logger.info(f"staking parameters: {staking_parameters}")

    tx = create_tx(
        w3, account, abi, contract_address, gas,
        era.value['index'], parachain_balance, staking_parameters, 
    )
    sign_and_send_to_para(w3=w3, priv_key=priv_key, tx=tx)
    wal_manager.write(
        f"requests_counter={requests_counter}" + '\n' +
        '---' + '\n'
    )


def start_era_monitoring(
        w3, substrate, account, para_id,
        priv_key, abi, contract_address,
        gas, stash_accounts, wal_manager,
        era_duration, initial_block_number,
    ):
    """Monitoring the moment of the era change"""
    substrate.query(
        module='Staking',
        storage_function='ActiveEra',
        subscription_handler=partial(
            subscription_handler, 
            account=account,
            abi=abi,
            contract_address=contract_address,
            era_duration=era_duration,
            gas=gas,
            initial_block_number=initial_block_number,
            para_id=para_id,
            priv_key=priv_key,
            stash_accounts=stash_accounts,
            substrate=substrate,
            wal_manager=wal_manager,
            w3=w3,
        )
    )


def default_mode(
        oracle_priv_key, w3, substrate, wal_manager, 
        para_id: int, stash_accounts: list, 
        abi, contract_address: str, gas: int,
        era_duration: int, initial_block_number: int,
    ):
    """Start of the Oracle default mode"""
    account = w3.eth.account.from_key(oracle_priv_key)

    logger.info('Starting default mode')
    start_era_monitoring(
        w3=w3, substrate=substrate, account=account, para_id=para_id,
        priv_key=oracle_priv_key, abi=abi, contract_address=contract_address,
        gas=gas, stash_accounts=stash_accounts, wal_manager=wal_manager,
        era_duration=era_duration, initial_block_number=initial_block_number,
    )
