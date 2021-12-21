from substrateinterface import SubstrateInterface
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
        recovering: bool = False, substrate: SubstrateInterface = None,
    ) -> SubstrateInterface:
        """Create Substrate interface with the first node that comes along, if there is no undesirable one"""
        tried_all = False

        if ss58_format not in SS58_FORMATS:
            logger.error("Invalid SS58 format")
            raise ValueError("Invalid SS58 format")

        while True:
            for url in urls:
                if url in undesirable_urls and not tried_all:
                    logger.info(f"Skipping undesirable url: {url}")
                    continue

                try:
                    if recovering:
                        substrate.websocket.close()
                        substrate.websocket.connect(url)
                    else:
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
