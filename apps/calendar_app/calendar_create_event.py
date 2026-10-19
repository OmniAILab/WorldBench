import os
import fire
from icalendar import Calendar, Event
from datetime import datetime
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app_paths import testbed_root, calendar_file as resolve_calendar
from action_args import build_command


DEMO = (
    "create a new event to a user's calendar where the time format is '%Y-%m-%d %H:%M:%S' "
    "and description and location are optional: "
    "{'app': 'calendar', 'action': 'create_event', 'user': [USER_NAME], 'summary': [EVENT_SUMMARY], 'time_start': [EVENT_START_TIME], 'time_end': [EVENT_END_TIME], 'description': [EVENT_DESCRIPTION], 'location': [EVENT_LOCATION]}"
)


def construct_action(work_dir, args: dict, py_file_path='/apps/calendar_app/calendar_create_event.py'):
    user = 'Multiple users' if isinstance(args["user"], list) else args["user"]
    return build_command(py_file_path, user=user, summary=args["summary"],
                         time_start=args["time_start"], time_end=args["time_end"],
                         description=args.get("description"), location=args.get("location"))


def create_event(user, summary, time_start, time_end, description='', location=''):
    os.makedirs(os.path.join(testbed_root(), 'calendar'), exist_ok=True)
    try:
        calendar_file = resolve_calendar(user)
        if not os.path.exists(calendar_file):
            calendar = Calendar()
            calendar.add('prodid', '-//WorldBench//mxm.dk//')
            calendar.add('version', '2.0')
        else:
            calendar = Calendar.from_ical(open(calendar_file, 'rb').read())

        event = Event()
        event.add('summary', summary)
        event.add('dtstart', datetime.strptime(time_start, '%Y-%m-%d %H:%M:%S'))
        event.add('dtend', datetime.strptime(time_end, '%Y-%m-%d %H:%M:%S'))
        event.add('dtstamp', datetime.now())
        if description:
            event.add('description', description)
        if location:
            event.add('location', location)

        calendar.add_component(event)

        with open(calendar_file, 'wb') as f:
            f.write(calendar.to_ical())
        return True
    except Exception as e:
        print('!!!', e)
        return False


def main(user, summary, time_start, time_end, description='', location=''):
    if user == 'Multiple users':
        return f"OBSERVATION: Failed to create a new event to {user}. Only support one user."
    success = create_event(user, summary, time_start, time_end, description, location)
    if success:
        return f"OBSERVATION: Successfully create a new event to {user}'s calendar."
    return f"OBSERVATION: Failed to create a new event to {user}'s calendar."


if __name__ == '__main__':
    fire.Fire(main)
