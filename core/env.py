import json
import re
import ast
import logging

import apps
from core.sandbox import LocalSandbox

ACTION_EXEC = "action_executed"


def extract_objects(text):
    """Balanced-brace candidates in the order they appear."""
    objects = []
    depth = 0
    start = None
    in_string = False
    quote = None
    prev = ""
    for i, ch in enumerate(text):
        if in_string:
            if ch == quote and prev != "\\":
                in_string = False
        elif ch in ("'", '"') and depth:
            in_string = True
            quote = ch
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(text[start:i + 1])
                    start = None
        prev = ch
    return objects


def parse_action(action_string):
    """Parse an action object from possibly messy model output."""
    text = action_string.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?", "", text).strip()

    candidates = [c for c in reversed(extract_objects(text))
                  if '"action"' in c or "'action'" in c]
    if not candidates:
        candidates = list(reversed(extract_objects(text)))
    if not candidates:
        candidates = [text]

    for candidate in candidates:
        try:
            return _parse_candidate(candidate)
        except Exception:
            continue
    return _parse_candidate(candidates[0])


def _parse_candidate(candidate):

    try:
        return json.loads(candidate)
    except Exception:
        pass

    try:
        return ast.literal_eval(candidate)
    except Exception:
        pass

    sanitized = _escape_newlines_in_strings(candidate)
    try:
        return json.loads(sanitized)
    except Exception:
        pass
    return ast.literal_eval(sanitized)


def _escape_newlines_in_strings(s):
    out = []
    in_string = False
    quote = None
    prev = ""
    for ch in s:
        if in_string:
            if ch == "\n":
                out.append("\\n")
                prev = ch
                continue
            if ch == "\r":
                out.append("\\r")
                prev = ch
                continue
            if ch == quote and prev != "\\":
                in_string = False
                quote = None
            out.append(ch)
        else:
            if ch in ("'", '"'):
                in_string = True
                quote = ch
            out.append(ch)
        prev = ch
    return "".join(out)


