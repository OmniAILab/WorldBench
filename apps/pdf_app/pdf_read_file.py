import os
import fire
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "read the content of a pdf file: "
    "{'app': 'pdf', 'action': 'read_file', 'file_path': [THE_PATH_TO_THE_PDF_FILE]}"
)


def construct_action(work_dir, args: dict, py_file_path='/apps/pdf_app/pdf_read_file.py'):
    return build_command(py_file_path, file_path=args["file_path"])


def read_pdf_to_string(file_path):
    import fitz
    doc = fitz.open(file_path)
    text = ''
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def read_pdf(file_path):
    return read_pdf_to_string(file_path)


def main(file_path, debug=False):
    if not os.path.exists(file_path):
        return f"OBSERVATION: The file {file_path} does not exist. Failed to read the file."
    try:
        text = read_pdf_to_string(file_path)
    except Exception as e:
        return f"OBSERVATION: Failed to read the pdf file: {e}"
    if debug:
        print(text)
    return "OBSERVATION: The following is the content from the pdf file:\n" + text


if __name__ == '__main__':
    fire.Fire(main)
