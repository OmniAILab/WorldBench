INTRO = "email: an app to manage emails, such as sending and reading emails."

from . import email_send_email
from . import email_list_emails
from . import email_read_email

__all__ = ["email_send_email", "email_list_emails", "email_read_email"]
