from dataclasses import dataclass, field
from substrateinterface.base import SubstrateInterface
from web3.main import Web3


@dataclass
class ServiceParameters:
    contract_address: str
    abi: list = field(default_factory=list)
    gas: int = 10000000

    era_duration: int = 30
    initial_block_number: int = 1

    max_number_of_failure_requests: int = 10
    timeout: int = 60

    stash_accounts: list = field(default_factory=list)

    para_id: int = 1000
    ss58_format: int = 2
    type_registry_preset: str = 'kusama'
    ws_urls_relay: list = field(default_factory=list)
    ws_urls_para: list = field(default_factory=list)

    substrate: SubstrateInterface = None
    w3: Web3 = None