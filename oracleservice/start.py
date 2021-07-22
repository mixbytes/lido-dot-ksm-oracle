#!/usr/bin/env python3
from substrateinterface import SubstrateInterface
from substrateinterface.utils.ss58 import ss58_decode
from web3 import Web3

def create_interface(url, ss58_format, type_registry_preset):
    substrate = SubstrateInterface(
            url=url, 
            ss58_format=ss58_format, 
            type_registry_preset=type_registry_preset,
    )

    substrate.update_type_registry_presets()

    return substrate


def create_tx(era_id, staking_parameters):
    nonce = w3.eth.getTransactionCount(account.address)
    print(staking_parameters)

    tx = w3.eth.contract(
            address=contract_address, 
            abi=abi
         ).functions.reportRelay(
            era_id, 
            # TODO modify staking_parameters - ?
            {'staking': [123, staking_parameters]},
         ).buildTransaction({'gas': gas, 'gasPrice': gas_price, 'nonce': nonce})

    return tx


def sign_and_send_to_para(tx):
    tx_signed = w3.eth.account.signTransaction(tx, private_key=private_key)
    tx_hash = w3.eth.sendRawTransaction(tx_signed.rawTransaction)
    tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

    print(f"tx_hash: {tx_hash}")
    print(f"tx_receipt: {tx_receipt}")


def get_stash_statuses(controllers_, validators_, nominators_):
    # 0 - Chill, 1 - Nominator, 2 - Validator
    statuses = {}
    nominators = set()
    validators = set()

    for nominator, _ in nominators_:
        nominators.add(nominator.value)

    for validator in validators_.value:
        validators.add(validator)

    for _, controller_info in controllers_:
        stash_account = controller_info.value['stash']
        if stash_account in nominators:
            statuses[stash_account] = 1
            continue

        if stash_account in validators:
            statuses[stash_account] = 2
            continue

        statuses[stash_account] = 0

    return statuses


def get_stash_balances(account_info):
    balances = {}

    for stash, info in account_info:
        balances[stash.value] = info.value['data']['free']

    return balances


def read_staking_parameters(app, block_hash=None, max_results=199):
    if not block_hash:
        block_hash = app.get_chain_head()

    print(f"Block hash: {block_hash}")

    staking_ledger_result = app.query_map(
        module='Staking',
        storage_function='Ledger',
        block_hash=block_hash,
        max_results=max_results,
    )

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

    system_account_result = app.query_map(
        module='System',
        storage_function='Account',
    )

    stash_balances = get_stash_balances(system_account_result)

    stash_statuses = get_stash_statuses(
        staking_ledger_result,
        session_validators_result,
        staking_nominators_result,
    )

    staking_parameters = []

    for controller, controller_info in staking_ledger_result:
        unlocking_values = []
        for elem in controller_info.value['unlocking']:
            unlocking_values.append({'balance': elem['value'], 'era': elem['era']})

        staking_parameters.append({
            'stash': '0x' + ss58_decode(controller_info.value['stash']),
            'controller': '0x' + ss58_decode(controller.value),
            'stake_status': stash_statuses[controller_info.value['stash']],
            'active_balance': controller_info.value['active'],
            'total_balance': controller_info.value['total'],
            'unlocking': unlocking_values,
            'claimed_rewards': controller_info.value['claimedRewards'],
            'stash_balance': stash_balances[controller_info.value['stash']],
        })

    return staking_parameters


def subscription_handler(era, update_nr, subscription_id):
    print(f"Active era index: {era.value['index']}, start timestamp: {era.value['start']}")

    # waiting for the end of the zero era in the queue
    if update_nr == 0:
        return

    staking_parameters = read_staking_parameters(substrate)
    # TODO remove later
    print(staking_parameters)
    print("________________________________________________________________________________")

    # uncomment when it's time to test
    tx = create_tx(era.value['index'], staking_parameters)
    # sign_and_send_to_para(tx)


def start_era_monitoring(app):
    app.query(
        module='Staking',
        storage_function='ActiveEra',
        subscription_handler=subscription_handler
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='oracle-service command line.')
    parser.add_argument('--ws_url_relay', help='websocket url', nargs='*', default=['ws://localhost:9951/'])
    parser.add_argument('--ws_url_para', help='websocket url', nargs='*', default=['ws://localhost:10055/'])
    parser.add_argument('--ss58_format', help='ss58 format', nargs=1, default=2)
    parser.add_argument('--type_registry_preset', help='type registry preset', nargs=1, default='kusama')
    parser.add_argument('--contract_address', help='parachain smart contract address', nargs='*', default='0x')
    parser.add_argument('--gas', help='gas', nargs=1, default=10000000)
    parser.add_argument('--gas_price', help='gas price', nargs=1, default=1000000000)
    parser.add_argument('--abi', help='path to abi', nargs=1, default='')

    args = parser.parse_args()
    ws_url_relay = args.ws_url_relay[0]
    ws_url_para = args.ws_url_para[0]
    ss58_format = args.ss58_format
    type_registry_preset = args.type_registry_preset
    contract_address = args.contract_address
    gas = args.gas
    gas_price = args.gas_price
    abi_path = args.abi

    w3 = Web3(Web3.WebsocketProvider(ws_url_para))
    substrate = create_interface(ws_url_relay, ss58_format, type_registry_preset)

    # TODO remove later for security reasons
    account = w3.eth.account.from_key('0x5fb92d6e98884f76de468fa3f6278f8807c48bebc13595d45af5bdc4da702133')
    private_key = account.privateKey
    
    start_era_monitoring(substrate)

