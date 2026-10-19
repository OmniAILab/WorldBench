import os
import fire
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "Query an LLM model for an answer to a given prompt: "
    "{'app': 'llm', 'action': 'complete_text', 'prompt': [PROMPT]}"
)


def construct_action(work_dir, args: dict, py_file_path="/apps/llm_app/llm_query.py"):
    return build_command(py_file_path, prompt=args["prompt"])


def _read_key(name):
    candidates = [
        os.path.join(os.environ.get("SANDBOX_ROOT", "/"), name),
        os.path.join("/", name),
    ]
    for path in candidates:
        if os.path.exists(path):
            return open(path).read().strip()
    return ""


def query(prompt):
    try:
        openai_key = os.environ.get("OPENAI_API_KEY") or _read_key("openai_key.txt")
        gemini_key = os.environ.get("GEMINI_API_KEY") or _read_key("gemini_key.txt")
        if openai_key:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model=os.environ.get("LLM_APP_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=256,
            )
            return resp.choices[0].message.content.strip()
        elif gemini_key:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model=os.environ.get("LLM_APP_MODEL", "gemini-3.5-flash"),
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=256),
            )
            return resp.text.strip()
        return "Error: no API key available for llm app."
    except Exception as e:
        return f"Error: {e}"


def main(prompt):
    return "OBSERVATION: " + query(prompt)


if __name__ == "__main__":
    fire.Fire(main)
