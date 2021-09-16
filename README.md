# lido-dot-ksm-oracle
oracle service for LiDo liquid staking 

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
* `PARA_ID` - Parachain ID.
* `PROMETHEUS_METRICS_PORT` - Prometheus client port. The default port is 8000.
* `LOG_LEVEL_STDOUT` - Logging level of the logging module: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. The default level is `INFO`.
