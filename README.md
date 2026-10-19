# WorldBench

Reference implementation of **WorldBench**, a culturally grounded multilingual benchmark for
executable agentic tasks. The runner materialises a sandbox from a pre-built testbed, drives an
LLM agent through a structured action interface, and evaluates the resulting final state with
deterministic and LLM-as-a-judge functions.

It runs with OpenAI and Google Gemini models through their APIs, and with open-weight checkpoints
downloaded from the HuggingFace Hub and executed on GPU.

## Quick start

Requires Python 3.11 or later. `excel convert_to_pdf` and `word convert_to_pdf` call LibreOffice, so
`libreoffice` must be on the `PATH` for tasks that convert documents.

```bash
git clone <repository-url> && cd worldbench
pip install -r requirements.txt

export GEMINI_API_KEY=...
python validate_benchmark.py examples
python run_benchmark.py --benchmark_dir examples/EN-UK --tag smoke --limit 1
```

Put the task sets in `data/`, one folder per language–locale setting, then run the full benchmark
with `--benchmark_dir data`.

---

## Table of contents

1. [How it works](#how-it-works)
2. [Benchmark layout](#benchmark-layout)
3. [Task JSON](#task-json)
4. [Evaluation functions](#evaluation-functions)
5. [Tool environment](#tool-environment)
6. [Running](#running)
7. [Command-line flags](#command-line-flags)
8. [Outputs, metrics and reports](#outputs-metrics-and-reports)
9. [Execution settings](#execution-settings)
10. [Validating a task pool](#validating-a-task-pool)
11. [Troubleshooting](#troubleshooting)
12. [Project layout](#project-layout)

---

## How it works

The agent references files through a virtual `/testbed` mount point, which is rewritten to the
real sandbox path before each command runs. For each task the runner:

1. Reads the task JSON (`persona`, `task`, `required_files`, `evaluation`).
2. Copies the pre-built `testbed/` of that task into a fresh sandbox.
3. Runs the agent loop: the model emits one JSON action at a time; the environment executes it
   against the sandbox and returns an observation; this repeats until the agent emits
   `finish_task`, stagnates, or reaches the iteration cap.
4. Snapshots the resulting `testbed/` and runs the evaluation functions against it.
5. Compares the initial and final testbed to check preservation of non-target files.
6. Writes per-task results and an updated report after every task.

## Benchmark layout

The runner walks a directory tree; there is no single combined JSON file. Expected layout:

```
<benchmark_dir>/<SETTING>/<Persona_X>/subtasks/<id>.json
<benchmark_dir>/<SETTING>/<Persona_X>/testbed/data/...      # pre-built input files
```

Example: `EN-UK/Persona_1/subtasks/0.json` with inputs in `EN-UK/Persona_1/testbed/data/`.

The released task sets live under `data/`, which already holds the eight setting folders:
`EN-US`, `EN-UK`, `IT`, `PT`, `ES`, `FR`, `DE`, and `ZH`. Folder names are free, however: the
runner finds every `subtasks/*.json` that has a sibling `testbed/`, and the top-level folder name
becomes the language label used in the report. See `data/README.md` for the layout in full.

- Point `--benchmark_dir` at a single setting folder (e.g. `data/IT`) to run one set.
- Point it at a parent folder containing several settings to run them all; results are then broken
  down per language.
- If the input folder has no language level, use `--set_language` to assign a label.

## Task JSON

```json
{
  "persona": "Free-text persona description in the target language.",
  "task": "Native-language instruction for the agent.",
  "required_files": ["input_a.xlsx"],
  "evaluation": [
    {"function": "evaluate_contain",
     "args": {"doc_type": "docx", "file": "./data/out.docx", "keywords": ["..."]}}
  ]
}
```

Tasks may also list `target_files` and `output_files` (path strings or `{"path": ...}` objects) for
files the agent is allowed to create or change beyond those named in the evaluators; both are
optional.

The `testbed/` directory is already materialised on disk, so the runner copies it into the sandbox
as is. `file` paths in `evaluation.args` are relative to `testbed/`, for example `./data/out.docx`
or `./calendar/Anika.ics`.

## Evaluation functions

Deterministic:

| Function | What it checks |
| --- | --- |
| `evaluate_contain` | Required text appears in a target file. |
| `evaluate_not_contain` | Prohibited text is absent from a target file. |
| `evaluate_excel_cell_value` | A spreadsheet cell holds the expected value (exact comparison). |
| `evaluate_file_exist` | A required output file has been created. |
| `evaluate_calendar_event` | One calendar event carries every expected property. |
| `evaluate_calendar_no_overlap` | Calendar events satisfy a non-overlap constraint. |
| `evaluate_sent_email` | A new email was delivered to the recipient and contains the expected text. |

LLM-as-a-judge:

| Function | What it checks |
| --- | --- |
| `evaluate_email` | A generated email satisfies the rubric. |
| `evaluate_note` | A generated note satisfies the rubric. |

An empty `evaluation` list, empty `keywords`, or empty `matches` is rejected instead of passing.

### Text matching

`evaluate_contain`, `evaluate_not_contain`, the keyword pre-check of the judge functions, and the
text properties of `evaluate_calendar_event` and `evaluate_sent_email` share one matcher
(`evaluation/matching.py`). Matching is deterministic: no fuzzy, semantic, or LLM-based comparison.

- Text is normalised with Unicode NFKC and case folding. Capitalisation, repeated whitespace,
  Unicode dashes, apostrophes, and punctuation between words are ignored.
- A keyword is split into tokens that must appear in the same order, in the same text field (a
  line, a table cell, or an event property), with at most 40 characters between consecutive tokens.
- Tokens match whole words. `register` does not match `registered`.
- Numbers, dates, times, and identifiers are single tokens that must match exactly: `1250` matches
  `1,250` but not `12500`, `INV-1250`, or `1250.5`; `2026-03-19` does not match `2026 03 19`.
  Symbols such as `%` and `€` are kept as tokens.
- Chinese and Japanese runs have no word boundaries, so they match as contiguous substrings.
- For PDFs, line breaks do not separate fields.

### Calendar events

```json
{"function": "evaluate_calendar_event",
 "args": {"file": "./data/planner.ics", "summary_keywords": ["Site visit"],
          "description_keywords": ["road closure"], "date": "2026-03-19",
          "time_start": "2026-03-19 09:00:00", "time_end": "2026-03-19 11:00:00"}}
```

`file` names the calendar; `username` instead resolves to `calendar/<username>.ics` or
`data/<username>.ics`. At least one of `summary_keywords`, `description_keywords`,
`location_keywords`, `date`, `time_start`, `time_end` is required, and every given property must
hold for the same event. Keywords use the text matcher on that property. Dates and times are
compared exactly after converting to `timezone` (optional, default UTC; naive times are read in
that zone). `time_start` and `time_end` take a full ISO datetime, or a bare time combined with
`date`. The calendar app edits an existing `data/<name>.ics` when the agent passes `<name>` as the
user and no `calendar/<name>.ics` exists.

### Sent emails

```json
{"function": "evaluate_sent_email",
 "args": {"recipient": "bob@nhs.uk", "subject_keywords": ["attendance"],
          "keywords": ["Friday screening"]}}
```

The email must be new relative to the initial testbed and present in the recipient's mailbox
(`emails/<local part>/`), addressed to `recipient` (a full address, or a local part), and every
keyword must occur in that same message. `keywords` are matched against subject and body,
`subject_keywords` against the subject only. Messages present in the initial testbed, and drafts
that were never delivered to the recipient's mailbox, cannot satisfy the task. `evaluate_email`
remains the judge-based check for free-form email files.

### Judge

Judge args schema: `{"file": ..., "doc_type": ..., "keywords": [...], "criteria": "..."}`. Any
non-empty `keywords` are checked first with the text matcher; the surviving text is then passed to
the judge together with `criteria`, falling back to the default rubric of the function when no
criteria are declared. The judge reads `OPENAI_API_KEY` or `GEMINI_API_KEY` and uses `JUDGE_MODEL`
(default `gemini-3.5-flash`), at temperature 0 with an output cap of 10 tokens. The verdict must be
exactly `YES` or `NO`; any other response is recorded as an evaluator error rather than a pass or
a fail. The judge is pinned: an unroutable name, or one whose provider key is missing, raises an
error rather than falling back to another model, because agents scored by different judges cannot
be compared. The judge actually used is written into the report header.

## Tool environment

Six tool families are exposed through a single JSON action format: `excel` (spreadsheets), `word`
(documents), `pdf`, `email` (messaging), `calendar`, and `shell`. The agent switches family with
`{"app": "system", "action": "switch_app", "target_app": "..."}` and terminates with
`{"app": "system", "action": "finish_task", "answer": "..."}`.

Only the active family accepts actions. An action addressed to a family the agent has not switched
to is rejected with an explanatory observation and leaves the sandbox unchanged, so every
trajectory pays the same cost for moving between families.

A seventh family, `llm`, lets the agent delegate to a hosted model. It is **off by default**,
because an open-weight agent that outsources the reasoning to GPT or Gemini is no longer being
measured; enable it with `--enable_llm_app` if a study needs it. The apps active in a run are
recorded in the report header alongside the judge model.

### Isolation

The sandbox is a working directory, not a security boundary. The `shell` app runs real commands as
the current user, and a command naming an absolute path outside `/testbed` reaches the host
filesystem. The subprocess environment is scrubbed, so API keys are not reachable from agent
commands, and no credentials are written into the sandbox unless `--enable_llm_app` is passed.
Even so, run untrusted models inside a disposable container.

## Running

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...          # OpenAI agent or judge
export GEMINI_API_KEY=...             # Gemini agent or judge (GOOGLE_API_KEY also accepted)

# Full EN-UK set with a Gemini agent
python run_benchmark.py \
  --benchmark_dir examples/EN-UK \
  --model_name gemini-3.5-flash \
  --tag my_run \
  --max_iter 30

# First 5 tasks of one setting, forcing the language label
python run_benchmark.py --benchmark_dir data/IT --model_name gemini-3.5-flash \
  --tag it --set_language IT --limit 5 --max_iter 30

# All eight settings, broken down per language
python run_benchmark.py --benchmark_dir data --model_name gemini-3.5-flash --tag all
```

### Open-weight checkpoints on GPU

Install the extra dependencies once, then pass a HuggingFace repo id as `--model_name`. The
checkpoint is downloaded from the Hub and run in process on the available GPUs; no API key is
required.

```bash
pip install -r requirements-hf.txt
export HF_TOKEN=hf_...                # only for gated repos such as meta-llama/*

python run_benchmark.py \
  --benchmark_dir examples/EN-UK \
  --model_name meta-llama/Meta-Llama-3-8B-Instruct \
  --tag llama8b

python run_benchmark.py \
  --benchmark_dir examples/EN-UK \
  --model_name meta-llama/Llama-3.3-70B-Instruct \
  --tag llama70b
```

The model is loaded once per run and reused across every task. Generation is greedy by default,
which makes the open-weight runs deterministic; `--temperature` switches to sampling. Prompts are
rendered with the tokenizer's own chat template, so each checkpoint sees the format it was trained
on, and generation stops on the turn-level terminator (`<|eot_id|>` for Llama-3 style models)
alongside the end-of-text token. The Hub token is read from the environment and never stored in
the repository. Loading is controlled by environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HF_TOKEN` | unset | Hub token, required for gated repositories. |
| `HF_DTYPE` | `bfloat16` | `bfloat16`, `float16`, `float32`, or `auto`. |
| `HF_DEVICE_MAP` | `auto` | Passed to `from_pretrained`; `auto` shards across all visible GPUs. |
| `HF_LOAD_IN_4BIT` | unset | Set to `1` for NF4 quantisation with double quantisation, which fits a 70B checkpoint on two 48 GB cards. |
| `HF_MAX_NEW_TOKENS` | `1024` | Generation budget per action. |
| `HF_TOP_P` | `0.9` | Nucleus sampling, used only when `--temperature` is above 0. |
| `CUDA_VISIBLE_DEVICES` | unset | Standard GPU selection. |

A checkpoint already served behind an OpenAI-compatible endpoint, for instance by vLLM, is reached
instead with a `local/` prefix and `LOCAL_API_BASE`:

```bash
export LOCAL_API_BASE=http://localhost:8000/v1
python run_benchmark.py --benchmark_dir examples/EN-UK \
  --model_name local/meta-llama/Llama-3.3-70B-Instruct --tag llama70b_vllm
```

**Model names.** The backend is chosen from the model string: names containing `gpt`, `o1`, `o3`,
or `o4` use OpenAI; names containing `gemini` use Google; a `local/` prefix uses `LOCAL_API_BASE`;
anything else is treated as a HuggingFace repo id and loaded on GPU. An `hf/` prefix forces the
HuggingFace backend. Use a current hosted model name; retired models such as `gemini-2.0-flash`
return HTTP 404.

**API keys.** The runner reads `OPENAI_API_KEY` and `GEMINI_API_KEY` (or `GOOGLE_API_KEY` as an
alias); both may be set at once and the right one is picked from the model. HuggingFace
checkpoints need no key, only `HF_TOKEN` for gated repositories.

## Command-line flags

| Flag | Meaning |
| --- | --- |
| `--benchmark_dir` | Root folder to scan (required). |
| `--model_name` | Agent model (`gpt-*`, `gemini-*`, a HuggingFace repo id, or `local/*`). Default `gemini-3.5-flash`. |
| `--tag` | Run label; output goes to `runs/<model>_<tag>/`. |
| `--max_iter` | Iteration cap per task (default 30). |
| `--temperature` | Override the agent decoding temperature for every provider. |
| `--max_obs_chars` | Truncate each observation to N characters (0, the default, disables it). |
| `--max_history_chars` | Budget for the execution history replayed in each prompt (default 24000). |
| `--enable_llm_app` | Expose the `llm` app; off by default. |
| `--judge_model` | Override the LLM-judge model. |
| `--set_language` | Force the language label for all tasks in the run. |
| `--language` | Run only tasks under this top-level folder. |
| `--persona` | Run only tasks for this second-level folder. |
| `--subtask` | Run only subtasks with this id. |
| `--select TOKEN` | Run only tasks whose path contains TOKEN; repeatable (AND). |
| `--limit N` | Run only the first N subtasks, after the other filters. |
| `--debug` | Print prompts, responses, and step-by-step actions. |

## Outputs, metrics and reports

Each run writes to `runs/<model>_<tag>/`:

- `results.jsonl` — one JSON line per task, rewritten atomically after every task so partial
  progress is never lost if the run is interrupted.
- `report.md` — a human-readable report, also updated after every task, with a
  `Status: IN PROGRESS / COMPLETED` header and the headline metric on top.
- `<lang>/<persona>/<id>/output/` — per-task `testbed` snapshot plus `env_history.json` and
  `llm_history.json`, which hold the full agent trajectory.

During execution the runner prints a live per-step log
(`[step k/max] <action_type> -> <observation>`).

### Headline metric: CTS

**CTS (Constrained Task Success)** is the primary metric. For a task `t`, `pass(t)` is true when
all task-specific evaluation functions succeed on the final sandbox state, and `preserve(t)` is
true when every pre-existing non-target file remains present and byte-identical. Then

```
CTS(t) = pass(t) AND preserve(t)
```

Target files are the paths declared in the evaluator arguments, plus the optional `target_files`
and `output_files` lists of the task (each entry a path string or an object with a `path`); every
other pre-existing file must remain untouched. The preservation check is binary and operates at file level, so any modification to a
non-target file causes failure, while changes within target files are assessed by the
task-specific evaluators. Files created during the run, such as the `.eml` artefacts written by
`send_email`, are new artefacts and do not count against preservation.

### Secondary metrics

- **Pass rate** — task evaluators only.
- **Preservation rate** — fraction of tasks where no non-target file was modified.
- **Mean steps on passed tasks**, malformed actions per task, iteration-cap hits, and clean
  termination rate.

Per-task fields in `results.jsonl`:

| Field | Meaning |
| --- | --- |
| `is_pass` | Whether all evaluation functions passed. |
| `cts` | Correct and non-target files preserved (headline metric). |
| `preserved` | Whether all non-target files were left unchanged. |
| `n_non_target_changes` | Count of non-target files modified or deleted. |
| `evaluations` | Per-function pass/fail detail; an `error` entry marks an evaluator error rather than a task failure. |
| `n_evaluator_errors` | Number of evaluation functions that raised instead of returning a verdict. |
| `n_steps` | Number of agent steps taken. |
| `n_malformed` | Actions that failed to parse or execute. |
| `n_failed_exec` | Commands that ran but reported failure. |
| `apps_used` | Which tool families the agent used, and how often. |
| `last_action_type` | The final action type, for example `finish_task`. |
| `hit_max_iter` | True if the agent ran out of steps without finishing. |
| `finished_cleanly` | True if the agent emitted `finish_task`. |
| `elapsed_sec` | Wall-clock time for the task. |
| `error` | Runtime error string, if any. |

`report.md` leads with the CTS headline, then secondary metrics, aggregate stats, and breakdowns
by language, by persona, and by evaluation function, plus a per-task table.

## Execution settings

| Parameter | Value |
| --- | --- |
| Iteration cap | 30 steps per task (`--max_iter`). |
| Actions per step | Exactly one JSON action object. |
| Agent decoding | 1024 max tokens. Temperature defaults to 0 for OpenAI, HuggingFace, and local endpoints, and to 1.0 for Gemini, following each provider's documented default; `--temperature` overrides all of them. |
| Judge decoding | Temperature 0, 10 max tokens. |
| Malformed-action retry | One retry with an explicit JSON reminder. |
| Stagnation detection | 5 identical consecutive actions trigger `got_stuck`. |
| Sandbox | Fresh copy of the testbed per task. |
| Result writing | Atomic writes after every task. |

## Tests

```bash
pip install pytest && python -m pytest -q tests
```

The suite pins the behaviour of the evaluators, the action quoting, the preservation check, and
the sandbox environment. CI runs it on every push together with the validator.

## Validating a task pool

```bash
python validate_benchmark.py data
```

The validator reads the configurations and the pristine testbeds and calls no model. It reports
missing fields, unknown evaluation functions, malformed evaluator arguments, `doc_type` values
that disagree with the file extension, `required_files` absent from the testbed, and tasks whose
initial state already satisfies every evaluator, which would hand the agent a free pass. It exits
non-zero when anything is wrong, so it drops straight into CI. Run it after every regeneration of
the task pool.

## Troubleshooting

**The report says `IN PROGRESS` and only a few tasks ran.** The run was interrupted before
finishing. The partial `results.jsonl` and `report.md` are still valid; re-run to continue.

**A task loops to the iteration cap with many malformed actions.** Inspect `env_history.json` for
that task to see the raw model output. The action parser accepts JSON, Python literals, and
newline-sanitised variants, so persistent malformed actions usually indicate a model that does not
follow the single-action schema.

**`File not exist` on a messaging task.** Some tasks expect a `.txt` file under `data/`, while the
agent may instead use the `email` app, which creates an `.eml` under `emails/`. Check that the
task's `evaluation.file` matches the artefact the instruction actually asks for.

**HTTP 404 from the provider.** The model was retired; use a current name.

**Low pass rate.** Remaining failures generally reflect model limitations: the agent stops early,
writes to the wrong artefact, or misses a required keyword. Use the per-task diagnostics and
`llm_history.json` to separate a weak model from a task or evaluator mismatch.

## Project layout

```
worldbench/
  run_benchmark.py          # discover -> copy testbed -> agent loop -> evaluate -> report
  validate_benchmark.py     # offline integrity check over a task pool
  tests/                    # test suite
  requirements-hf.txt       # extra dependencies for the GPU backend
  apps/                     # tool families, single JSON action format
  core/
    sandbox.py              # local filesystem sandbox
    env.py                  # agent environment, action parser, failure counters
    policy.py               # LLM policy, persona-grounded system message
    llm.py                  # agent backends: OpenAI, Google, HuggingFace on GPU, local endpoint
  evaluation/
    evaluators.py           # deterministic and LLM-as-a-judge evaluation functions
    matching.py             # deterministic text matcher shared by the evaluators
  data/                     # task sets, one folder per language-locale setting
    EN-US/  EN-UK/  IT/  PT/  ES/  FR/  DE/  ZH/
  examples/
    EN-UK/                  # small runnable sample (Persona_1..4, pre-built testbeds)
```
