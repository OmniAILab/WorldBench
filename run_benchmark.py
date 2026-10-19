import os
import json
import glob
import time
import argparse
import fnmatch
import hashlib

from core.sandbox import LocalSandbox
import apps
from core.env import WorldBenchEnv
from core.llm import PROVIDERS_REQUIRING_KEY, detect_provider
from core.policy import LLMPolicy
from evaluation import EVALUATION_FUNCTIONS
from evaluation.evaluators import INITIAL_STATE_FUNCTIONS, active_judge_model, calendar_paths


def discover_subtasks(benchmark_dir):
    """Yield (language, persona, subtask_id, config_path, testbed_dir).

    Expected layout:
        <benchmark_dir>/<LANG>/<Persona_X>/subtasks/<id>.json
        <benchmark_dir>/<LANG>/<Persona_X>/testbed/

    The language level is optional: if a persona folder is found directly under
    <benchmark_dir>, language is reported as "".
    """
    items = []
    config_paths = glob.glob(
        os.path.join(benchmark_dir, "**", "subtasks", "*.json"), recursive=True
    )
    for config_path in sorted(config_paths):
        persona_dir = os.path.dirname(os.path.dirname(config_path))
        persona = os.path.basename(persona_dir)
        parent = os.path.dirname(persona_dir)
        language = (
            os.path.basename(parent)
            if os.path.abspath(parent) != os.path.abspath(benchmark_dir)
            else ""
        )
        subtask_id = os.path.splitext(os.path.basename(config_path))[0]
        testbed_dir = os.path.join(persona_dir, "testbed")
        items.append((language, persona, subtask_id, config_path, testbed_dir))
    return items


def resolve_api_key(model_name, openai_key, gemini_key):
    provider = detect_provider(model_name)
    if provider == "openai":
        return openai_key
    if provider == "google":
        return gemini_key
    if provider == "local":
        return os.environ.get("LOCAL_API_KEY", "EMPTY")
    return ""


