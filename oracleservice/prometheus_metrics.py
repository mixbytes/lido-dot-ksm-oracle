from dataclasses import dataclass
from prometheus_client import Counter, Gauge


@dataclass
class MetricsExporter:
    is_recovery_mode_active = Gauge('is_recovery_mode_active', '1, if the recovery mode, otherwise - the default mode')

    active_era_id = Gauge('active_era_id', 'active era index')
    last_era_reported = Gauge('last_era_reported', 'the last era that the Oracle has reported')
    previous_era_change_block_number = Gauge('previous_era_change_block_number', 'block number of the previous era change')
    time_elapsed_until_last_era_report = Gauge('time_elapsed_until_last_era_report',
                                               'the time elapsed until the last epoch report from the unix epoch in seconds')

    total_stashes_free_balance = Gauge('total_stashes_free_balance', 'total free balance of all stash accounts')

    para_exceptions_count = Counter('para_exceptions_count', 'parachain exceptions count')
    relay_exceptions_count = Counter('relay_exceptions_count', 'relay chain exceptions count')


metrics_exporter = MetricsExporter()
