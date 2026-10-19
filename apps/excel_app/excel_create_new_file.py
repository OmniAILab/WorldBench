import os
import fire
import openpyxl
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "create a new excel file: "
    "{'app': 'excel', 'action': 'create_new_file', 'file_path': [THE_PATH_TO_THE_NEW_EXCEL_FILE]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/excel_app/excel_create_new_file.py'):
    return build_command(py_file_path, file_path=args["file_path"])


def excel_create_new_file(file_path):
    try:
        wb = openpyxl.Workbook()
        wb.save(file_path)
        return True
    except Exception:
        return False


def main(file_path):
    if os.path.exists(file_path):
        return f"OBSERVATION: File {file_path} already exists"
    success = excel_create_new_file(file_path)
    if success:
        return f"OBSERVATION: Successfully create new file {file_path}"
    return f"OBSERVATION: Failed to create new file {file_path}"


if __name__ == '__main__':
    fire.Fire(main)
