import os
import fire
import openpyxl
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from action_args import build_command

DEMO = (
    "delete the content of a cell in the excel file (index starts from 1): "
    "{'app': 'excel', 'action': 'delete_cell', 'file_path': [THE_PATH_TO_THE_EXCEL_FILE], 'row_idx': [THE_ROW_INDEX], 'column_idx': [THE_COLUMN_INDEX]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/excel_app/excel_delete_cell.py'):
    return build_command(py_file_path, file_path=args["file_path"],
                         row_idx=args["row_idx"], column_idx=args["column_idx"],
                         sheet_name=args.get("sheet_name"))


def excel_delete_cell(file_path, row_idx, column_idx, sheet_name=None):
    try:
        workbook = openpyxl.load_workbook(file_path)
        sheet = workbook.active if sheet_name is None else workbook[sheet_name]
        sheet.cell(row=int(row_idx), column=int(column_idx)).value = None
        workbook.save(file_path)
        return True
    except Exception as e:
        print(e)
        return False


def main(file_path, row_idx, column_idx, sheet_name=None):
    if not os.path.exists(file_path):
        return f"OBSERVATION: The file {file_path} does not exist. Failed to delete the cell."
    success = excel_delete_cell(file_path, row_idx, column_idx, sheet_name)
    if success:
        return f'OBSERVATION: Successfully delete cell in {file_path}'
    return f'OBSERVATION: Failed to delete cell in {file_path}'


if __name__ == '__main__':
    fire.Fire(main)
