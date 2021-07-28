#!/usr/bin/env python3
from log import init_log
from substrateinterface import SubstrateInterface
from substrateinterface.utils.ss58 import ss58_decode
from web3 import Web3
from websocket._exceptions import WebSocketAddressException

import json
import logging
import os
import sys


DEFAULT_GAS_LIMIT = 10000000
SS58_FORMATS = (0, 2, 42)
previous_era = 0

init_log(stdout_level=os.getenv('LOG_LEVEL_STDOUT', 'INFO'))
logger = logging.getLogger()


def get_abi(abi_path):
    with open(abi_path, 'r') as f:
        return json.load(f)


def decode_stash_addresses(accounts):
    if not accounts:
        return None

    decoded_accounts = []

    for acc in accounts:
        if not acc.startswith('0x'):
            decoded_accounts.append('0x' + ss58_decode(acc))
        else:
            decoded_accounts.append(ss58_decode(acc))

    return decoded_accounts


def create_interface(url, ss58_format, type_registry_preset):
    substrate = None

    if ss58_format not in SS58_FORMATS:
        logging.error("Invalid SS58 format")

        return substrate

    for u in url:
        if not u.startswith('ws'):
            logging.warning(f"Unsupported ws provider: {u}")
            continue

        try:
            substrate = SubstrateInterface(
                url=u,
                ss58_format=ss58_format,
                type_registry_preset=type_registry_preset,
            )

            substrate.update_type_registry_presets()

        except ValueError:
            logging.warning(f"Failed to connect to {u} with type registry preset '{type_registry_preset}'")
        else:
            break

    return substrate


def create_provider(url):
    provider = None

    for u in url:
        if not u.startswith('ws'):
            logging.warning(f"Unsupported ws provider: {u}")
            continue

        try:
            provider = Web3.WebsocketProvider(u)
        except WebSocketAddressException:
            logging.warning(f"Failed to connect to {u}")
        else:
            break

    return provider


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


def get_parachain_balance(app, block_hash=None):
    global para_id

    if not block_hash:
        block_hash = app.get_chain_head()

    prefix = b'para'
    para_addr = bytearray(prefix)
    para_addr.append(para_id & 0xFF)
    para_id = para_id >> 8
    para_addr.append(para_id & 0xFF)
    para_id = para_id >> 8
    para_addr.append(para_id & 0xFF)

    para_addr = app.ss58_encode(para_addr.ljust(32, b'\0'))

    result = app.query(
        module='System',
        storage_function='Account',
        params=[para_addr]
    )

    if result is None:
        logging.warning(f"{para_id} is gone")
        return 0

    return result.value['data']['free']


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


def get_stash_balances(app, stash_accounts):
    balances = {}

    for stash in stash_accounts:
        result = app.query(
            module='System',
            storage_function='Account',
            params=[stash]
        )

        balances[stash] = result.value['data']['free']

    return balances


def get_ledger_data(app, block_hash, stash_accounts):
    ledger_data = {}

    for stash in stash_accounts:
        controller = app.query(
            module='Staking',
            storage_function='Bonded',
            params=[stash],
            block_hash=block_hash,
        )

        if controller.value is None:
            continue

        staking_ledger = app.query(
            module='Staking',
            storage_function='Ledger',
            params=[controller.value],
            block_hash=block_hash,
        )

        ledger_data[controller.value] = staking_ledger

    return ledger_data


def read_staking_parameters(app, block_hash=None, max_results=199):
    if not block_hash:
        block_hash = app.get_chain_head()

    staking_ledger_result = get_ledger_data(app, block_hash, stash_accounts)

    session_validators_result = app.query(
        module='Session',
        storage_function='Validators',
        block_hash=block_hash,
    )

    staking_nominators_result = app.query_map(
        module='Staking',
        storage_function='Nominators',
        block_hash=block_hash,
    )

    stash_balances = get_stash_balances(app, stash_accounts)

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


def find_start_block(app, era_id):
    current_block_hash = substrate.get_chain_head()
    current_block_info = substrate.get_block_header(current_block_hash)
    previous_block_hash = current_block_info['header']['parentHash']
    previous_block_era = app.query(
        module='Staking',
        storage_function='ActiveEra',
        block_hash=previous_block_hash,
    )

    while previous_block_era.value['index'] >= era_id:
        current_block_hash = previous_block_hash
        current_block_info = substrate.get_block_header(current_block_hash)
        previous_block_hash = current_block_info['header']['parentHash']
        previous_block_era = app.query(
            module='Staking',
            storage_function='ActiveEra',
            block_hash=previous_block_hash,
        )

    return current_block_hash


def subscription_handler(era, update_nr, subscription_id):
    global previous_era
    if era.value['index'] == previous_era:
        return
    else:
        previous_era = era.value['index']

    logger.info(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")
    block_hash = find_start_block(substrate, era.value['index'])
    logger.info(f"Block hash: {block_hash}")

    parachain_balance = get_parachain_balance(substrate, block_hash)
    staking_parameters = read_staking_parameters(substrate, block_hash)
    if not staking_parameters:
        logging.warning('No staking parameters found')
        return

    logger.info(f"parachain_balance: {parachain_balance}")
    logger.info(f"staking parameters: {staking_parameters}")

    tx = create_tx(era.value['index'], parachain_balance, staking_parameters)
    sign_and_send_to_para(tx)


def start_era_monitoring(app):
    app.query(
        module='Staking',
        storage_function='ActiveEra',
        subscription_handler=subscription_handler,
    )


if __name__ == "__main__":
    ws_url_relay = os.getenv('WS_URL_RELAY', 'ws://localhost:9951/').split(',')
    ws_url_para = os.getenv('WS_URL_PARA', 'ws://localhost:10055/').split(',')
    ss58_format = int(os.getenv('SS58_FORMAT', 2))
    type_registry_preset = os.getenv('TYPE_REGISTRY_PRESET', 'kusama')
    para_id = int(os.getenv('PARA_ID'))

    contract_address = os.getenv('CONTRACT_ADDRESS', None)
    if contract_address is None:
        sys.exit('No contract address provided')

    abi_path = os.getenv('ABI_PATH', 'oracleservice/abi.json')
    abi = get_abi(abi_path)

    gas = int(os.getenv('GAS_LIMIT', DEFAULT_GAS_LIMIT))

    w3 = Web3(create_provider(ws_url_para))
    if not w3.isConnected():
        sys.exit('Failed to create web3 provider')

    substrate = create_interface(ws_url_relay, ss58_format, type_registry_preset)
    if substrate is None:
        sys.exit('Failed to create substrate-interface')

    stash = os.getenv('STASH_ACCOUNTS').split(',')
    stash_accounts = decode_stash_addresses(stash)
    if stash_accounts is None:
        sys.exit('Failed to parse stash accounts list')

    oracle_private_key = os.getenv('ORACLE_PRIVATE_KEY')
    if oracle_private_key is None:
        sys.exit('Failed to parse oracle private key')
    account = w3.eth.account.from_key(oracle_private_key)

    start_era_monitoring(substrate)
