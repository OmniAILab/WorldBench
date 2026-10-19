import fire
from docx import Document
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "create a new word file: "
    "{'app': 'word', 'action': 'create_new_file', 'file_path': [THE_PATH_TO_THE_NEW_WORD_FILE]}"
)


def construct_action(work_dir, args: dict, py_file_path='/apps/word_app/word_create_new_file.py'):
    return build_command(py_file_path, file_path=args["file_path"])

def word_create_new_file(file_path):
    try:
        document = Document()
        document.save(file_path)
        return True
    except Exception:
        return False

def main(file_path):
    success = word_create_new_file(file_path)
    if success:
        return 'OBSERVATION: ' + f"Successfully create new file {file_path}"
    return 'OBSERVATION: ' + f"Failed to create new file {file_path}"


if __name__ == '__main__':
    fire.Fire(main)
