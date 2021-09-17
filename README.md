# lido-dot-ksm-oracle
Oracle service for LiDo liquid staking. Collects staking parameters from relay chain and reports to the parachain [liquid staking module](https://github.com/mixbytes/lido-dot-ksm).

## How it works
* Upon the start daemon determines the reportable era and retrieves the list of stash accounts to report for.
* If no stash accounts were found, waits for the beginning of the next era. Otherwise, daemon starts collections staking parameters for each stash account separately, signs and sends transaction to the oracle contract.
* After a report has been sent for all stash accounts, it moves on to waiting for the next era.

## Requirements
* Running relay chain node, from which the staking parameters are read.
* Running parachain node with [contracts](https://github.com/mixbytes/lido-dot-ksm) deployed.
* ABI for the OracleMaster contract (see above).
* Python 3.7+
* The application functions as a linux daemon (unit) process under the System V subsystem.


## Setup
```shell
python -m pip install --upgrade pip
pip install -r requirements.txt
```


## Run
The oracle service receives its configuration from environment variables. You need to provide WS URLs of relay chain and parachain nodes, oracle private key, OracleMaster contract address and parachain ID.

```shell
export ORACLE_PRIVATE_KEY=$ORACLE_PRIVATE_KEY_0X_PREFIXED
export WS_URL_RELAY=$RELAY_CHAIN_NODE_ADDRESS
export WS_URL_PARA=$PARACHAIN_NODE_ADDRESS
export CONTRACT_ADDRESS=0xc01Ee7f10EA4aF4673cFff62710E1D7792aBa8f3
export PARA_ID=1000
./oracleservice/start.py
```

To stop the service, send a SIGINT or SIGTERM signal to the process.


## Full list of configuration options

* `WS_URL_RELAY` - WS URL of relay chain node. **Required**.
* `WS_URL_PARA` - WS URL of parachain node. **Required**.
* `CONTRACT_ADDRESS` - OracleMaster contract address. **Required**. Example: `0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84`.
* `ORACLE_PRIVATE_KEY` - Oracle private key, 0x prefixed. **Required**.
* `ABI_PATH` - Path to ABI file. The default value is `oracleservice/abi.json`.
* `GAS_LIMIT` - The predefined gas limit for composed transaction. The default value is 10000000.
* `MAX_NUMBER_OF_FAILURE_REQUESTS` - If the number of failure requests exceeds this value, the node (relay chain or parachain) is blacklisted for TIMEOUT seconds during recovery mode. The default value is 10.
* `TIMEOUT` - The time the failure node stays in the black list in recovery mode. The default value is 60 seconds.
* `ERA_DURATION_IN_SECONDS` - The duration of era in seconds. Needed for setting the SIGALRM timer. The default value is 180. **Required**.
* `ERA_DURATION_IN_BLOCKS` - The duration of era in blocks. Needed to calculate the start block number of a new era according to the formula "`era_id` * `ERA_DURATION_IN_SECONDS` + `INITIAL_BLOCK_NUMBER`" The default value is 30. **Required**.
* `INITIAL_BLOCK_NUMBER` - The sequence number of the block, from which the countdown is done according to the formula: "`era_id` * `ERA_DURATION_IN_SECONDS` + `INITIAL_BLOCK_NUMBER`". The default value is 1. **Required**.
* `WATCHDOG_DELAY` - Additional time before watchdog is triggered to break connection if there is no era change event more than `ERA_DURATION_IN_SECONDS` seconds. The default value is 5 seconds.
* `SS58_FORMAT` - The default value is 2. **Required**.
* `TYPE_REGISTRY_PRESET` - The default value is 'kusama'. **Required**.
* `PARA_ID` - Parachain ID. **Required**.
* `PROMETHEUS_METRICS_PORT` - Prometheus client port. The default port is 8000.
* `LOG_LEVEL_STDOUT` - Logging level of the logging module: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. The default level is `INFO`.


## Prometheus metrics

Prometheus exporter is running on port 8000 by default and provides the following metrics.

| name                                     | description                                                      | frequency                                 |
| ---------------------------------------- | -----------------------------------------------------------------|-------------------------------------------|
| **process_virtual_memory_bytes**  <br> *Gauge* | Virtual memory size in bytes | Every call |
| **process_resident_memory_bytes** <br> *Gauge* | Resident memory size in bytes | Every call |
| **process_start_time_seconds**    <br> *Gauge* | Start time of the process since unix epoch in seconds | Every call |
| **process_cpu_seconds_total**     <br> *Counter* | Total user and system CPU time spent in seconds | Every call |
| **process_open_fds**              <br> *Gauge* | Number of open file descriptors | Every call |
| **process_max_fds**               <br> *Gauge* | Maximum number of open file descriptors | Every call |
| **agent**            <br> *Info* | The address of the connected relay chain node | Each reconnection |
| **is_recovery_mode_active**            <br> *Gauge* | Is oracle-service in recovery mode or not: 1, if the recovery mode, otherwise - the default mode | Starting and ending recovery mode |
| **active_era_id**            <br> *Gauge* | Active era index | Every change of era |
| **last_era_reported**            <br> *Gauge* | The last era that the Oracle has reported | After completing the sending of reports for the era |
| **previous_era_change_block_number**            <br> *Gauge* | Block number of the previous era change | Every change of era, if at least one stash account was found |
| **time_elapsed_until_last_era_report**            <br> *Gauge* | The time elapsed until the last report from the UNIX epoch in seconds | After each successful sending of a report |
| **total_stashes_free_balance**            <br> *Gauge* | Total free balance of all stash accounts for the era | Every time a new report is generated |
| **tx_revert**            <br> *Histogram* | Number of failed transactions | Every unsuccessful sending of a report |
| **tx_success**            <br> *Histogram* | Number of successful transactions | Every successful sending of a report |
| **para_exceptions_count**            <br> *Counter* | Parachain exceptions count | |
| **relay_exceptions_count**            <br> *Counter* | Relay chain exceptions count |  |
