# HW2: agent-generated test suite

| What | Where |
|---|---|
| Final test suite | [`backend/tests_hw2/`](../../backend/tests_hw2/) |
| Seeded-fault (mutation) harness | [`backend/hw2_eval/`](../../backend/hw2_eval/) |
| Raw results (pytest output, coverage JSON, mutation JSON) | [`docs/hw2/results/`](results/) |
| Every prompt given to the agent, verbatim | [`docs/hw2/AGENT_LOG.md`](AGENT_LOG.md) |
| Report | [`docs/hw2/REPORT.md`](REPORT.md) / `REPORT.pdf` |

The older suite in `backend/tests/` was written during HW1 and is kept only as a
reference point; the HW2 agent never read it.

## Run the tests

Needs Python 3.11+. Every network call and LLM is mocked, so no API keys or internet
access are needed.

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests_hw2 -q
```

The run ends with `433 passed, 6 xfailed`. The xfails are strict: they document five
real application bugs (one has two cases; see the report), and each turns into a failure
once its bug is fixed.

## Reproduce the coverage numbers

```bash
cd backend
.venv/bin/python -m pytest tests_hw2 -q --cov=app --cov-branch --cov-report=term-missing
```

## Reproduce the fault-detection numbers

Each seeded fault is applied to a temporary copy of `backend/app`, never to the real
source, and the suite runs against it. A fault is detected when any test fails.

```bash
cd backend
.venv/bin/python -m hw2_eval.run_mutants tests_hw2 --out /tmp/main.json               # 40 faults shown to the agent in Round 2
.venv/bin/python -m hw2_eval.run_mutants tests_hw2 --set heldout --out /tmp/held.json # 18 faults never shown to the agent
```

To score an earlier version of the suite, check it out somewhere and pass
`--from <path>/backend/tests_hw2`. Commits on this branch:

| Version | Commit |
|---|---|
| Initial (Part 1) | `666b421` |
| After Round 1 | `84f5a35` |
| After Round 2 (final) | the branch head |

The HW1 reference is scored with `run_mutants tests ...`.