def run_single_task(config, source_testbed_dir, model_name, api_key, work_dir, max_iter, debug,
                    temperature=None, max_obs_chars=0, expose_keys=False,
                    max_history_chars=24000):
    task_text = config.get("task", "")
    apps_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps")

    sandbox = LocalSandbox(os.path.join(work_dir, "sandbox"))
    sandbox.setup(
        source_testbed_dir=source_testbed_dir if os.path.isdir(source_testbed_dir) else None,
        source_apps_dir=apps_dir,
        openai_key=os.environ.get("OPENAI_API_KEY"),
        gemini_key=os.environ.get("GEMINI_API_KEY"),
        expose_keys=expose_keys,
    )

    env = WorldBenchEnv(sandbox=sandbox, task=task_text, verbose=debug,
                        max_obs_chars=max_obs_chars)
    env.reset()

    policy = LLMPolicy(
        model_name=model_name,
        key=api_key,
        env=env,
        config=config,
        debug_mode=debug,
        temperature=temperature,
        max_history_chars=max_history_chars,
    )

    done = False
    n_iter = 0
    while not done:
        n_iter += 1
        action = policy.forward(env)
        obs, reward, done, info = env.step(action)
        act_type = env.last_action_type or "malformed"
        print(f"    [step {n_iter}/{max_iter}] {act_type} -> {str(obs)[:70]}", flush=True)
        if n_iter >= max_iter:
            if debug:
                print(f"Max iterations reached: {max_iter}")
            break

    output_dir = os.path.join(work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    env.snapshot(output_dir)
    env.dump_history(output_dir)
    policy.dump_history(output_dir)

    return os.path.join(output_dir, "testbed"), env, n_iter


def evaluate_task(config, result_testbed_dir, initial_testbed_dir=None):
    items = config.get("evaluation") or []
    if not items:
        return False, [{"function": "(none)", "passed": False, "error": "empty_evaluation"}]
    eval_details = []
    overall = True
    for eval_item in items:
        function = eval_item["function"]
        args = eval_item["args"]
        fn = EVALUATION_FUNCTIONS.get(function)
        if fn is None:
            print(f"Unknown evaluation function: {function}")
            eval_details.append({"function": function, "passed": False, "error": "unknown_function"})
            overall = False
            continue
        try:
            extra = {"initial_dir": initial_testbed_dir} if function in INITIAL_STATE_FUNCTIONS else {}
            passed = bool(fn(result_testbed_dir, args, **extra))
            eval_details.append({"function": function, "passed": passed})
            if not passed:
                print(f"Failed: {function} {args}")
                overall = False
        except Exception as e:
            print(f"Error in {function}: {type(e).__name__}: {e}")
            eval_details.append({"function": function, "passed": False,
                                 "error": f"{type(e).__name__}: {e}"})
            overall = False
    return overall, eval_details


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_files_rel(root):
    """Map every regular file under root to its digest.

    Read errors are not absorbed: a file that cannot be read makes preservation
    unscorable, and an unscorable metric must not silently report success.
    """
    out = {}
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if os.path.islink(full) or not os.path.isfile(full):
                raise PreservationUnscorable(
                    f"{full} is not a regular file; preservation is defined over regular "
                    "files only"
                )
            out[rel] = _file_digest(full)
    return out


def _normalise_target(path):
    """Canonical testbed-relative form of an evaluator file argument."""
    path = path.replace("\\", "/").lstrip("/")
    if path.startswith("./"):
        path = path[2:]
    path = os.path.normpath(path)
    parts = path.split(os.sep)
    if parts and parts[0] == "testbed":
        parts = parts[1:]
    return os.sep.join(parts)


def _declared_paths(entries):
    if isinstance(entries, (str, dict)):
        entries = [entries]
    for entry in entries or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if isinstance(path, str) and path:
            yield path


def _eval_target_files(config):
    """Files the task is allowed to create or modify."""
    declared = []
    sources = [config] + [ev.get("args", {}) for ev in config.get("evaluation", [])]
    for source in sources:
        declared.extend(_declared_paths(source.get("target_files")))
        declared.extend(_declared_paths(source.get("output_files")))
    for ev in config.get("evaluation", []):
        args = ev.get("args", {})
        declared.extend(_declared_paths(args.get("file")))
        if args.get("username") and not args.get("file") and ev.get("function", "").startswith(
                "evaluate_calendar"):
            declared.extend(calendar_paths(args["username"]))

    targets = set()
    for path in declared:
        path = _normalise_target(path)
        targets.add(path)
        if os.sep not in path:
            targets.add(os.path.join("data", path))
    return targets


class PreservationUnscorable(Exception):
    """Raised when the initial or final state needed to score preservation is missing."""


def compute_preservation(source_testbed_dir, result_testbed_dir, config):
    """Preservation of pre-existing non-target files, plus created-artefact diagnostics.

    Preservation, and therefore CTS, depends only on pre-existing non-target files:
    a file created by the agent is reported separately and never fails the constraint.
    A missing initial or final state raises instead of reporting success, because the
    constraint is unscorable without both.
    """
    if not source_testbed_dir or not os.path.isdir(source_testbed_dir):
        raise PreservationUnscorable(
            f"missing pristine testbed: {source_testbed_dir}"
        )
    if not result_testbed_dir or not os.path.isdir(result_testbed_dir):
        raise PreservationUnscorable(
            f"missing final testbed: {result_testbed_dir}"
        )
    before = _list_files_rel(source_testbed_dir)
    after = _list_files_rel(result_testbed_dir)
    targets = _eval_target_files(config)

    def is_target(rel):
        rel = _normalise_target(rel)
        return any(fnmatch.fnmatchcase(rel, pattern) for pattern in targets)

    non_target = [rel for rel in before if not is_target(rel)]
    modified = sum(1 for rel in non_target if rel in after and after[rel] != before[rel])
    deleted = sum(1 for rel in non_target if rel not in after)
    created = sum(1 for rel in after if rel not in before and not is_target(rel))
    target_touched = any(
        rel for rel in set(before) | set(after)
        if is_target(rel) and before.get(rel) != after.get(rel)
    )
    return {
        "preserved": modified + deleted == 0,
        "n_non_target_changes": modified + deleted,
        "n_modified": modified,
        "n_deleted": deleted,
        "n_created": created,
        "n_non_target": len(non_target),
        "acted": bool(modified or deleted or created or target_touched),
    }


def compute_task_stats(env, n_iter, max_iter, elapsed):
    apps_used = {}
    for action, _ in env.history:
        if isinstance(action, dict):
            app = action.get("app")
            if app:
                apps_used[app] = apps_used.get(app, 0) + 1
    return {
        "n_steps": n_iter,
        "n_actions": len(env.history),
        "n_malformed": getattr(env, "n_malformed", 0),
        "n_protocol_violations": getattr(env, "n_protocol_violations", 0),
        "n_failed_exec": getattr(env, "n_failed_exec", 0),
        "apps_used": apps_used,
        "last_action_type": env.last_action_type,
        "hit_max_iter": n_iter >= max_iter
        and env.last_action_type not in ("finish_task", "got_stuck"),
        "finished_cleanly": env.last_action_type == "finish_task",
        "elapsed_sec": round(elapsed, 2),
    }


def write_reports(run_root, results, total_planned, model_name, tag, done=False):
    """Write results.jsonl and report.md atomically after every task."""

    def atomic_write(path, text):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    atomic_write(
        os.path.join(run_root, "results.jsonl"),
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
    )

    passed = sum(1 for r in results if r["is_pass"])
    total_done = len(results)

    def avg(key):
        vals = [r.get(key) for r in results if isinstance(r.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    by_lang = {}
    for r in results:
        by_lang.setdefault(r["language"] or "(root)", []).append(r)
    by_persona = {}
    for r in results:
        by_persona.setdefault((r["language"] or "(root)", r["persona"]), []).append(r)

    eval_stats = {}
    for r in results:
        for ed in r.get("evaluations", []):
            s = eval_stats.setdefault(ed["function"], {"pass": 0, "errors": 0, "total": 0})
            s["total"] += 1
            if ed["passed"]:
                s["pass"] += 1
            elif "error" in ed:
                s["errors"] += 1

    n_hit_max = sum(1 for r in results if r.get("hit_max_iter"))
    n_clean = sum(1 for r in results if r.get("finished_cleanly"))
    n_errors = sum(1 for r in results if r.get("error"))
    n_eval_errors = sum(1 for r in results if r.get("n_evaluator_errors"))
    n_cts = sum(1 for r in results if r.get("cts"))
    preservation_scored = [r for r in results if r.get("preserved") is not None]
    n_preserved = sum(1 for r in preservation_scored if r["preserved"])
    acted = [r for r in preservation_scored if r.get("acted")]
    n_preserved_acted = sum(1 for r in acted if r["preserved"])
    n_with_created = sum(1 for r in preservation_scored if r.get("n_created"))
    succ_steps = [
        r.get("n_steps")
        for r in results
        if r.get("is_pass") and isinstance(r.get("n_steps"), (int, float))
    ]
    avg_succ_steps = sum(succ_steps) / len(succ_steps) if succ_steps else 0.0

    lines = []
    lines.append(f"# WorldBench report — {model_name} ({tag})")
    lines.append("")
    lines.append(f"Judge: `{active_judge_model()}` — apps: {', '.join(sorted(apps.AVAILABLE_APPS))}")
    lines.append("")
    lines.append(f"Status: {'COMPLETED' if done else 'IN PROGRESS'}  ({total_done}/{total_planned} tasks run)")
    lines.append("")
    lines.append("## Headline metric: CTS")
    lines.append("")
    lines.append(
        "**CTS (Constrained Task Success)** — a task counts only when every task-specific "
        "evaluation function passes and every pre-existing non-target file is left unchanged."
    )
    lines.append("")
    if total_done:
        lines.append(f"### CTS: {n_cts}/{total_done} = {n_cts / total_done * 100:.2f}%")
    lines.append("")
    lines.append("## Secondary metrics")
    lines.append("")
    if total_done:
        lines.append(f"- Pass rate (task evaluators only): {passed}/{total_done} = {passed / total_done * 100:.2f}%")
        if preservation_scored:
            lines.append(
                f"- Preservation rate (pre-existing non-target files untouched): "
                f"{n_preserved}/{len(preservation_scored)} = "
                f"{n_preserved / len(preservation_scored) * 100:.2f}%  "
                f"(over tasks that ran; {total_done - len(preservation_scored)} errored)"
            )
        if acted:
            lines.append(
                f"- Preservation rate on tasks where the agent changed the state: "
                f"{n_preserved_acted}/{len(acted)} = "
                f"{n_preserved_acted / len(acted) * 100:.2f}%  "
                f"({len(preservation_scored) - len(acted)} tasks left the testbed untouched "
                f"and preserve it vacuously)"
            )
        lines.append(f"- Mean steps on passed tasks: {avg_succ_steps:.1f}")
    lines.append("")
    lines.append("## Aggregate stats")
    lines.append("")
    lines.append(f"- Mean steps per task: {avg('n_steps'):.1f}")
    lines.append(f"- Mean malformed actions per task: {avg('n_malformed'):.2f}")
    lines.append(f"- Mean failed executions per task: {avg('n_failed_exec'):.2f}")
    lines.append(f"- Mean protocol violations per task: {avg('n_protocol_violations'):.2f}")
    lines.append(f"- Mean non-target changes per task: {avg('n_non_target_changes'):.2f}")
    lines.append(
        f"- Mean modified / deleted per task: {avg('n_modified'):.2f} / {avg('n_deleted'):.2f}"
    )
    lines.append(
        f"- Mean created non-target artefacts per task: {avg('n_created'):.2f}  "
        f"({n_with_created}/{len(preservation_scored) or 1} tasks left at least one; "
        f"diagnostic only, not part of CTS)"
    )
    lines.append(f"- Mean time per task (s): {avg('elapsed_sec'):.1f}")
    lines.append(f"- Tasks terminated via finish_task: {n_clean}/{total_done}")
    lines.append(f"- Tasks that hit the iteration cap: {n_hit_max}/{total_done}")
    lines.append(f"- Tasks with a runtime error: {n_errors}/{total_done}")
    lines.append(f"- Tasks with an evaluator error: {n_eval_errors}/{total_done}")
    lines.append("")
    lines.append("## By language")
    lines.append("")
    lines.append("| Language | CTS | Pass | Total | CTS rate | Pass rate | Mean steps |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for lang, rs in sorted(by_lang.items()):
        c = sum(1 for r in rs if r.get("cts"))
        p = sum(1 for r in rs if r["is_pass"])
        st = sum(r.get("n_steps", 0) for r in rs) / len(rs)
        lines.append(f"| {lang} | {c} | {p} | {len(rs)} | {c / len(rs) * 100:.1f}% | {p / len(rs) * 100:.1f}% | {st:.1f} |")
    lines.append("")
    lines.append("## By persona")
    lines.append("")
    lines.append("| Language | Persona | CTS | Pass | Total | CTS rate | Pass rate |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for (lang, persona), rs in sorted(by_persona.items()):
        c = sum(1 for r in rs if r.get("cts"))
        p = sum(1 for r in rs if r["is_pass"])
        lines.append(f"| {lang} | {persona} | {c} | {p} | {len(rs)} | {c / len(rs) * 100:.1f}% | {p / len(rs) * 100:.1f}% |")
    lines.append("")
    lines.append("## By evaluation function")
    lines.append("")
    lines.append("| Function | Pass | Evaluator errors | Total | Rate |")
    lines.append("| --- | --- | --- | --- | --- |")
    for fn, s in sorted(eval_stats.items()):
        lines.append(f"| {fn} | {s['pass']} | {s['errors']} | {s['total']} | "
                     f"{s['pass'] / s['total'] * 100:.1f}% |")
    lines.append("")
    lines.append("## Per task")
    lines.append("")
    lines.append("| Language | Persona | Subtask | CTS | Pass | Preserved | Acted | Modified | Deleted | Created | Steps | Malformed | Protocol | Failed exec | Last action |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        lines.append(
            f"| {r['language'] or '(root)'} | {r['persona']} | {r['subtask_id']} | "
            f"{'YES' if r.get('cts') else 'NO'} | {'PASS' if r['is_pass'] else 'FAIL'} | "
            f"{'—' if r.get('preserved') is None else ('YES' if r['preserved'] else 'NO')} | "
            f"{'—' if r.get('acted') is None else ('YES' if r['acted'] else 'NO')} | "
            f"{r.get('n_modified', '')} | {r.get('n_deleted', '')} | {r.get('n_created', '')} | "
            f"{r.get('n_steps', '')} | {r.get('n_malformed', '')} | "
            f"{r.get('n_protocol_violations', '')} | "
            f"{r.get('n_failed_exec', '')} | {r.get('last_action_type', '')} |"
        )
    lines.append("")
    atomic_write(os.path.join(run_root, "report.md"), "\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Run WorldBench tasks against an LLM agent.")
    parser.add_argument("--benchmark_dir", required=True,
                        help="Root directory of the benchmark (e.g. ./EN-UK or a folder containing EN-UK/, FR/, ...)")
    parser.add_argument("--model_name", default="gemini-3.5-flash",
                        help="Agent model: gpt-*, gemini-*, or a HuggingFace repo id such as "
                             "meta-llama/Llama-3.3-70B-Instruct (default gemini-3.5-flash)")
    parser.add_argument("--tag", default="run", help="Run tag")
    parser.add_argument("--work_dir", default="./runs", help="Working directory for outputs")
    parser.add_argument("--max_iter", type=int, default=30, help="Iteration cap per task")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Override the agent decoding temperature for every provider")
    parser.add_argument("--max_obs_chars", type=int, default=0,
                        help="Truncate each observation to this many characters (0 disables it)")
    parser.add_argument("--max_history_chars", type=int, default=24000,
                        help="Budget for the execution history replayed in each prompt")
    parser.add_argument("--enable_llm_app", action="store_true",
                        help="Expose the llm app, letting the agent delegate to a hosted model. "
                             "Off by default because it confounds comparisons between backbones")
    parser.add_argument("--judge_model", default=None, help="Override the LLM-judge model")
    parser.add_argument("--language", default=None, help="Only run tasks under this top-level folder (e.g. EN-UK, IT)")
    parser.add_argument("--set_language", default=None,
                        help="Force the language label for all tasks in this run, for benchmark "
                             "directories that point straight at a single setting")
    parser.add_argument("--persona", default=None, help="Only run tasks for this second-level folder (e.g. Persona_1)")
    parser.add_argument("--subtask", default=None, help="Only run subtasks with this id (e.g. 0)")
    parser.add_argument("--select", action="append", default=None,
                        help="Only run tasks whose path contains this substring; repeatable")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N subtasks, after the other filters")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.judge_model:
        os.environ["JUDGE_MODEL"] = args.judge_model
    if args.enable_llm_app:
        apps.enable_optional_app("llm")

    if not os.environ.get("GEMINI_API_KEY") and os.environ.get("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

    api_key = resolve_api_key(
        args.model_name,
        os.environ.get("OPENAI_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY", ""),
    )
    if not api_key and detect_provider(args.model_name) in PROVIDERS_REQUIRING_KEY:
        raise RuntimeError(
            f"No API key set for model {args.model_name}. "
            "Set OPENAI_API_KEY or GEMINI_API_KEY/GOOGLE_API_KEY."
        )

    items = discover_subtasks(args.benchmark_dir)
    if args.language:
        items = [x for x in items if x[0] == args.language]
    if args.persona:
        items = [x for x in items if x[1] == args.persona]
    if args.subtask is not None:
        items = [x for x in items if x[2] == str(args.subtask)]
    if args.select:
        for token in args.select:
            items = [x for x in items if token in x[3]]
    if args.limit is not None:
        items = items[: args.limit]

    if not items:
        raise RuntimeError(f"No subtasks found under {args.benchmark_dir}")

    run_root = os.path.join(args.work_dir, f"{args.model_name.replace('/', '_')}_{args.tag}")
    os.makedirs(run_root, exist_ok=True)

    results = []
    for idx, (language, persona, subtask_id, config_path, testbed_dir) in enumerate(items):
        if args.set_language:
            language = args.set_language
        label = f"{language}/{persona}/{subtask_id}" if language else f"{persona}/{subtask_id}"
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        print(f"\n=== {label} ({idx + 1}/{len(items)}) ===")
        print(f"Task: {config.get('task', '')[:120]}")

        task_work_dir = os.path.join(run_root, language or "_", persona, subtask_id)
        os.makedirs(task_work_dir, exist_ok=True)

        is_pass = False
        eval_details = []
        stats = {}
        error = None
        preserved = None
        n_non_target_changes = None
        preservation = {}
        result_testbed_dir = None
        env = None
        cts = False
        t0 = time.time()
        try:
            result_testbed_dir, env, n_iter = run_single_task(
                config, testbed_dir, args.model_name, api_key,
                task_work_dir, args.max_iter, args.debug, args.temperature,
                args.max_obs_chars, args.enable_llm_app, args.max_history_chars
            )
            elapsed = time.time() - t0
            is_pass, eval_details = evaluate_task(config, result_testbed_dir, testbed_dir)
            stats = compute_task_stats(env, n_iter, args.max_iter, elapsed)
        except Exception as e:
            error = str(e)
            print(f"!!! Task error: {e}")
        finally:
            final_state = result_testbed_dir if result_testbed_dir and os.path.isdir(
                result_testbed_dir) else os.path.join(task_work_dir, "sandbox", "testbed")
            try:
                preservation = compute_preservation(testbed_dir, final_state, config)
                preserved = preservation["preserved"]
                n_non_target_changes = preservation["n_non_target_changes"]
            except PreservationUnscorable as e:
                preserved = False
                preservation = {"preserved": False, "unscorable": str(e)}
                print(f"!!! Preservation unscorable, counted as a violation: {e}")
            cts = bool(is_pass and preserved)
            if not cts and stats:
                print(f"  [diag] steps={stats['n_steps']} malformed={stats['n_malformed']} "
                      f"failed_exec={stats['n_failed_exec']} last_action={stats['last_action_type']} "
                      f"hit_max_iter={stats['hit_max_iter']} preserved={preserved} "
                      f"modified={preservation.get('n_modified')} "
                      f"deleted={preservation.get('n_deleted')} "
                      f"created={preservation.get('n_created')} acted={preservation.get('acted')}")
                for a, o in (env.history[-3:] if env is not None else []):
                    a_str = a.get("action") if isinstance(a, dict) else str(a)[:60]
                    print(f"  [diag] action={a_str} -> obs={str(o)[:90]}")
        print(f"Result: {'PASS' if is_pass else 'FAIL'}  |  CTS: {'YES' if cts else 'NO'}")

        results.append({
            "language": language,
            "persona": persona,
            "subtask_id": subtask_id,
            "task": config.get("task", "")[:200],
            "is_pass": is_pass,
            "cts": cts,
            "preserved": preserved,
            "n_non_target_changes": n_non_target_changes,
            "n_modified": preservation.get("n_modified"),
            "n_deleted": preservation.get("n_deleted"),
            "n_created": preservation.get("n_created"),
            "n_non_target": preservation.get("n_non_target"),
            "acted": preservation.get("acted"),
            "evaluations": eval_details,
            "n_evaluator_errors": sum(1 for d in eval_details if "error" in d),
            "error": error,
            **stats,
        })
        write_reports(run_root, results, len(items), args.model_name, args.tag, done=False)

    write_reports(run_root, results, len(items), args.model_name, args.tag, done=True)

    passed = sum(1 for r in results if r["is_pass"])
    cts_total = sum(1 for r in results if r.get("cts"))
    total = len(results)
    print("\n====================================")
    print(f"Model: {args.model_name} Tag: {args.tag}")

    by_lang = {}
    for r in results:
        by_lang.setdefault(r["language"], []).append(r)
    for lang, rs in sorted(by_lang.items()):
        c = sum(1 for r in rs if r.get("cts"))
        p = sum(1 for r in rs if r["is_pass"])
        print(f"  {lang or '(root)'}: CTS {c}/{len(rs)} = {c / len(rs) * 100:.1f}%  |  "
              f"Pass {p}/{len(rs)} = {p / len(rs) * 100:.1f}%")

    if total:
        print(f"CTS (headline): {cts_total}/{total} = {cts_total / total * 100:.2f}%")
        print(f"Pass rate:      {passed}/{total} = {passed / total * 100:.2f}%")
    else:
        print("No tasks run")
    print(f"Reports written to: {os.path.join(run_root, 'results.jsonl')} and report.md")


if __name__ == "__main__":
    main()
