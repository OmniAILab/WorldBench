# WorldBench

WorldBench is a multilingual benchmark for evaluating LLM agents on culturally grounded everyday tasks. It contains 1,600 tasks across seven languages and eight cultural settings. Each task is situated in the working context of a persona and requires an agent to interact with a sandbox containing documents, spreadsheets, PDFs, emails, calendars and related files. Tasks are constructed in their target language and are reviewed by annotators familiar with the relevant language and cultural context.

WorldBench evaluates both task completion and the changes an agent makes to its environment. An agent may complete the requested task while also modifying files that should have remained untouched. We therefore use **Constrained Task Success (CTS)**, which requires all task-specific evaluation functions to pass and all pre-existing non-target files to be preserved. 

The repository contains the benchmark runner, evaluation functions and testbed format. It supports OpenAI and Gemini models through their APIs, open-weight models from Hugging Face, and models served through OpenAI-compatible local endpoints.

**Paper:** [WorldBench: Culturally Grounded Benchmark for Multilingual Agents](https://arxiv.org/abs/2609.01056) (Ranaldi et al., 2026).

## Quick start

Requires **Python 3.11+** and a model-provider API key for hosted-agent runs. Install `libreoffice` and
make it available on `PATH` for tasks involving document-to-PDF conversion.

From the repository root:

```bash
python -m pip install -r requirements.txt
export GEMINI_API_KEY="<your-api-key>"

python run_benchmark.py \
  --benchmark_dir examples/EN-UK \
  --model_name gemini-3.5-flash \
  --tag smoke \
  --limit 1
```

This runs one of the included EN-UK example tasks. Results are saved under `runs/<model>_<tag>/`. To run a
complete locale, use `--benchmark_dir data/IT`; to run all eight settings, use `--benchmark_dir data`. See
[Running](#running) for other backends and options.


## Contents

- [How it works](#how-it-works)
- [Benchmark layout](#benchmark-layout) · [Task JSON](#task-json)
- [Evaluation functions](#evaluation-functions) · [Tool environment](#tool-environment)
- [Running](#running) · [Command-line flags](#command-line-flags)
- [Outputs and metrics](#outputs-and-metrics) · [Execution settings](#execution-settings)
- [Tests](#tests) · [Task generation](#task-generation)
- [Troubleshooting](#troubleshooting) · [Project layout](#project-layout)
- [Citation](#citation)

## How it works

The agent is given a sandbox path. This path is translated to the actual working directory before commands execute. For every task the runner:

1. reads the task JSON (`persona`, `task`, `required_files`, `evaluation`);
2. copies the persona's pre-built `testbed/` into a fresh sandbox, hence, each sub-task starts from the same state;
3. runs the agent loop: the model returns one JSON action, the environment executes it and returns an    observation, and this repeats until the agent calls `finish_task`, gets stuck, or hits the iteration cap;
4. snapshots the final `testbed/` and runs the evaluation functions on it;
5. compares the initial and final testbed to see whether files that were not targets were left alone;
6. writes the result and refreshes the report after each task.

## Benchmark layout

The runner walks a directory tree. There is no single combined JSON file.

```
<benchmark_dir>/<SETTING>/<Persona_N>/subtasks/<id>.json
<benchmark_dir>/<SETTING>/<Persona_N>/testbed/data/         # input files
<benchmark_dir>/<SETTING>/<Persona_N>/testbed/emails/       # mailboxes, one folder per local part
<benchmark_dir>/<SETTING>/<Persona_N>/testbed/calendar/     # <name>.ics
```

Each persona has a shared initial testbed and usually three sub-tasks; their IDs need not be consecutive.
Every sub-task receives its own fresh copy, so changes made during one run do not affect another. For
example, `EN-UK/Persona_1/subtasks/0.json` reads its inputs from `EN-UK/Persona_1/testbed/data/`.

The documented `data/` release uses eight locale folders: `EN-US`, `EN-UK`, `IT`, `PT`, `ES`, `FR`, `DE`, `ZH`. The names are not enforced. The runner picks up any `subtasks/*.json` that has a `testbed/` next to it, and the top-level folder name becomes the language label in the report. 

- To run one setting, point `--benchmark_dir` at it (for instance `data/IT`).
- To run several, point it at their parent folder. The report then splits results by language.
- If there is no language level in the folder structure, use `--set_language`.

## Task JSON

```json
{
  "persona": "A persona description in the target language.",
  "task": "A task instruction in the target language.",
  "required_files": ["input_a.xlsx"],
  "evaluation": [
    {"function": "evaluate_contain",
     "args": {"doc_type": "docx", "file": "./data/out.docx", "keywords": ["required phrase"]}}
  ]
}
```

A task can also declare `target_files` and `output_files` (strings or `{"path": ...}` objects). These are
files the agent may create or change in addition to the ones named in the evaluators. Both are optional.

Paths in `evaluation.args` are relative to the persona's `testbed/`, for example `./data/out.docx` or
`./calendar/Anika.ics`.

The benchmark was produced from persona, scenario and application/constraint seeds, expanded into
executable environments and reviewed for realism, cultural appropriateness, solvability and file adequacy.
The separate generation pipeline is described in [Task generation](#task-generation); running the
benchmark does not require it.

## Evaluation functions

Deterministic evaluators:

| Function | What it checks |
| --- | --- |
| `evaluate_contain` | Required text appears in a target file. |
| `evaluate_not_contain` | Forbidden text is absent from a target file. |
| `evaluate_excel_cell_value` | A spreadsheet cell holds the expected value (exact comparison). |
| `evaluate_file_exist` | An output file has been created. |
| `evaluate_calendar_event` | One calendar event has all the expected properties. |
| `evaluate_calendar_no_overlap` | Calendar events satisfy a non-overlap constraint. |
| `evaluate_sent_email` | A new email reached the recipient and contains the expected text. |

LLM-judged evaluators:

| Function | What it checks |
| --- | --- |
| `evaluate_email` | A generated email meets the rubric. |
| `evaluate_note` | A generated note meets the rubric. |

Empty `evaluation` lists and empty required `keywords` or `matches` are rejected rather than treated as
passes.

### Text matching

`evaluate_contain`, `evaluate_not_contain`, the keyword pre-check of the judge functions, and the text
fields of `evaluate_calendar_event` and `evaluate_sent_email` all use the same matcher
(`evaluation/matching.py`). 


### Calendar events

```json
{"function": "evaluate_calendar_event",
 "args": {"file": "./data/planner.ics", "summary_keywords": ["Site visit"],
          "description_keywords": ["road closure"], "date": "2026-03-19",
          "time_start": "2026-03-19 09:00:00", "time_end": "2026-03-19 11:00:00"}}
```

`file` names the calendar. You can pass `username` instead, which resolves to `calendar/<username>.ics` or
`data/<username>.ics`. At least one of `summary_keywords`, `description_keywords`, `location_keywords`,
`date`, `time_start`, `time_end` is required, and all the given properties must hold for the same event.
Keywords go through the text matcher. Dates and times are compared exactly after conversion to `timezone`
(optional, UTC by default; naive times are read in that zone). `time_start` and `time_end` take a full ISO
datetime, or a bare time combined with `date`. If the agent passes `<name>` as the user and there is no
`calendar/<name>.ics`, the calendar app edits `data/<name>.ics` instead.

### Sent emails

```json
{"function": "evaluate_sent_email",
 "args": {"recipient": "bob@nhs.uk", "subject_keywords": ["attendance"],
          "keywords": ["Friday screening"]}}
```

The email has to be new compared with the initial testbed and sit in the recipient's mailbox
(`emails/<local part>/`). `recipient` is a full address or a local part. Every keyword must occur in the
same message: `keywords` are matched against subject and body, `subject_keywords` against the subject
only. Emails that were already in the testbed, and drafts that never reached the recipient's mailbox,
don't count. For free-form email files there is `evaluate_email`, which uses the judge.

### Judge

Args: `{"file": ..., "doc_type": ..., "keywords": [...], "criteria": "..."}`. If `keywords` is non-empty
it is checked first with the text matcher. The surviving text is then sent to the judge with `criteria`,
or with the function's default rubric if there are none. The judge reads `OPENAI_API_KEY` or
`GEMINI_API_KEY` and uses `JUDGE_MODEL` (default `gemini-3.5-flash`), at temperature 0 and with a 10-token
output cap. The answer has to be exactly `YES` or `NO`. Anything else is recorded as an evaluator error,
not as a pass or a fail.

The judge model is fixed for the run. If the model name cannot be routed, or its provider key is missing, the run raises an
error and does not fall back to another model, to keep evaluation consistent across runs. The judge that was actually used is printed in the report header.

## Tool environment

The agent has six tool families behind one JSON action format: `excel`, `word`, `pdf`, `email`, `calendar`
and `shell`. It changes family with `{"app": "system", "action": "switch_app", "target_app": "..."}` and
ends with `{"app": "system", "action": "finish_task", "answer": "..."}`.

Only the active family accepts actions. If the agent addresses a family it has not switched to, the action
is rejected with an explanation and the sandbox stays untouched. Switches are explicit actions and count
towards the trajectory.

There is also a seventh family, `llm`, which lets the agent delegate to a hosted model. It is off by
default: an open-weight agent that hands the reasoning to GPT or Gemini is no longer what we want to
measure. Turn it on with `--enable_llm_app` if a study needs it. The active apps are listed in the report
header next to the judge.

### Isolation

The sandbox is a working directory, **not a security boundary**. The `shell` app runs real commands as the current user, and a command that names an absolute path outside `/testbed` can reach the host. The subprocess environment is scrubbed, so API keys are not visible to agent commands, and no credentials are written into the sandbox unless `--enable_llm_app` is passed. Still, run untrusted models in a disposable container.

## Running

```bash
python -m pip install -r requirements.txt
export OPENAI_API_KEY="<your-openai-api-key>"          # OpenAI agent or judge
export GEMINI_API_KEY="<your-gemini-api-key>"             # Gemini agent or judge (GOOGLE_API_KEY also works)

# EN-UK examples with a Gemini agent
python run_benchmark.py \
  --benchmark_dir examples/EN-UK \
  --model_name gemini-3.5-flash \
  --tag my_run \
  --max_iter 30

# First 5 tasks of one setting, forcing the language label
python run_benchmark.py --benchmark_dir data/IT --model_name gemini-3.5-flash \
  --tag it --set_language IT --limit 5 --max_iter 30

# All eight settings, results split per language
python run_benchmark.py --benchmark_dir data --model_name gemini-3.5-flash --tag all
```

### Open-weight checkpoints on GPU

Install the extra dependencies once, then pass a HuggingFace repo id as `--model_name`. The checkpoint is
downloaded from the Hub and run in process on the visible GPUs. No API key is needed for **local agent inference**. Tasks using LLM-judged evaluation still require a supported judge provider and API key.

```bash
python -m pip install -r requirements-hf.txt
export HF_TOKEN="<your-hf-token>"                # only for gated repos such as meta-llama/*

python run_benchmark.py \
  --benchmark_dir examples/EN-UK \
  --model_name meta-llama/Meta-Llama-3-8B-Instruct \
  --tag llama8b
```

The model is loaded once per run and reused for all tasks. Generation is greedy unless you pass
`--temperature`, so open-weight runs are deterministic by default. Prompts use the tokenizer's own chat
template, and generation stops on the turn terminator (`<|eot_id|>` for Llama-3 style models) as well as
on end-of-text. Authentication tokens are read from the environment rather than stored in the repository.
These variables control loading:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HF_TOKEN` | unset | Hub token, needed for gated repos. |
| `HF_DTYPE` | `bfloat16` | `bfloat16`, `float16`, `float32` or `auto`. |
| `HF_DEVICE_MAP` | `auto` | Passed to `from_pretrained`; `auto` shards over all visible GPUs. |
| `HF_LOAD_IN_4BIT` | unset | Set to `1` for NF4 quantisation with double quantisation (a 70B fits on two 48 GB cards). |
| `HF_MAX_NEW_TOKENS` | `1024` | Generation budget per action. |
| `HF_TOP_P` | `0.9` | Nucleus sampling, only used when `--temperature` is above 0. |
| `CUDA_VISIBLE_DEVICES` | unset | Standard GPU selection. |

If the checkpoint is already served behind an OpenAI-compatible endpoint (vLLM, for instance), use the
`local/` prefix and `LOCAL_API_BASE`:

```bash
export LOCAL_API_BASE=http://localhost:8000/v1
python run_benchmark.py --benchmark_dir examples/EN-UK \
  --model_name local/meta-llama/Llama-3.3-70B-Instruct --tag llama70b_vllm
```

Model names decide the backend. A name containing `gpt`, `o1`, `o3` or `o4` goes to OpenAI, one containing
`gemini` goes to Google, a `local/` prefix goes to `LOCAL_API_BASE`, and anything else is treated as a
HuggingFace repo id. An `hf/` prefix forces HuggingFace. Use a currently available hosted model name;
retired model IDs may return HTTP 404.

The runner reads `OPENAI_API_KEY` and `GEMINI_API_KEY` (`GOOGLE_API_KEY` works as an alias). You can set
both and the right one is chosen from the model.

## Command-line flags

| Flag | Meaning |
| --- | --- |
| `--benchmark_dir` | Root folder to scan (required). |
| `--model_name` | Agent model: `gpt-*`, `gemini-*`, a HuggingFace repo id or `local/*`. Default `gemini-3.5-flash`. |
| `--tag` | Run label. Output goes to `runs/<model>_<tag>/`. |
| `--max_iter` | Iteration cap per task (default 30). |
| `--temperature` | Override the agent temperature for every provider. |
| `--max_obs_chars` | Truncate each observation to N characters (0, the default, means no limit). |
| `--max_history_chars` | Budget for the history replayed in each prompt (default 24000). |
| `--enable_llm_app` | Expose the `llm` app (off by default). |
| `--judge_model` | Override the judge model. |
| `--set_language` | Force the language label for every task in the run. |
| `--language` | Only run tasks under this top-level folder. |
| `--persona` | Only run tasks of this second-level folder. |
| `--subtask` | Only run sub-tasks with this id. |
| `--select TOKEN` | Only run tasks whose path contains TOKEN; repeatable (AND). |
| `--limit N` | Run only the first N sub-tasks, after the other filters. |
| `--debug` | Print prompts, responses and step-by-step actions. |

## Outputs and metrics

Each run writes to `runs/<model>_<tag>/`:

- `results.jsonl`: one line per task, rewritten atomically after every task, so nothing is lost if the run
  is interrupted.
- `report.md`: a human-readable report, also refreshed after each task, with a
  `Status: IN PROGRESS / COMPLETED` line and CTS at the top.
- `<lang>/<persona>/<id>/output/`: the final `testbed` snapshot, plus `env_history.json` and
  `llm_history.json` with the full trajectory.

While it runs, the runner prints a live log: `[step k/max] <action_type> -> <observation>`.

### Constrained Task Success (CTS)

Constrained Task Success (CTS) is the main evaluation metric. For a task `t`, `pass(t)` holds when every
evaluation function succeeds on the final state, and `preserve(t)` holds when every non-target file that
existed at the start is still there and byte-identical.

```
CTS(t) = pass(t) AND preserve(t)
```

Target files are the paths named in the evaluator args plus the optional `target_files` and `output_files`
of the task. Every other pre-existing file must stay untouched. The check is binary and per file:
modifying a non-target file fails the task, while changes inside target files are left to the evaluators.
New files created during a run (for example, an `.eml` file produced by `send_email`) do not count as
modifications of *pre-existing* non-target files.


## Execution settings

- Iteration cap: 30 steps per task (`--max_iter`).
- One JSON action per step.
- Agent decoding: 1024 max tokens. Temperature is 0 for OpenAI, HuggingFace and local endpoints and 1.0
  for Gemini (each provider's own default). `--temperature` overrides all of them.
- Judge decoding: temperature 0, 10 max tokens.
- A malformed action gets one retry with an explicit JSON reminder.
- Five identical consecutive actions trigger `got_stuck`.
- Fresh copy of the testbed for every task.
- Results are written atomically after every task.

## Tests

```bash
python -m pip install pytest && python -m pytest -q tests
```

The tests cover the evaluators, action quoting, the preservation check and the sandbox. CI runs them on
every push.


## Project layout

```
worldbench/
  run_benchmark.py          # discover -> copy testbed -> agent loop -> evaluate -> report
  requirements.txt          # core dependencies
  requirements-hf.txt       # extra dependencies for the GPU backend
  tests/                    # test suite
  .github/workflows/ci.yml  # CI: compile, pyflakes, import check, pytest
  apps/                     # tool families, one JSON action format
  core/
    sandbox.py              # local filesystem sandbox
    env.py                  # agent environment, action parser, failure counters
    policy.py               # LLM policy, persona-grounded system message
    llm.py                  # backends: OpenAI, Google, HuggingFace on GPU, local endpoint
  evaluation/
    evaluators.py           # deterministic and judge-based evaluation functions
    matching.py             # text matcher shared by the evaluators
  data/                     # task sets, one folder per setting (see data/README.md)
    EN-US/  EN-UK/  IT/  PT/  ES/  FR/  DE/  ZH/
  examples/
    EN-UK/                  # small runnable sample (Persona_1..4, pre-built testbeds)
```

## Citation

If you use WorldBench in your research, please cite:

```bibtex
@misc{ranaldi2026worldbench,
  title         = {WorldBench: Culturally Grounded Benchmark for Multilingual Agents},
  author        = {Ranaldi, Leonardo and Shen, Sherrie and Kai, Jushi and Birch, Alexandra},
  year          = {2026},
  eprint        = {2609.01056},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  url           = {https://arxiv.org/abs/2609.01056}
}
```

