import icalendar
import os
import fire
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app_paths import calendar_file as resolve_calendar
from action_args import build_command

DEMO = (
    "list all events from a user's calendar: "
    "{'app': 'calendar', 'action': 'list_events', 'username': [USER_NAME]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/calendar_app/calendar_list_events.py'):
    username = 'Multiple users' if isinstance(args["username"], list) else args["username"]
    return build_command(py_file_path, username=username)

def format_time(obj):
    if obj:
        return obj.dt.strftime('%Y-%m-%d %H:%M:%S')
    return obj

def list_events(username):
    calendar_file = resolve_calendar(username)
    if not os.path.exists(calendar_file):
        return ''
    try:
        calendar = icalendar.Calendar.from_ical(open(calendar_file, 'rb').read())
        message = ''
        for component in calendar.walk():
            if component.name == "VEVENT":
                message += f'Summary: {component.get("summary")}\n'
                message += f'Start Time: {format_time(component.get("dtstart"))}\n'
                message += f'End Time: {format_time(component.get("dtend"))}\n'
                message += f'Description: {component.get("description")}\n'
                message += f'Location: {component.get("location")}\n'
                message += '-' * 50 + '\n'
        return message.strip()
    except Exception:
        return 'Error: Failed to list events.'

def main(username):
    if username == 'Multiple users':
        return f"OBSERVATION: Failed to list events for {username}. Only support one user."
    message = list_events(username)
    if message == 'Error: Failed to list events.':
        return f"OBSERVATION: Failed to list events for {username}."
    elif message == '':
        return f"OBSERVATION: No events found for {username}."
    return f"OBSERVATION: Successfully list events for {username}:\n{message}"

if __name__ == '__main__':
    fire.Fire(main)
