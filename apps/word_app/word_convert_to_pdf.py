import os
import fire
import subprocess
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "convert a word file to a pdf file: "
    "{'app': 'word', 'action': 'convert_to_pdf', 'file_path': [THE_PATH_TO_THE_WORD_FILE]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/word_app/word_convert_to_pdf.py'):
    return build_command(py_file_path, file_path=args["file_path"])


def word_convert_to_pdf(file_path):
    try:
        out_dir = os.path.dirname(os.path.abspath(file_path))
        pdf_path = os.path.splitext(file_path)[0] + '.pdf'
        previous_mtime = os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else None
        result = subprocess.run(
            ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', out_dir, file_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(result.stderr or result.stdout)
            return False
        if not os.path.exists(pdf_path):
            return False
        return previous_mtime is None or os.path.getmtime(pdf_path) > previous_mtime
    except Exception as e:
        print(e)
        return False


def main(file_path):
    if not os.path.exists(file_path):
        return f"OBSERVATION: The file {file_path} does not exist. Failed to convert."
    success = word_convert_to_pdf(file_path)
    if success:
        return f"OBSERVATION: Successfully convert {file_path} to pdf"
    return f"OBSERVATION: Failed to convert {file_path} to pdf"


if __name__ == '__main__':
    fire.Fire(main)
