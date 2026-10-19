import json
import logging

import apps
from core.llm import build_llm


PROMPT_DICT = {
    'prompt_undecided_app': (
        '##Task: {task}\n'
        '##Available apps: {available_apps}\n'
        '##Instruction:\n'
        " - choose an app from the available apps: {{'app': 'system', 'action': 'switch_app', 'target_app': [THE_APP_YOU_CHOOSE]}}\n"
        '##Command:'
    ),
    'prompt_undecided_app_w_history': (
        '##Task: {task}\n'
        '##History:\n'
        '{history}'
        '##Available apps: {available_apps}\n'
        '##Instruction:\n'
        " - choose an app from the available apps: {{'app': 'system', 'action': 'switch_app', 'target_app': [THE_APP_YOU_CHOOSE]}}\n"
        '##Command:'
    ),
    'prompt_decided_app': (
        '##Task: {task}\n'
        '##History:\n'
        '{history}'
        '##Current apps: {current_app}\n'
        '##Instruction: Choose one action from the list as the next step.\n'
        '{detailed_instruction}'
        " - switch to another app among {available_apps}: {{'app': 'system', 'action': 'switch_app', 'target_app': [THE_APP_YOU_CHOOSE]}}\n"
        " - finish the task with your answer as None if the task is not a question: {{'app': 'system', 'action': 'finish_task', 'answer': 'None'}}\n"
        " - finish the task with your answer if the task is a question: {{'app': 'system', 'action': 'finish_task', 'answer': [ANSWER]}}\n"
        '##Command:'
    ),
}


class LLMPolicy:
    def __init__(self, model_name, key, env, config, llm_cache=None, debug_mode=False,
                 temperature=None, max_history_chars=24000):
        self.system_message = self.construct_system_message(config, env.available_apps)
        self.llm = build_llm(model_name, key, self.system_message, temperature)
        self.llm_history = []
        self.action_window = []
        self.action_window_size = 5
        self.llm_cache = llm_cache
        self.logger = logging.getLogger(__name__)
        self.debug_mode = debug_mode
        self.max_history_chars = max_history_chars

    def dump_history(self, output_dir):
        with open(f'{output_dir}/llm_history.json', 'w', encoding='utf-8') as f:
            json.dump(self.llm_history, f, indent=2, ensure_ascii=False)

    def construct_system_message(self, config, available_apps):
        app_introduction = ''
        for app in available_apps:
            app_introduction += f' - {apps.AVAILABLE_APPS[app].INTRO}\n'
        persona = config.get('persona', '')
        username = config.get('username', '')
        identity_lines = ''
        if persona:
            identity_lines += f"You are an AI assistant acting for the following user:\n{persona}\n"
        if username:
            identity_lines += f"The user's name is {username}.\n"
        system_message = (
            f"{identity_lines}"
            "You can help solve the task step by step.\n"
            "You can interact with an operating system and use apps to solve the task.\n"
            "You must follow the instructions and use the given json format to call APIs.\n"
            "You can only generate one action at a time.\n"
            "Your response MUST be a single JSON action object and NOTHING ELSE: "
            "no explanation, no reasoning, no markdown, no code fences. Output only the JSON.\n"
            "Do not over-explore. Once you have read the information you need, take the "
            "action that modifies or creates the required file. Reading the same file twice "
            "is wasteful; prefer acting over inspecting.\n"
            "To edit a Word or Excel file, use the dedicated word/excel app actions "
            "(for example word write_to_file), NOT shell python scripts.\n"
            "When the task is complete, finish with "
            "{'app': 'system', 'action': 'finish_task', 'answer': 'None'}.\n"
            "You can find files for your task in `/testbed/data`. If you don't know the "
            "filenames, please switch to shell app and call commands to list the directory.\n"
            "You have following apps installed in the system:\n"
            f"{app_introduction}"
        ).strip()
        return system_message

    def proc_action(self, action):
        if '{' not in action or '}' not in action:
            return action
        left = action.find('{')
        right = action.rfind('}') + 1
        return action[left:right]

    def forward(self, env):
        prompt = self.build_prompt(env)
        if self.llm_cache and prompt in self.llm_cache:
            response = self.llm_cache[prompt]
        else:
            response = self.llm.generate(prompt)

        if "{" not in str(response) or "}" not in str(response):
            retry_prompt = (
                prompt
                + "\n\nYour previous response did not contain a valid JSON action. "
                "Respond with ONLY the single JSON action object, nothing else."
            )
            response = self.llm.generate(retry_prompt)

        self.llm_history.append((self.system_message, prompt, response))

        if self.debug_mode:
            print('\n\n' + '>' * 20)
            print(f'Prompt: {prompt}')
            print(f'Response: {response}')
            print('<' * 20 + '\n\n')

        action = self.proc_action(response)
        if action == '':
            action = response

        self.action_window.append(action)
        self.action_window = self.action_window[-self.action_window_size:]
        if len(self.action_window) >= self.action_window_size and all(
            a == self.action_window[0] for a in self.action_window
        ):
            self.logger.warning(f"Action stuck in the action window: {action}")
            action = repr({'app': 'system', 'action': 'got_stuck'})

        return action

    def build_prompt(self, env):
        if env.current_app is None:
            if len(env.history) == 0:
                return PROMPT_DICT['prompt_undecided_app'].format_map({
                    'task': env.task,
                    'available_apps': list(env.available_apps.keys()),
                })
            return PROMPT_DICT['prompt_undecided_app_w_history'].format_map({
                'task': env.task,
                'history': self._format_history(env),
                'available_apps': list(env.available_apps.keys()),
            })
        detailed_instruction = ''
        for action in env.get_available_actions():
            action_module = apps.AVAILABLE_ACTIONS[env.current_app][action]
            detailed_instruction += f" - {action_module.DEMO}\n"
        available_apps = [app for app in env.available_apps.keys() if app != env.current_app]
        return PROMPT_DICT['prompt_decided_app'].format_map({
            'task': env.task,
            'history': self._format_history(env),
            'current_app': env.current_app,
            'detailed_instruction': detailed_instruction,
            'available_apps': available_apps,
        })

    def _format_history(self, env):
        steps = []
        for i, (action, observation) in enumerate(env.history):
            head = f' - Step {i}: {action}'
            if observation:
                steps.append((head + ' -> [', str(observation).replace('\n', '\\n'), ']\n'))
            else:
                steps.append((head, '', '\n'))

        entries = [head + body + tail for head, body, tail in steps]
        history = ''.join(entries)
        if not self.max_history_chars or len(history) <= self.max_history_chars:
            return history

        kept, budget = [], self.max_history_chars
        for entry in reversed(entries):
            if len(entry) > budget:
                break
            kept.append(entry)
            budget -= len(entry)

        if kept:
            omitted = len(entries) - len(kept)
            prefix = f' - [{omitted} earlier steps omitted]\n' if omitted else ''
            return prefix + ''.join(reversed(kept))

        head, body, tail = steps[-1]
        marker = '... observation truncated'
        available = max(0, self.max_history_chars - len(head) - len(tail) - len(marker))
        prefix = f' - [{len(entries) - 1} earlier steps omitted]\n' if len(entries) > 1 else ''
        return prefix + head + body[:available] + marker + tail
