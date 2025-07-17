### Starting the Agent 

#### First Time:

**Health Monitor**
`python3 -m pip install psutil requests`
`python3 codex-health-monitor.py --config health-monitor-config.json --verbose > "./logs/health-monitor.log" 2>&1 &`

**Codex**
`cd codex-rs`
`cargo build`
`cargo run --bin codex -- autonomous -f ../config.yaml -d {DURATION} -m {MODEL}`


#### If Codex Dies 
Go into `codex-rs/logs` and find the most recent log dir. Then, run 
`cargo run --bin codex -- autonomous -f ../config.yaml --resume-dir logs/{LOGDIR}` 

