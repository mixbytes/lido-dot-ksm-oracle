from substrateinterface import SubstrateInterface

import logging
import time


logger = logging.getLogger(__name__)

max_number_of_requests = 0
substrate = None
timeout = 0
wal_manager = None
w3 = None


def change_node(url, ss58_format, type_registry_preset, undesirable_url=None):
    tried_all = False

    while True:
        substrate = None

        for u in url:
            if u == undesirable_url and not tried_all:
                logging.info(f"Skipping undesirable url: {u}")
                continue

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

            except ConnectionRefusedError:
                logging.warning(f"Failed to connect to {u}: connection refused")

            else:
                logging.info(f"The connection was made at the address: {u}")
                return substrate

        tried_all = True
        logging.error('Failed to connect to any node')
        logger.info(f"Timeout: {timeout} seconds")
        time.sleep(timeout)


def recovery_mode(_w3, _substrate, _wal_mng, _timeout: int, max_num_or_req: int, url: list, is_start=True):
    global max_number_of_requests
    global timeout
    global substrate
    global wal_manager
    global w3

    max_number_of_requests = max_num_or_req
    timeout = _timeout
    substrate = _substrate
    wal_manager = _wal_mng
    w3 = _w3

    logger.info('Starting recovery mode')

    record = wal_manager.get_last_record()
    counter = 0
    wal_error = False

    try:
        if not record['approved']:
            record = wal_manager.get_penultimate_record()

        counter = int(record['requests_counter'])

    except KeyError:
        logging.warning('Error in the WAL structure')
        wal_error = True

    if counter > max_number_of_requests:
        logger.info('Trying to connect to another node')
        substrate = change_node(url, substrate.ss58_format, substrate.type_registry_preset, substrate.url)

    elif not is_start or wal_error:
        logger.info('Trying to connect to another node')
        substrate = change_node(url, substrate.ss58_format, substrate.type_registry_preset)

    logger.info('Recovery mode is completed')
    return substrate
