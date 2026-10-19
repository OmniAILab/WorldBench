from . import excel_app
from . import pdf_app
from . import shell_app
from . import word_app
from . import calendar_app
from . import email_app
from . import llm_app


OPTIONAL_APPS = ('llm',)

ALL_APPS = {
    'calendar': calendar_app,
    'excel': excel_app,
    'pdf': pdf_app,
    'shell': shell_app,
    'word': word_app,
    'email': email_app,
    'llm': llm_app,
}

AVAILABLE_APPS = {k: v for k, v in ALL_APPS.items() if k not in OPTIONAL_APPS}

ALL_ACTIONS = {
    'calendar': {
        'create_event': calendar_app.calendar_create_event,
        'delete_event': calendar_app.calendar_delete_event,
        'list_events': calendar_app.calendar_list_events,
    },
    'excel': {
        'read_file': excel_app.excel_read_file,
        'set_cell': excel_app.excel_set_cell,
        'delete_cell': excel_app.excel_delete_cell,
        'create_new_file': excel_app.excel_create_new_file,
        'convert_to_pdf': excel_app.excel_convert_to_pdf,
    },
    'pdf': {
        'convert_to_word': pdf_app.pdf_convert_to_word,
        'read_file': pdf_app.pdf_read_file,
    },
    'email': {
        'send_email': email_app.email_send_email,
        'list_emails': email_app.email_list_emails,
        'read_email': email_app.email_read_email,
    },
    'shell': {
        'command': shell_app.command,
    },
    'word': {
        'convert_to_pdf': word_app.word_convert_to_pdf,
        'create_new_file': word_app.word_create_new_file,
        'read_file': word_app.word_read_file,
        'write_to_file': word_app.word_write_to_file,
    },
    'llm': {
        'complete_text': llm_app.llm_query,
    },
}

AVAILABLE_ACTIONS = {k: v for k, v in ALL_ACTIONS.items() if k not in OPTIONAL_APPS}


def enable_optional_app(name):
    """Add an optional app to the action space."""
    if name not in ALL_APPS:
        raise KeyError(f"unknown app: {name}")
    AVAILABLE_APPS[name] = ALL_APPS[name]
    AVAILABLE_ACTIONS[name] = ALL_ACTIONS[name]