class WorldBenchEnv:
    """Agent environment backed by a local filesystem sandbox."""

    name = "worldbench_local"

    def __init__(self, sandbox: LocalSandbox, task="<undefined>", verbose=True, max_obs_chars=0,
                 require_active_app=True):
        self.sandbox = sandbox
        self.max_obs_chars = max_obs_chars
        self.require_active_app = require_active_app
        self.task = task
        self.current_app = None
        self.available_apps = apps.AVAILABLE_APPS
        self.history = []
        self.observation = None
        self.info = {}
        self.last_action_type = None
        self.answer = None
        self.n_malformed = 0
        self.n_protocol_violations = 0
        self.n_failed_exec = 0
        self.logger = logging.getLogger(__name__)
        if not verbose:
            self.logger.disabled = True

    def reset(self):
        self.history = []
        self.observation = None
        self.info = {}
        self.current_app = None
        self.last_action_type = None
        self.answer = None
        self.n_malformed = 0
        self.n_protocol_violations = 0
        self.n_failed_exec = 0
        self.sandbox.workdir = self.sandbox.VIRTUAL_TESTBED

    def get_available_actions(self):
        return list(apps.AVAILABLE_ACTIONS[self.current_app].keys())

    def check_valid_action(self, action: dict) -> bool:
        return isinstance(action, dict) and "app" in action and "action" in action

    def _reject(self, action, message, malformed=False, protocol=False):
        self.observation = message
        self.info[ACTION_EXEC] = False
        if malformed:
            self.n_malformed += 1
        if protocol:
            self.n_protocol_violations += 1
        self.history.append((action, self.observation))

    def _action_help(self, app, requested):
        listing = "".join(f"\n - {module.DEMO}"
                          for module in apps.AVAILABLE_ACTIONS[app].values())
        return (f"Malformed action! Unknown action '{requested}' for app {app}. "
                f"Available actions:{listing}")

    def _minor_action_fix(self, action):
        proc_action = {}
        for k, v in action.items():
            if isinstance(v, list) and len(v) == 1:
                proc_action[k] = v[0]
            else:
                proc_action[k] = v
        return proc_action

    def exec_action(self, action_string: str) -> None:
        self.observation = None
        self.last_action_type = None
        self.info = {}
        try:
            action = parse_action(action_string)
            action = self._minor_action_fix(action)
            assert self.check_valid_action(action)

            if action["action"] == "switch_app":
                action["app"] = "system"

            self.last_action_type = action.get("action")

            is_cd_flag = False
            command = None
            new_path = None

            if action["app"] == "shell":
                command = action["command"]
                if isinstance(command, list):
                    command = " ".join(command)
                stripped = command.strip()
                is_cd_flag = stripped == "cd" or stripped.startswith("cd ")
                if is_cd_flag:
                    cd_arg = stripped[3:].strip()
                    new_path = self.sandbox.simplify_path(self.sandbox.workdir, cd_arg)
                    command = f"cd {new_path}"
            elif action["app"] == "system":
                if action["action"] == "switch_app":
                    target = action.get("target_app")
                    if target not in self.available_apps:
                        return self._reject(action, f"Unknown app: {target}. Available apps: "
                                                    f"{list(self.available_apps.keys())}.",
                                            malformed=True)
                    self.current_app = target
                    self.observation = f"Successfully switched to app: {self.current_app}"
                elif action["action"] == "finish_task":
                    self.answer = action.get("answer", "None")
                    self.observation = "Task finished"
                elif action["action"] == "got_stuck":
                    self.observation = "Task failed"
                command = None
            else:
                app_actions = apps.AVAILABLE_ACTIONS.get(action["app"])
                if app_actions is None:
                    return self._reject(action, f"Unknown app: {action['app']}. Available "
                                                f"apps: {list(self.available_apps)}.",
                                        malformed=True)
                if self.require_active_app and action["app"] != self.current_app:
                    return self._reject(action, f"App {action['app']} is not active. Switch to "
                                                f"it first with {{'app': 'system', 'action': "
                                                f"'switch_app', 'target_app': '{action['app']}'}}.",
                                        protocol=True)
                action_module = app_actions.get(action["action"])
                if action_module is None:
                    return self._reject(action, self._action_help(action["app"], action["action"]),
                                        malformed=True)
                try:
                    command = action_module.construct_action(self.sandbox.workdir, args=action)
                except KeyError as missing:
                    return self._reject(action, f"Malformed action! Missing argument {missing} "
                                                f"for {action['app']} {action['action']}. "
                                                f"Expected: {action_module.DEMO}", malformed=True)

            if command is not None:
                exit_code, output = self.sandbox.exec(
                    command,
                    structured=action["app"] not in ("shell", "system"),
                    expose_keys=action["app"] == "llm",
                )
                self.observation = self.sandbox.to_virtual(
                    output.split("OBSERVATION:")[-1].strip())
                self.info[ACTION_EXEC] = exit_code == 0
                if exit_code != 0 or "Failed to" in self.observation:
                    self.n_failed_exec += 1

                if is_cd_flag and self.info[ACTION_EXEC]:
                    self.sandbox.workdir = new_path

                if self.observation == "" and action["app"] == "shell":
                    self.observation = self.sandbox.to_virtual(
                        f"Successfully executed command: {command}. "
                        f"The output was [{output}].")

            self.history.append((action, self.observation))
        except Exception:
            self.observation = (
                "Malformed action! You must follow the given action format! "
                "Try a different action."
            )
            self.info[ACTION_EXEC] = False
            self.last_action_type = None
            self.n_malformed += 1
            self.history.append((action_string, self.observation))

    def _truncate(self, observation):
        if not self.max_obs_chars or observation is None:
            return observation
        text = str(observation)
        if len(text) <= self.max_obs_chars:
            return observation
        return text[: self.max_obs_chars] + f"\n[... truncated, {len(text)} characters total]"

    def step(self, action: str):
        self.exec_action(action)
        self.observation = self._truncate(self.observation)
        if self.history:
            action_obj, _ = self.history[-1]
            self.history[-1] = (action_obj, self.observation)
        self.logger.info(f"Action: {action}")
        self.logger.info(f"Observation: {self.observation}")
        done = self.last_action_type in ("finish_task", "got_stuck")
        return self.observation, 0, done, self.info

    def dump_history(self, output_dir):
        with open(f"{output_dir}/env_history.json", "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, default=str, ensure_ascii=False)
        with open(f"{output_dir}/answer.txt", "w", encoding="utf-8") as f:
            f.write(str(self.answer if self.answer is not None else "None") + "\n")

    def snapshot(self, output_dir):
        self.sandbox.snapshot_testbed(output_dir)
