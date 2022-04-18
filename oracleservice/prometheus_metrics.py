from dataclasses import dataclass, InitVar
from prometheus_client import Counter, Gauge, Histogram, Info

import os


@dataclass
class MetricsExporter:
    """Prometheus metrics that the Oracle collects"""
    _prefix: InitVar[str] = None

    def __post_init__(self, _prefix: str):
        if _prefix is None:
            _prefix = ''

        self.agent = Info('agent', "the address of the connected relay chain node", namespace=_prefix)

        self.is_recovery_mode_active = Gauge('is_recovery_mode_active', "1, if the recovery mode, otherwise - the default mode", namespace=_prefix)  # noqa: E501

        self.active_era_id = Gauge('active_era_id', "active era index", namespace=_prefix)
        self.last_era_reported = Gauge('last_era_reported', "the last era that the Oracle has reported", namespace=_prefix)
        self.last_failed_era = Gauge('last_failed_era', "the last era for which sending the report ended with a revert", namespace=_prefix)  # noqa: E501
        self.previous_era_change_block_number = Gauge('previous_era_change_block_number', "block number of the previous era change", namespace=_prefix)  # noqa: E501
        self.time_elapsed_until_last_report = Gauge('time_elapsed_until_last_report', "the time elapsed until the last report from the unix epoch in seconds", namespace=_prefix)  # noqa: E501

        self.total_stashes_free_balance = Gauge('total_stashes_free_balance', "total free balance of all stash accounts", namespace=_prefix)  # noqa: E501
        self.oracle_balance = Gauge('oracle_balance', "oracle balance [wei]", ['address'], namespace=_prefix)

        self.tx_revert = Histogram('tx_revert', "reverted transactions", namespace=_prefix)
        self.tx_success = Histogram('tx_success', "successful transactions", namespace=_prefix)

        self.para_exceptions_count = Counter('para_exceptions_count', "parachain exceptions count", namespace=_prefix)
        self.relay_exceptions_count = Counter('relay_exceptions_count', "relay chain exceptions count", namespace=_prefix)


prefix = os.getenv('PROMETHEUS_METRICS_PREFIX', '')
metrics_exporter = MetricsExporter(prefix)
