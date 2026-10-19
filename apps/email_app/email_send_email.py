import os
import fire
import re
import uuid
from email.message import EmailMessage
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app_paths import testbed_root
from action_args import build_command

DEMO = (
    "Send an email to a recipient: "
    "{'app': 'email', 'action': 'send_email', 'sender': [SENDER], 'recipient': [RECIPIENT], 'subject': [SUBJECT], 'content': [CONTENT]}"
)

def construct_action(work_dir, args: dict, py_file_path='/apps/email_app/email_send_email.py'):
    recipient = 'Multiple recipients' if isinstance(args["recipient"], list) else args["recipient"]
    return build_command(py_file_path, sender=args["sender"], recipient=recipient,
                         subject=args["subject"], content=args["content"])

def _message_filename(subject):
    """A unique, path-safe filename derived from the subject."""
    slug = re.sub(r'[^\w.-]+', '_', str(subject)).strip('_')[:60] or 'message'
    return f'{slug}-{uuid.uuid4().hex[:8]}.eml'


def _mailbox(address):
    return address.split('@')[0]


def _address(value):
    return value if '@' in value else f'{value}@example.com'


def send_email(sender, recipient, subject, content):
    sender_address, recipient_address = _address(sender), _address(recipient)
    sender, recipient = _mailbox(sender), _mailbox(recipient)

    base = testbed_root()
    os.makedirs(f'{base}/emails/{sender}', exist_ok=True)
    os.makedirs(f'{base}/emails/{recipient}', exist_ok=True)
    try:
        email = EmailMessage()
        email['From'] = sender_address
        email['To'] = recipient_address
        email['Subject'] = subject
        email.set_content(content)
        filename = _message_filename(subject)
        rendered = email.as_string()
        for mailbox in (recipient, sender):
            with open(os.path.join(base, 'emails', mailbox, filename), 'w', encoding='utf-8') as f:
                f.write(rendered)
        return f'Successfully sent email to {recipient}.'
    except Exception as e:
        print('!!!', e)
        return 'Error: Failed to send email.'

def main(sender, recipient, subject, content):
    if recipient == 'Multiple recipients':
        return f"OBSERVATION: Failed to send email to {recipient}. Only support one recipient."
    message = send_email(sender, recipient, subject, content)
    if message == 'Error: Failed to send email.':
        return f"OBSERVATION: Failed to send email to {recipient}."
    return f"OBSERVATION: Successfully sent email to {recipient}."


if __name__ == '__main__':
    fire.Fire(main)
