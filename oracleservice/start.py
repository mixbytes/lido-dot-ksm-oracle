#!/usr/bin/env python3
from substrateinterface import SubstrateInterface
from web3 import Web3

import json
import os
import re
import sys


DEFAULT_GAS_LIMIT = 10000000
ss58_formats = (0, 2, 42)


def get_abi(abi_path):
    with open(abi_path, 'r') as f:
        a = f.read()
        
    abi = json.loads(a)

    return abi


def decode_stash_addresses(app, accounts):
    decoded_accounts = []

    for acc in accounts:
        if not acc.startswith('0x'):
            decoded_accounts.append('0x' + app.ss58_decode(acc))
        else:
            decoded_accounts.append(app.ss58_decode(acc))
    

    return decoded_accounts


def create_interface(url, ss58_format, type_registry_preset):
    substrate = None

    if ss58_format not in ss58_formats:
        print(f"Invalid SS58 format")

        return substrate

    for u in url:
        if not u.startswith('ws'):
            print(f"Unsupported ws provider: {u}")
            continue

        try:
            substrate = SubstrateInterface(
                url=u, 
                ss58_format=ss58_format, 
                type_registry_preset=type_registry_preset,
            )

            substrate.update_type_registry_presets()

        except:
            print(f"Failed to connect to {u} with type registry preset '{type_registry_preset}'")
        else:
            break

    return substrate


def create_provider(url):
    provider = None

    for u in url:
        if not u.startswith('ws'):
            print(f"Unsupported ws provider: {u}")
            continue

        try:
            provider = Web3.WebsocketProvider(u)
        except:
            print(f"Failed to connect to {u}")
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
         ).buildTransaction({'gas': gas, 'gasPrice': gas_price, 'nonce': nonce})

    return tx


def sign_and_send_to_para(tx):
    tx_signed = w3.eth.account.signTransaction(tx, private_key=oracle_private_key)
    tx_hash = w3.eth.sendRawTransaction(tx_signed.rawTransaction)
    tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

    print(f"tx_hash: {tx_hash}")
    print(f"tx_receipt: {tx_receipt}")


def get_parachain_balance(app, block_hash=None):
    if not block_hash:
        block_hash = app.get_chain_head()

    para_id = app.query(
        module='Paras',
        storage_function='Parachains',
        block_hash=block_hash
    ).value[0]

    prefix = b'para'
    para_addr = bytearray(prefix)
    para_addr.append(para_id & 0xFF)
    para_id = para_id>>8
    para_addr.append(para_id & 0xFF)
    para_id = para_id>>8
    para_addr.append(para_id & 0xFF)
    
    para_addr = app.ss58_encode(para_addr.ljust(32, b'\0')) 

    result = app.query(
        module='System',
        storage_function='Account',
        params=[para_addr]
    )

    if result is None:
        print(f"{para} is gone")
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
        
        stash_addr = '0x' + app.ss58_decode(controller_info.value['stash'])
        controller_addr = '0x' + app.ss58_decode(controller)

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
    print(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")
    block_hash = find_start_block(substrate, era.value['index'])
    print(f"Block hash: {block_hash}")

    parachain_balance = get_parachain_balance(substrate, block_hash)
    staking_parameters = read_staking_parameters(substrate, block_hash)
    if not staking_parameters:
        print('No staking parameters found')
        return

    print(f"parachain_balance: {parachain_balance}")
    print(f"staking parameters: {staking_parameters}")

    tx = create_tx(era.value['index'], parachain_balance, staking_parameters)
    sign_and_send_to_para(tx)


def start_era_monitoring(app):
    app.query(
        module='Staking',
        storage_function='ActiveEra',
        subscription_handler=subscription_handler,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='oracle-service command line.')
    parser.add_argument('--ws_url_relay', help='websocket url', nargs='*', default=['ws://localhost:9951/'])
    parser.add_argument('--ws_url_para', help='websocket url', nargs='*', default=['ws://localhost:10055/'])
    parser.add_argument('--ss58_format', help='ss58 format', type=int, default=2)
    parser.add_argument('--type_registry_preset', help='type registry preset', type=str, default='kusama')
    parser.add_argument('--gas_price', help='gas price', type=int, default=1000000000)
    parser.add_argument('--abi', help='path to abi', type=str, default='oracleservice/abi.json')
    parser.add_argument('--stash', help='stash account list', nargs='+')

    args = parser.parse_args()
    ws_url_relay = args.ws_url_relay
    ws_url_para = args.ws_url_para
    ss58_format = args.ss58_format
    type_registry_preset = args.type_registry_preset
    gas_price = args.gas_price

    abi_path = args.abi
    abi = get_abi(abi_path)

    contract_address = os.getenv('CONTRACT_ADDRESS', None)
    if contract_address is None:
        sys.exit('No contract address provided')
    gas = int(os.getenv('GAS_LIMIT', DEFAULT_GAS_LIMIT))

    w3 = Web3(create_provider(ws_url_para))
    if not w3.isConnected():
        sys.exit('Failed to create web3 provider')

    substrate = create_interface(ws_url_relay, ss58_format, type_registry_preset)
    if substrate is None:
        sys.exit('Failed to create substrate-interface')

    stash_accounts = decode_stash_addresses(substrate, args.stash)

    oracle_private_key = os.getenv('ORACLE_PRIVATE_KEY')
    if oracle_private_key is None:
        sys.exit('Failed to parse oracle private key')
    account = w3.eth.account.from_key(oracle_private_key)
    
    start_era_monitoring(substrate)

