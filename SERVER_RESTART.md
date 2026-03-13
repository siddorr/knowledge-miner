# Server Restart Instruction (First Attempt)

Default method: start in a live terminal session.
Use these exact commands from a terminal.

## 1) Go to project and activate venv

```bash
cd /home/garik/Documents/git/knowledge-miner
source .venv/bin/activate
```

## 2) Load environment variables

```bash
set -a
[ -f .env ] && source .env
set +a
```

## 3) Stop old server (if running)

```bash
pkill -f "[u]vicorn knowledge_miner.main:app" || true
```

## 4) Start server (live session, recommended)

```bash
python -m uvicorn knowledge_miner.main:app --reload
```

Expected line:

`Uvicorn running on http://127.0.0.1:8000`

## 5) Verify health in another terminal

```bash
curl -s http://127.0.0.1:8000/healthz
```

Expected:

`{"status":"ok"}`

## Optional: background start (non-live)

Use this only if you explicitly want detached mode:

```bash
cd /home/garik/Documents/git/knowledge-miner && source .venv/bin/activate && set -a; [ -f .env ] && source .env || true; set +a; pkill -f "[u]vicorn knowledge_miner.main:app" || true; nohup python -m uvicorn knowledge_miner.main:app --reload >/tmp/knowledge-miner-uvicorn.log 2>&1 &
```

## One-line restart (live-session, copy/paste)

```bash
cd /home/garik/Documents/git/knowledge-miner && source .venv/bin/activate && set -a; [ -f .env ] && source .env || true; set +a; pkill -f "[u]vicorn knowledge_miner.main:app" || true; python -m uvicorn knowledge_miner.main:app --reload
```

## Common issues

- `ModuleNotFoundError: No module named 'knowledge_miner'`
  - You are not in project folder, or venv not activated.
- `No such option: --relo`
  - Typo: use `--reload`.
- `SyntaxError` from `python -c "from ... import` on multiple lines
  - Keep `python -c` as a single-line command.
