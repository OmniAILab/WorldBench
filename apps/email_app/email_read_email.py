import os
import fire
from email import policy
from email.parser import BytesParser
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app_paths import testbed_root
from action_args import build_command

DEMO = (
    "Read a user's email by the given Email ID: "
    "{'app': 'email', 'action': 'read_email', 'username': [USERNAME], 'email_id': [EMAIL_ID]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/email_app/email_read_email.py'):
    return build_command(py_file_path, email_id=args["email_id"], username=args["username"])

def _decode(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        return ''
    return payload.decode(part.get_content_charset() or 'utf-8', errors='replace')


def get_email_content(msg):
    if msg.is_multipart():
        return "\n".join(
            _decode(part) for part in msg.iter_parts()
            if part.get_content_type() in ('text/plain', 'text/html')
        )
    return _decode(msg)


def read_email(username, email_id):
    if not os.path.isdir(f'{testbed_root()}/emails/{username}'):
        return 'Error: Failed to read email.'
    try:
        if not email_id.endswith('.eml'):
            email_id += '.eml'
        email_file = f'{testbed_root()}/emails/{username}/{email_id}'
        with open(email_file, 'rb') as f:
            email = BytesParser(policy=policy.default).parsebytes(f.read())
        message = ''
        message += f'From: {email["From"]}\n'
        message += f'To: {email["To"]}\n'
        message += f'Subject: {email["Subject"]}\n'
        message += f'Content: {get_email_content(email) + "..."}\n'
        return message
    except Exception as e:
        print('!!!', e)
        return 'Error: Failed to read email.'


def main(username, email_id):
    message = read_email(username, email_id)
    if message != 'Error: Failed to read email.':
        return f"OBSERVATION: Successfully read email {email_id} for {username}:\n{message}"
    return f"OBSERVATION: Failed to read email {email_id} for {username}."


if __name__ == '__main__':
    fire.Fire(main)
