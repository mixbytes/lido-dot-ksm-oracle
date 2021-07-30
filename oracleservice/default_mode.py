from substrateinterface.utils.ss58 import ss58_decode
from utils import get_parachain_balance
import logging


logger = logging.getLogger()

abi = None
account = None
contract_address = ''
gas = 0
oracle_private_key = ''
para_id = 0
previous_era = 0
stash_accounts = []
substrate = None
w3 = None


def create_tx(era_id, parachain_balance, staking_parameters):
    nonce = w3.eth.getTransactionCount(account.address)

    tx = w3.eth.contract(
            address=contract_address,
            abi=abi
        ).functions.reportRelay(
            era_id,
            {'parachain_balance': parachain_balance, 'stake_ledger': staking_parameters},
        ).buildTransaction({'gas': gas, 'nonce': nonce})

    return tx


def sign_and_send_to_para(tx):
    tx_signed = w3.eth.account.signTransaction(tx, private_key=oracle_private_key)
    tx_hash = w3.eth.sendRawTransaction(tx_signed.rawTransaction)
    tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

    logger.info(f"tx_hash: {tx_hash.hex()}")
    logger.info(f"tx_receipt: {tx_receipt}")


def find_start_block(substrate, era_id):
    current_block_hash = substrate.get_chain_head()
    current_block_info = substrate.get_block_header(current_block_hash)
    previous_block_hash = current_block_info['header']['parentHash']
    previous_block_era = substrate.query(
        module='Staking',
        storage_function='ActiveEra',
        block_hash=previous_block_hash,
    )

    while previous_block_era.value['index'] >= era_id:
        current_block_hash = previous_block_hash
        current_block_info = substrate.get_block_header(current_block_hash)
        previous_block_hash = current_block_info['header']['parentHash']
        previous_block_era = substrate.query(
            module='Staking',
            storage_function='ActiveEra',
            block_hash=previous_block_hash,
        )

    return current_block_hash


def get_stash_statuses(controllers_, validators_, nominators_):
    # 0 - Chill, 1 - Nominator, 2 - Validator
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


def read_staking_parameters(substrate, block_hash=None, max_results=199):
    if not block_hash:
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


def subscription_handler(era, update_nr, subscription_id):
    global previous_era
    global substrate

    if era.value['index'] == previous_era:
        return
    else:
        previous_era = era.value['index']

    logger.info(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")
    block_hash = find_start_block(substrate, era.value['index'])
    logger.info(f"Block hash: {block_hash}")

    parachain_balance = get_parachain_balance(substrate, para_id, block_hash)
    staking_parameters = read_staking_parameters(substrate, block_hash)
    if not staking_parameters:
        logging.warning('No staking parameters found')
        return

    logger.info(f"parachain_balance: {parachain_balance}")
    logger.info(f"staking parameters: {staking_parameters}")

    tx = create_tx(era.value['index'], parachain_balance, staking_parameters)
    sign_and_send_to_para(tx)


def start_era_monitoring(substrate):
    substrate.query(
        module='Staking',
        storage_function='ActiveEra',
        subscription_handler=subscription_handler
    )


def default_mode(_oracle_pk, _w3, _substrate, _para_id: int, _stash_acc: list, _abi, _contr_addr: str, _gas: int):
    global abi
    global account
    global contract_address
    global gas
    global oracle_private_key
    global para_id
    global stash_accounts
    global substrate
    global w3

    abi = _abi
    contract_address = _contr_addr
    gas = _gas
    oracle_private_key = _oracle_pk
    para_id = _para_id
    stash_accounts = _stash_acc
    substrate = _substrate
    w3 = _w3

    account = w3.eth.account.from_key(oracle_private_key)

    logger.info('Starting default mode')
    start_era_monitoring(substrate)
