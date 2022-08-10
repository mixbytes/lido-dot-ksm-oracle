#!/usr/bin/env python3
import logging
import os
import signal
import sys

from flask import Flask, jsonify
from functools import partial
from log import init_log
from oracle import Oracle
from prometheus_client import make_wsgi_app
from server_thread import ServerThread
from service_parameters import ServiceParameters
from threading import Lock
from utils import cache, check_log_level, EXPECTED_NETWORK_EXCEPTIONS, stop_signal_handler
from websocket._exceptions import WebSocketConnectionClosedException
from werkzeug.middleware.dispatcher import DispatcherMiddleware


logger = logging.getLogger(__name__)
flask_app = Flask(__name__)
flask_app.wsgi_app = DispatcherMiddleware(flask_app.wsgi_app, {
    '/metrics': make_wsgi_app(),
})

DEFAULT_LOG_LEVEL_STDOUT = 'INFO'

cache.init_app(app=flask_app, config={'CACHE_TYPE': 'SimpleCache'})
cache.set('oracle_status', 'not working')
oracle_status_lock = Lock()


def main():
    try:
        log_level = os.getenv('LOG_LEVEL_STDOUT', DEFAULT_LOG_LEVEL_STDOUT)
        check_log_level(log_level)
        init_log(stdout_level=log_level)

        service_params = ServiceParameters(oracle_status_lock)
        oracle = Oracle(service_params=service_params)

    except KeyboardInterrupt:
        exit()
    except Exception as exc:
        sys.exit(f"An exception occurred: {type(exc)} - {exc}")

    try:
        rest_api_server = ServerThread(
            flask_app,
            service_params.rest_api_port,
            service_params.rest_api_ip_address,
        )
        logger.info(f"Starting the REST API server on port {service_params.rest_api_port}")
        rest_api_server.start()
    except Exception as exc:
        sys.exit(f"Failed to start REST API server: {type(exc)} - {exc}")

    signal.signal(signal.SIGTERM, partial(
        stop_signal_handler,
        substrate=oracle.service_params.substrate,
        rest_api_server=rest_api_server,
    ))
    signal.signal(signal.SIGINT, partial(
        stop_signal_handler,
        substrate=oracle.service_params.substrate,
        rest_api_server=rest_api_server,
    ))

    oracle_loop_start(oracle, rest_api_server)


def oracle_loop_start(oracle: Oracle, rest_api_server: ServerThread):
    """Start the Oracle in an infinite loop with exception handling"""
    while True:
        try:
            oracle.start_default_mode()

        except Exception as exc:
            exc_type = type(exc)
            if exc_type in EXPECTED_NETWORK_EXCEPTIONS:
                logger.warning(f"{exc_type}: {exc}")
            else:
                logger.error(f"{exc_type}: {exc}")
                if exc_type == WebSocketConnectionClosedException:
                    if exc.args and exc.args[0] == 'socket is already closed.':
                        stop_signal_handler(
                            substrate=oracle.service_params.substrate,
                            rest_api_server=rest_api_server,
                        )
            oracle.start_recovery_mode()


@flask_app.route('/healthcheck', methods=['GET'])
def healthcheck():
    with oracle_status_lock:
        oracle_status = cache.get('oracle_status')

    body = {'oracle_status': oracle_status}
    response = jsonify(body)
    response.status = 200

    return response


if __name__ == '__main__':
    main()
