# Server Start / Restart

Current recommended methods are the repo scripts, not a hand-written uvicorn command.

## Recommended commands

```bash
cd /home/garik/Documents/git/knowledge-miner
./run_server.sh
```

Routine restart after code/config changes:

```bash
cd /home/garik/Documents/git/knowledge-miner
./restart_server.sh
```

Check current server/runtime state:

```bash
cd /home/garik/Documents/git/knowledge-miner
./server_status.sh
```

These scripts already handle:
1. venv activation
2. `.env` loading if present
3. stopping stale uvicorn processes
4. runtime lock handling
5. healthcheck verification

## Manual start (only when you explicitly need it)

```bash
cd /home/garik/Documents/git/knowledge-miner
source .venv/bin/activate
set -a
[ -f .env ] && source .env
set +a
python -m uvicorn knowledge_miner.main:app --host 0.0.0.0 --port 8000 --reload
```

Expected health response:

`{"status":"ok"}`

## Manual health check

```bash
curl -s http://127.0.0.1:8000/healthz
```

## Notes

1. `run_server.sh` binds to `0.0.0.0:8000` by default.
2. `restart_server.sh` is the normal way to apply code/config changes locally.
3. `server_status.sh` is the quickest way to confirm PID, lock, and health status.
4. Frontend assets use a version query param, but a hard refresh of `/hmi2` is still useful after UI changes.

## Common issues

- `ModuleNotFoundError: No module named 'knowledge_miner'`
  - You are not in project folder, or venv not activated, or you bypassed the repo scripts incorrectly.
- `No such option: --relo`
  - Typo: use `--reload`.
- `SyntaxError` from `python -c "from ... import` on multiple lines
  - Keep `python -c` as a single-line command.
- Healthcheck fails after restart
  - inspect `knowledge-miner-uvicorn.log`
  - then run `./server_status.sh`
