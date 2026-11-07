"""Score a test suite against the seeded faults in mutants.py.

    cd backend
    .venv/bin/python -m hw2_eval.run_mutants tests_hw2 --out ../docs/hw2/results/mutants-final.json

For every mutant, `backend/app` and the test directory are copied to a fresh temp
workspace, the fault is applied there, and the suite runs with `-x`; the real source
tree is never modified. A mutant is killed when pytest exits non-zero.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hw2_eval.mutants import MUTANTS, Mutant

BACKEND = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def workspace(test_dir: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="hw2-mut-"))
    backend = root / "backend"
    shutil.copytree(BACKEND / "app", backend / "app", ignore=IGNORE)
    shutil.copytree(BACKEND / test_dir, backend / test_dir, ignore=IGNORE)
    return root


def run_suite(backend: Path, test_dir: str, timeout: int) -> tuple[int | None, str]:
    cmd = [sys.executable, "-m", "pytest", test_dir, "-x", "-q", "-p", "no:cacheprovider", "--no-header"]
    try:
        proc = subprocess.run(cmd, cwd=backend, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    failed = next((l.split(" ")[1] for l in lines if l.startswith("FAILED ") or l.startswith("ERROR ")), "")
    return proc.returncode, failed or (lines[-1] if lines else "")


def check(mutant: Mutant) -> None:
    source = (BACKEND / mutant.file).read_text(encoding="utf-8")
    count = source.count(mutant.old)
    if count != 1:
        raise SystemExit(f"{mutant.id}: snippet found {count} times in {mutant.file}")


def run_mutant(mutant: Mutant, test_dir: str, timeout: int) -> dict:
    root = workspace(test_dir)
    try:
        path = root / "backend" / mutant.file
        path.write_text(path.read_text(encoding="utf-8").replace(mutant.old, mutant.new, 1), encoding="utf-8")
        start = time.monotonic()
        code, detail = run_suite(root / "backend", test_dir, timeout)
        return {
            "id": mutant.id,
            "file": mutant.file,
            "requirement": mutant.requirement,
            "killed": code != 0,  # a timeout (None) counts as detected
            "exit_code": code,
            "killed_by": detail if code != 0 else None,
            "seconds": round(time.monotonic() - start, 1),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("test_dir", help="test directory relative to backend/, e.g. tests_hw2")
    parser.add_argument("--out", required=True, help="where to write the JSON results")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--only", nargs="*", help="mutant ids to run (default: all)")
    args = parser.parse_args()

    mutants = [m for m in MUTANTS if not args.only or m.id in args.only]
    for m in mutants:
        check(m)

    root = workspace(args.test_dir)
    try:
        code, detail = run_suite(root / "backend", args.test_dir, args.timeout)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if code != 0:
        raise SystemExit(f"the unmutated suite must pass first (exit {code}: {detail})")

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda m: run_mutant(m, args.test_dir, args.timeout), mutants))

    killed = sum(r["killed"] for r in results)
    summary = {
        "test_dir": args.test_dir,
        "mutants": len(results),
        "killed": killed,
        "score": round(killed / len(results), 3),
        "survived": [r["id"] for r in results if not r["killed"]],
        "results": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for r in results:
        print(f"{r['id']}  {'KILLED  ' if r['killed'] else 'SURVIVED'}  {r['requirement']}")
    print(f"\n{args.test_dir}: {killed}/{len(results)} mutants killed ({summary['score']:.0%})")


if __name__ == "__main__":
    main()
