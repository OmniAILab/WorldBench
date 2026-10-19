import fire
import os
from glob import glob
from email import policy
from email.parser import BytesParser
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app_paths import testbed_root
from action_args import build_command

DEMO = (
    "List emails for a given username: "
    "{'app': 'email', 'action': 'list_emails', 'username': [USER_NAME]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/email_app/email_list_emails.py'):
    return build_command(py_file_path, username=args["username"])


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


def list_emails(username, testbed_dir=None, first_n_letters=20):
    if testbed_dir is None:
        testbed_dir = testbed_root()
    if not os.path.isdir(f'{testbed_dir}/emails/{username}'):
        return 'No emails found.'
    try:
        email_folder = f'{testbed_dir}/emails/{username}'
        email_files = sorted(glob(f'{email_folder}/*.eml'))
        emails = []
        for email_file in email_files:
            with open(email_file, 'rb') as f:
                emails.append(BytesParser(policy=policy.default).parsebytes(f.read()))
        message = ''
        for email, email_file in zip(emails, email_files):
            email_name = email_file.split('/')[-1]
            message += f'Email ID: {email_name}\n'
            message += f'From: {email["From"]}\n'
            message += f'To: {email["To"]}\n'
            message += f'Subject: {email["Subject"]}\n'
            if first_n_letters != -1:
                message += f'Content: {get_email_content(email)[:first_n_letters] + "..."}\n'
            else:
                message += f'Content: {get_email_content(email)}\n'
            message += '-' * 50 + '\n'
        if not message:
            message = 'No emails found.'
        return message
    except Exception:
        return 'Error: Failed to list emails.'


def main(username):
    message = list_emails(username)
    if message == 'No emails found.':
        return f"OBSERVATION: No emails found for {username}."
    elif message != 'Error: Failed to list emails.':
        return f"OBSERVATION: Successfully list emails for {username}:\n{message}"
    return f"OBSERVATION: Failed to list emails for {username}."


if __name__ == '__main__':
    fire.Fire(main)
