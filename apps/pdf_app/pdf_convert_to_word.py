import os
import fire
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "convert a pdf file to a word file: "
    "{'app': 'pdf', 'action': 'convert_to_word', 'file_path': [THE_PATH_TO_THE_PDF_FILE]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/pdf_app/pdf_convert_to_word.py'):
    return build_command(py_file_path, file_path=args["file_path"])


def pdf_convert_to_word(file_path):
    try:
        from pdf2docx import Converter
        docx_path = os.path.splitext(file_path)[0] + '.docx'
        cv = Converter(file_path)
        cv.convert(docx_path)
        cv.close()
        return os.path.exists(docx_path)
    except Exception as e:
        print(e)
        return False


def main(file_path):
    if not os.path.exists(file_path):
        return f"OBSERVATION: The file {file_path} does not exist. Failed to convert."
    success = pdf_convert_to_word(file_path)
    if success:
        return f"OBSERVATION: Successfully convert {file_path} to word"
    return f"OBSERVATION: Failed to convert {file_path} to word"


if __name__ == '__main__':
    fire.Fire(main)
