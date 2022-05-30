import logging

from threading import Thread
from werkzeug.serving import make_server


logger = logging.getLogger(__name__)


class ServerThread(Thread):
    """A class containing methods for starting a thread with the server and terminating it"""
    def __init__(self, app, port: int, ip_address: str = 'localhost'):
        Thread.__init__(self)
        logger.debug(f"Starting web server on {ip_address}:{port}")
        self.server = make_server(ip_address, port, app, threaded=True)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        """Start the server"""
        self.server.serve_forever()

    def shutdown(self):
        """Shutdown the server"""
        self.server.shutdown()
