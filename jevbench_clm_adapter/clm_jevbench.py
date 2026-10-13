#!/usr/bin/env python3
"""Run Running-Dolphins/jev-bench datasets against local Contrastive-LM/CLM.

Place next to the unmodified upstream `jevbench.py`.
Only Python standard library. No Jev key required.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import jevbench as jb
except ImportError as e:
    raise SystemExit("Cannot import jevbench.py. Copy clm_jevbench.py into jev-bench/ and run it there.") from e

ROOT = Path(__file__).resolve().parent
DEFAULT_CLM_URL = os.getenv("CLM_URL", "http://127.0.0.1:8700")
DEFAULT_MODEL = os.getenv("CLM_MODEL", "clm-latest")
DEFAULT_OUT = ROOT / "clm_runs"


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def json_dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    staging.replace(path)


def get_json(url: str, timeout: int = 10, key: str = ""):
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CLMClient:
    def __init__(self, base_url, model, timeout=300, retries=3, api_key=""):
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.api_key = api_key

    def call(self, state, questions, dry_run=False):
        if dry_run:
            return jb._fake(questions), {"input_tokens": 0}, 0.001
        payload = json.dumps({"model": self.model, "state": state, "questions": questions},
                             ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        retry_codes = {429, 500, 502, 503, 504, 529}
        for attempt in range(self.retries):
            req = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
            begin = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    reply = json.loads(response.read().decode("utf-8"))
                elapsed = time.perf_counter() - begin
                if not isinstance(reply.get("answers"), dict):
                    raise RuntimeError("CLM response missing `answers`: " + repr(reply)[:400])
                for ident in questions:
                    if ident not in reply["answers"]:
                        raise RuntimeError(f"CLM response missing answers[{ident!r}]")
                return reply["answers"], reply.get("usage") or {}, elapsed
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:1400]
                err = f"HTTP {e.code} from {self.url}: {body}"
                if e.code not in retry_codes or attempt + 1 == self.retries:
                    raise RuntimeError(err) from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = f"CLM connection failed: {e!r}"
                if attempt + 1 == self.retries:
                    raise RuntimeError(err) from e
            delay = min(30, 2 ** attempt)
            print(f"  [retry {attempt + 1}/{self.retries}] {err}; sleep {delay}s", file=sys.stderr)
            time.sleep(delay)
        raise RuntimeError("CLM failed after retries")

    def check(self):
        h = get_json(self.base_url + "/health", timeout=10, key=self.api_key)
        if h.get("ok") is not True or h.get("embedder") is not True:
            raise RuntimeError(f"Health is not ready: {h}")
        models = get_json(self.base_url + "/v1/models", timeout=10, key=self.api_key)
        available = [x.get("name") for x in models.get("models", [])]
        if self.model not in available:
            raise RuntimeError(f"Model {self.model!r} not found in {available}")
        qa = {"check": {"type": "choice", "instructions": "Choose the correct team.",
                        "criteria": {"billing": "Handles refunds and invoices", "tech": "Fixes application code"}}}
        ans, _, elapsed = self.call("The customer requests a refund of a duplicate charge.", qa)
        probs = ans["check"].get("probabilities") or {}
        assert ans["check"]["type"] == "choice" and set(probs) == {"billing", "tech"}, ans
        print(f"PASS: CLM {self.model} /health, /v1/models, actual /v1/systemone ({elapsed:.2f}s)")


def example_fingerprint(task, n, examples, question, model):
    raw = json.dumps({"task": task, "requested_n": n, "examples": examples,
                      "question": question, "model": model},
                     ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def valid_prediction(prob, label):
    if not isinstance(prob, dict) or not prob:
        raise ValueError("Empty probability distribution")
    if label not in prob:
        raise ValueError(f"Gold label {label!r} not present among {list(prob)[:12]} (n={len(prob)})")
    ps = [float(x) for x in prob.values()]
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in ps) or abs(sum(ps) - 1) > 0.02:
        raise ValueError(f"Probabilities are invalid (sum={sum(ps):.4f})")


def adapt_summary(name, rows, metadata, model):
    # Reuse the benchmark author's exact accuracy/ECE/Brier/threshold formulas.
    jb.MODEL = model
    jb.PRICE_PER_MTOK = 0.0  # original Jev tariff MUST NOT be applied to self-hosted CLM
    s = jb.summarize(name, rows, metadata)
    s.pop("usd_per_1000_decisions", None)
    s["cost_note"] = "Self-hosted CLM GPU/CPU cost was NOT measured. Original Jev API tariff is inapplicable."
    s["backend_url"] = "local-clm"
    return s


def summary_display(s):
    print(f"  ACC={s['accuracy']:.2%} ECE={s['ece']:.4f} Brier={s['brier']:.4f} "
          f"latency p50={s['latency_median_s']:.3f}s p90={s['latency_p90_s']:.3f}s")
    p = s['thresholds']['0.9']
    print(f"  @0.9 coverage={p['coverage']:.2%} selective accuracy={p['accuracy']} "
          f"errors passed={p['errors_let_through']}")


def save_prediction_slim(out: Path, name, rows):
    slim_path = out / "predictions" / f"{name}.jsonl"
    slim_path.parent.mkdir(parents=True, exist_ok=True)
    with slim_path.open("w", encoding="utf-8") as fp:
        for i, r in enumerate(rows):
            slim = {"i": i, "label": r["label"], "pred": r["pred"],
                    "p_top": r["p_top"], "confidence": r.get("confidence"),
                    "correct": r["correct"], "latency_s": r["latency_s"],
                    "input_tokens": r.get("usage", {}).get("input_tokens", 0)}
            if "id" in r:
                slim["id"] = r["id"]
            fp.write(json.dumps(slim, ensure_ascii=False) + "\n")


def run_one(name, n, client, out, dry_run=False):
    fn, kind, description = jb.TASKS[name]
    print(f"\n[{name}] loading public dataset (requested {n} rows)...", flush=True)
    examples, question = fn(n)
    if not examples:
        raise RuntimeError(f"Dataset {name} returned no examples")
    fp = example_fingerprint(name, n, examples, question, client.model)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta_path = raw_dir / f"{name}.meta.json"
    raw_path = raw_dir / f"{name}.jsonl"
    rows = []
    if not dry_run:
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != fp:
                raise RuntimeError(f"Existing {name} checkpoint mismatches dataset/model/question. "
                                   f"Back up {raw_path} and {meta_path}, or choose another --out directory.")
            if raw_path.exists():
                for line in raw_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        obj = json.loads(line)
                        if obj.get("i") != len(rows):
                            raise RuntimeError(f"Bad checkpoint order for {name} at index {len(rows)}")
                        rows.append(obj["row"])
        else:
            if raw_path.exists():
                raise RuntimeError(f"Found {raw_path} without its metadata. Refusing unsafe resume.")
            json_dump(meta_path, {"task": name, "requested_n": n, "n": len(examples),
                                  "fingerprint": fp, "model": client.model,
                                  "backend_url": client.base_url, "created_at": iso_now()})
    print(f"[{name}] {len(rows)}/{len(examples)} completed; choice set: "
          f"{len(question.get('criteria', {})) if question.get('criteria') else 2}", flush=True)

    fp_out = None if dry_run else raw_path.open("a", encoding="utf-8")
    try:
        for i in range(len(rows), len(examples)):
            ex = examples[i]
            ans, usage, lat = client.call(ex["state"], {"q": question}, dry_run=dry_run)
            pred, top_p, confidence, prob = jb.read_answer(ans["q"])
            valid_prediction(prob, ex["label"])
            row = {**ex, "pred": pred, "p_top": top_p, "confidence": confidence, "prob": prob,
                   "correct": int(pred == ex["label"]), "latency_s": lat, "usage": usage}
            rows.append(row)
            if fp_out:
                fp_out.write(json.dumps({"i": i, "row": row}, ensure_ascii=False) + "\n")
                fp_out.flush()
            if (i + 1) % 10 == 0 or i + 1 == len(examples):
                print(f"  {name}: {i + 1}/{len(examples)}", flush=True)
    finally:
        if fp_out:
            fp_out.close()

    summary = adapt_summary(name, rows, {"kind": kind, "description": description,
                                         "requested_n": n, "fingerprint": fp}, client.model)
    summary_display(summary)
    if not dry_run:
        json_dump(out / "results" / f"{name}.json", summary)
        save_prediction_slim(out, name, rows)
    return summary


def run_experiment(name, n, client, out, dry_run=False):
    print(f"\n[experiment {name}] running unmodified benchmark experiment logic...", flush=True)
    jb.call = lambda state, questions, dry_run=False, retries=6: client.call(state, questions, dry_run=dry_run)
    jb.MODEL = client.model
    jb.PRICE_PER_MTOK = 0.0
    jb.WORKERS = 1  # two T4s: avoid sending up to 8 concurrent * 150-option requests
    exp_obj, rows = jb.EXPERIMENTS[name][0](n, dry_run)
    # Original experiment aggregate cost field, if present, inherits Jev's pricing
    # semantics. Suppress it instead of suggesting local inference costs $0.
    def redact_cost(x):
        if isinstance(x, dict):
            return {k: redact_cost(v) for k, v in x.items() if k != "usd_per_1000_decisions"}
        if isinstance(x, list):
            return [redact_cost(y) for y in x]
        return x
    exp_obj = redact_cost(exp_obj)
    exp_obj["model"] = client.model
    exp_obj["note"] = "No self-host GPU cost measurement; experimental runs do not support resume."
    print(json.dumps(exp_obj, indent=2, ensure_ascii=False))
    if not dry_run:
        json_dump(out / "experiments" / f"x-{name}.json", exp_obj)
        exp_raw = out / "experiments" / f"x-{name}.jsonl"
        with exp_raw.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return exp_obj


def compare(out):
    """Descriptive comparison to published Jev (Sep 2026), not a matched run."""
    target = out / "comparison_clm_vs_published_jev.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = ["task", "clm_n", "jev_n", "clm_accuracy", "jev_accuracy", "delta_accuracy_pp",
              "clm_ece", "jev_ece", "delta_ece", "clm_coverage_0.9", "jev_coverage_0.9",
              "clm_selective_accuracy_0.9", "jev_selective_accuracy_0.9",
              "clm_median_latency_s", "jev_median_latency_s", "note"]
    rows = []
    for task in jb.TASKS:
        local = out / "results" / f"{task}.json"
        published = ROOT / "results" / f"{task}.json"
        if not local.exists() or not published.exists():
            continue
        c = json.loads(local.read_text(encoding="utf-8"))
        j = json.loads(published.read_text(encoding="utf-8"))
        def gate(s, key):
            return (s.get("thresholds") or {}).get("0.9", {}).get(key)
        rows.append({
            "task": task, "clm_n": c.get("n"), "jev_n": j.get("n"),
            "clm_accuracy": c.get("accuracy"), "jev_accuracy": j.get("accuracy"),
            "delta_accuracy_pp": round(100 * (c["accuracy"] - j["accuracy"]), 2),
            "clm_ece": c.get("ece"), "jev_ece": j.get("ece"),
            "delta_ece": round(c["ece"] - j["ece"], 4),
            "clm_coverage_0.9": gate(c, "coverage"), "jev_coverage_0.9": gate(j, "coverage"),
            "clm_selective_accuracy_0.9": gate(c, "accuracy"),
            "jev_selective_accuracy_0.9": gate(j, "accuracy"),
            "clm_median_latency_s": c.get("latency_median_s"),
            "jev_median_latency_s": j.get("latency_median_s"),
            "note": "Published Jev historical baseline; samples/hardware/network may differ, not strict paired comparison",
        })
    with target.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} task comparisons to {target}")
    for row in rows:
        print(f"  {row['task']:<14} Jev={row['jev_accuracy']:.1%} "
              f"CLM={row['clm_accuracy']:.1%} Δ={row['delta_accuracy_pp']:+.2f}pp")
    if not rows:
        print("No comparable rows: run tasks and ensure upstream results/*.json exists.")


def main():
    ap = argparse.ArgumentParser(description="Run jev-bench against localhost CLM (no Jev API key)")
    ap.add_argument("command", choices=["check", "list", "run", "experiment", "compare"])
    ap.add_argument("name", nargs="?", default="all", help="task/experiment name, or all")
    ap.add_argument("--n", type=int, default=500, help="examples PER task (default 500)")
    ap.add_argument("--clm-url", default=DEFAULT_CLM_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true", help="fake predictions; do not save results")
    args = ap.parse_args()
    if args.n < 1 or args.timeout < 1 or args.retries < 1:
        ap.error("--n, --timeout and --retries must be positive")
    if args.command == "list":
        print("TASKS (12):")
        for name, (_, kind, desc) in jb.TASKS.items():
            print(f"  {name:<15} {kind:<22} {desc}")
        print("EXPERIMENTS (6):")
        for name, (_, desc) in jb.EXPERIMENTS.items():
            print(f"  {name:<15} {desc}")
        return
    if args.command == "compare":
        compare(args.out)
        return

    client = CLMClient(args.clm_url, args.model, timeout=args.timeout,
                       retries=args.retries, api_key=os.getenv("CLM_API_KEY", ""))
    if not args.dry_run:
        client.check()
    else:
        print("DRY RUN: fake predictions only, not evidence of model quality.")
    if args.command == "check":
        return
    if args.command == "run":
        names = list(jb.TASKS) if args.name == "all" else [args.name]
        if any(n not in jb.TASKS for n in names):
            ap.error(f"unknown task {args.name!r}; use list")
        all_summaries = {}
        for name in names:
            s = run_one(name, args.n, client, args.out, args.dry_run)
            all_summaries[name] = {"n": s["n"], "accuracy": s["accuracy"],
                                   "ece": s["ece"], "brier": s["brier"],
                                   "latency_median_s": s["latency_median_s"]}
        if not args.dry_run:
            json_dump(args.out / "aggregate.json", {"timestamp": iso_now(), "model": client.model,
                                                    "tasks_this_invocation": all_summaries,
                                                    "note": "Aggregate only includes completed tasks of this invocation."})
            compare(args.out)
        print(f"\nFinished {len(names)} tasks, {sum(s['n'] for s in all_summaries.values())} evaluated examples")
    else:
        names = list(jb.EXPERIMENTS) if args.name == "all" else [args.name]
        if any(n not in jb.EXPERIMENTS for n in names):
            ap.error(f"unknown experiment {args.name!r}; use list")
        for name in names:
            run_experiment(name, args.n, client, args.out, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Per-task progress was checkpointed; repeat the same command to resume.",
              file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print("Check vLLM terminal/logs for EngineCore error. Completed samples can be resumed.", file=sys.stderr)
        sys.exit(1)
