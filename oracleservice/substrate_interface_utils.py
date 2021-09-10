from substrateinterface import Keypair, SubstrateInterface
from websocket._exceptions import WebSocketAddressException
from websockets.exceptions import InvalidStatusCode

import logging
import time


logger = logging.getLogger(__name__)

SS58_FORMATS = (0, 2, 42)


class SubstrateInterfaceUtils:
    def create_interface(
        urls: list, ss58_format: int = 2,
        type_registry_preset: str = 'kusama',
        timeout: int = 60, undesirable_urls: set = set(),
    ) -> SubstrateInterface:
        """Create Substrate interface with the first node that comes along, if there is no undesirable one"""
        substrate = None
        tried_all = False

        if ss58_format not in SS58_FORMATS:
            logger.error("Invalid SS58 format")
            raise ValueError("Invalid SS58 format")

        while True:
            for url in urls:
                if url in undesirable_urls and not tried_all:
                    logger.info(f"Skipping undesirable url: {url}")
                    continue

                if not url.startswith('ws'):
                    logger.warning(f"Unsupported ws provider: {url}")
                    continue

                try:
                    substrate = SubstrateInterface(
                        url=url,
                        ss58_format=ss58_format,
                        type_registry_preset=type_registry_preset,
                    )

                    substrate.update_type_registry_presets()

                except (
                    ConnectionRefusedError,
                    InvalidStatusCode,
                    ValueError,
                    WebSocketAddressException,
                ) as exc:
                    logger.warning(f"Failed to connect to {url}: {exc}")
                    if isinstance(exc.args[0], str) and exc.args[0].find("Unsupported type registry preset") != -1:
                        raise ValueError(exc.args[0])

                else:
                    logger.info(f"The connection was made at the address: {url}")

                    return substrate

            tried_all = True

            logger.error("Failed to connect to any node")
            logger.info(f"Timeout: {timeout} seconds")
            time.sleep(timeout)

    def get_validators(substrate: SubstrateInterface, block_hash: str = None):
        """Get list of validators using 'Validators' storage function from 'Session' module"""
        if block_hash is None:
            return substrate.query(
             module='Session',
             storage_function='Validators',
            )

        return substrate.query(
            module='Session',
            storage_function='Validators',
            block_hash=block_hash,
        )

    def get_nominators(substrate: SubstrateInterface, block_hash: str = None):
        """Get list of nominators using 'Nominators' storage function from 'Staking' module"""
        if block_hash:
            return substrate.query_map(
             module='Staking',
             storage_function='Nominators',
             block_hash=block_hash,
            )

        return substrate.query_map(
            module='Staking',
            storage_function='Nominators',
        )

    def get_account(substrate: SubstrateInterface, stash: Keypair):
        """Get account using 'Account' storage function from 'System' module"""
        return substrate.query(
             module='System',
             storage_function='Account',
             params=[stash.ss58_address],
        )

    def get_ledger(substrate: SubstrateInterface, controller: Keypair, block_hash: str = None):
        """Get ledger using 'Ledger' storage function from 'Staking' module"""

        return substrate.query(
            module='Staking',
            storage_function='Ledger',
            params=[controller.ss58_address],
            block_hash=block_hash,
        )

    def get_controller(substrate: SubstrateInterface, stash: Keypair, block_hash: str = None):
        """Get controller using 'Bonded' storage function from 'Staking' module"""

        return substrate.query(
            module='Staking',
            storage_function='Bonded',
            params=[stash.ss58_address],
            block_hash=block_hash,
        )

    def get_parachain_address(_para_id: int) -> str:
        """Get parachain address using parachain id with ss58 format provided"""
        prefix = b'para'
        para_addr = bytearray(prefix)
        para_addr.append(_para_id & 0xFF)
        _para_id = _para_id >> 8
        para_addr.append(_para_id & 0xFF)
        _para_id = _para_id >> 8
        para_addr.append(_para_id & 0xFF)

        return para_addr.ljust(32, b'\0').hex()
