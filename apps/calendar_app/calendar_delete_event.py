import os
import fire
from icalendar import Calendar
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app_paths import calendar_file as resolve_calendar
from action_args import build_command


DEMO = (
    "delete an event from a user's calendar given the event summary:"
    "{'app': 'calendar', 'action': 'delete_event', 'user': [USER_NAME], 'summary': [EVENT_SUMMARY]}"
)


def construct_action(work_dir, args: dict, py_file_path='/apps/calendar_app/calendar_delete_event.py'):
    return build_command(py_file_path, user=args["user"], summary=args["summary"])


def delete_event(user, summary):
    try:
        calendar_file = resolve_calendar(user)
        calendar = Calendar.from_ical(open(calendar_file, 'rb').read())
        new_calendar = Calendar()
        for key, value in calendar.items():
            new_calendar.add(key, value)
        for component in calendar.subcomponents:
            if component.name == "VEVENT" and component.get('summary') == summary:
                continue
            new_calendar.add_component(component)
        with open(calendar_file, 'wb') as f:
            f.write(new_calendar.to_ical())
        return True
    except Exception as e:
        print('!!!', e)
        return False


def main(user, summary):
    success = delete_event(user, summary)
    if success:
        return f"OBSERVATION: Successfully delete an event named {summary} from {user}'s calendar."
    return f"OBSERVATION: Failed to delete an event named {summary} from {user}'s calendar."


if __name__ == '__main__':
    fire.Fire(main)
