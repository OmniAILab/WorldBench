import datetime
import glob
import hashlib
import os
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses

import icalendar
import openpyxl
import pytz

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from apps.excel_app import excel_read_file
from apps.word_app import word_read_file
from apps.pdf_app import pdf_read_file
from apps.email_app import email_list_emails
from apps.email_app.email_list_emails import get_email_content
from evaluation import matching


DEFAULT_JUDGE_MODEL = "gemini-3.5-flash"
JUDGE_MAX_TOKENS = 10


def _keywords(args, key="keywords"):
    values = args.get(key)
    if isinstance(values, (str, int, float)):
        values = [values]
    values = [str(value) for value in values or []]
    if not values:
        raise ValueError(f"'{key}' is empty, so nothing would be verified")
    return values


def _searchable(content, doc_type):
    if doc_type == "xlsx":
        content = re.sub(r"\(\d+, \d+\): ", "", content).replace("[Empty Cell]", "")
    return content


def _evaluate_contain_text(content, args):
    doc_type = args.get("doc_type")
    return matching.contains_all(
        _searchable(content, doc_type), _keywords(args), join_lines=doc_type == "pdf"
    )


def _resolve_mailbox(testbed_dir, username):
    candidates = [
        os.path.join(testbed_dir, "emails", username),
        os.path.join(testbed_dir, "emails", username.lower()),
    ]
    candidates.extend(
        path for path in sorted(glob.glob(os.path.join(testbed_dir, "emails", "*")))
        if username.lower() in os.path.basename(path).lower()
    )
    return next((path for path in candidates if os.path.isdir(path)), None)


def _target_content(testbed_dir, args):
    """Text of the declared target, or None when it cannot be read."""
    doc_type = args["doc_type"]
    if doc_type == "email":
        mailbox = _resolve_mailbox(testbed_dir, args["username"])
        if mailbox is None:
            print("!!! Mailbox not found for:", args["username"])
            return None
        return email_list_emails.list_emails(os.path.basename(mailbox), testbed_dir, -1)

    file_path = os.path.join(testbed_dir, args["file"])
    if not os.path.isfile(file_path):
        print("!!! File not exist:", file_path)
        return None
    return _read_document(file_path, doc_type)


def evaluate_contain(testbed_dir, args):
    content = _target_content(testbed_dir, args)
    if content is None:
        return False
    return _evaluate_contain_text(content, args)


def evaluate_not_contain(testbed_dir, args):
    content = _target_content(testbed_dir, args)
    if content is None:
        return False
    doc_type = args.get("doc_type")
    content = _searchable(content, doc_type)
    return not any(
        matching.contains(content, keyword, join_lines=doc_type == "pdf")
        for keyword in _keywords(args)
    )


def evaluate_file_exist(testbed_dir, args):
    return os.path.isfile(os.path.join(testbed_dir, args["file"]))


def _normalise_cell(value):
    """Render a cell or an expected value for exact comparison."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value).strip()


def evaluate_excel_cell_value(testbed_dir, args):
    """Compare declared cells exactly, reading the workbook rather than its text."""
    if not args["matches"]:
        raise ValueError("'matches' is empty, so nothing would be verified")
    file_path = os.path.join(testbed_dir, args["file"])
    if not os.path.exists(file_path):
        print("!!! File not exist:", file_path)
        return False
    try:
        cached = openpyxl.load_workbook(file_path, data_only=True)
        raw = openpyxl.load_workbook(file_path)
    except Exception as e:
        print("!!! Cannot open workbook:", file_path, e)
        return False
    sheet_name = args.get("sheet_name")
    if sheet_name and sheet_name not in cached.sheetnames:
        print(f"!!! Sheet not found: {sheet_name} in {file_path}")
        return False
    cached_sheet = cached[sheet_name] if sheet_name else cached.active
    raw_sheet = raw[sheet_name] if sheet_name else raw.active
    for match in args["matches"]:
        row, col = int(match["row"]), int(match["col"])
        value = cached_sheet.cell(row=row, column=col).value
        if value is None:
            value = raw_sheet.cell(row=row, column=col).value
        actual = _normalise_cell(value)
        expected = _normalise_cell(match["value"])
        if actual.casefold() != expected.casefold():
            print(f"!!! Cell ({row}, {col}): expected {expected!r}, found {actual!r}")
            return False
    return True


CALENDAR_DIRS = ("calendar", "data")


def calendar_paths(username):
    return [os.path.join(folder, f"{username}.ics") for folder in CALENDAR_DIRS]


def _calendar_file(testbed_dir, args):
    if args.get("file"):
        candidates = [args["file"]]
    elif args.get("username"):
        candidates = calendar_paths(args["username"])
    else:
        raise ValueError("a calendar evaluator needs 'username' or 'file'")
    for candidate in candidates:
        path = os.path.join(testbed_dir, candidate)
        if os.path.isfile(path):
            return path
    print("!!! Calendar not found:", [os.path.join(testbed_dir, c) for c in candidates])
    return None


def _load_events(calendar_file):
    if calendar_file is None:
        return None
    try:
        with open(calendar_file, "rb") as f:
            calendar = icalendar.Calendar.from_ical(f.read())
    except Exception as e:
        print(f"!!! Cannot parse calendar {calendar_file}: {e}")
        return None
    return [c for c in calendar.walk() if c.name == "VEVENT"]


def _as_datetime(value, tz):
    if not isinstance(value, datetime.datetime):
        value = datetime.datetime.combine(value, datetime.time.min)
    return tz.localize(value) if value.tzinfo is None else value.astimezone(tz)


def evaluate_calendar_no_overlap(testbed_dir, args):
    events = _load_events(_calendar_file(testbed_dir, args))
    if events is None:
        return False
    events = [e for e in events if e.get("dtstart") is not None and e.get("dtend") is not None]
    events.sort(key=lambda e: _as_datetime(e.get("dtstart").dt, pytz.UTC))
    for first, second in zip(events, events[1:]):
        if _as_datetime(first.get("dtend").dt, pytz.UTC) > _as_datetime(second.get("dtstart").dt, pytz.UTC):
            print(f"Event {first.get('summary')} and Event {second.get('summary')} overlap")
            return False
    return True


def _expected_datetime(value, date, tz):
    text = str(value).strip()
    try:
        return _as_datetime(datetime.datetime.fromisoformat(text), tz)
    except ValueError:
        pass
    if date is None:
        raise ValueError(f"time {text!r} needs a 'date' argument")
    return _as_datetime(datetime.datetime.combine(date, datetime.time.fromisoformat(text)), tz)


def _event_end(event, start):
    if event.get("dtend") is not None:
        return event.get("dtend").dt
    if event.get("duration") is not None:
        return start + event.get("duration").dt
    return None


def evaluate_calendar_event(testbed_dir, args):
    tz = pytz.timezone(args.get("timezone", "UTC"))
    date = datetime.date.fromisoformat(str(args["date"])) if args.get("date") else None
    start = _expected_datetime(args["time_start"], date, tz) if args.get("time_start") else None
    end = _expected_datetime(args["time_end"], date, tz) if args.get("time_end") else None
    keywords = {
        field: _keywords(args, f"{field}_keywords")
        for field in ("summary", "description", "location") if args.get(f"{field}_keywords")
    }
    if not (keywords or date or start or end):
        raise ValueError("evaluate_calendar_event declares no event property to check")

    events = _load_events(_calendar_file(testbed_dir, args))
    for event in events or []:
        if event.get("dtstart") is None:
            continue
        raw_start = event.get("dtstart").dt
        event_start = _as_datetime(raw_start, tz)
        raw_end = _event_end(event, raw_start)
        event_end = _as_datetime(raw_end, tz) if raw_end is not None else None
        checks = [
            date is None or event_start.date() == date,
            start is None or event_start == start,
            end is None or event_end == end,
        ] + [
            matching.contains_all(str(event.get(field, "")), words, join_lines=True)
            for field, words in keywords.items()
        ]
        if all(checks):
            return True
    return False


def _read_document(file_path, doc_type):
    """Text of the document, or None when it cannot be read."""
    readers = {
        "doc": word_read_file.word_read_file_into_string,
        "docx": word_read_file.word_read_file_into_string,
        "pdf": pdf_read_file.read_pdf_to_string,
        "xlsx": excel_read_file.excel_read_file,
    }
    reader = readers.get(doc_type)
    try:
        if reader:
            return reader(file_path)
        text = open(file_path, encoding="utf-8", errors="replace").read()
        if doc_type == "ics":
            text = text.replace("\r\n ", "").replace("\n ", "")
            text = text.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";")
        return text
    except Exception as e:
        print(f"!!! Cannot read {file_path} as {doc_type}: {e}")
        return None


def _llm_judge(document_text, doc_type, criteria):
    """True when the judge answers YES for the criteria."""
    judge_prompt = (
        "You are an evaluator for a benchmark. You are given a document and a set of "
        "criteria. Decide whether the document fully satisfies ALL the criteria.\n\n"
        f"Document type: {doc_type}\n"
        f"Criteria: {criteria}\n\n"
        "Document content:\n"
        "-----\n"
        f"{document_text}\n"
        "-----\n\n"
        "Answer with a single word: YES if the document satisfies all the criteria, "
        "or NO otherwise. Do not output anything else."
    )

    openai_key = os.environ.get("OPENAI_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    requested = os.environ.get("JUDGE_MODEL")

    judge_model = requested or DEFAULT_JUDGE_MODEL
    name = judge_model.lower()
    requested = judge_model
    if "gemini" in name:
        if not gemini_key:
            raise RuntimeError(f"JUDGE_MODEL={requested} requires GEMINI_API_KEY.")
        return _judge_gemini(gemini_key, requested, judge_prompt)
    if "gpt" in name or name.startswith(("o1", "o3", "o4")):
        if not openai_key:
            raise RuntimeError(f"JUDGE_MODEL={requested} requires OPENAI_API_KEY.")
        return _judge_openai(openai_key, requested, judge_prompt)
    raise RuntimeError(
        f"Unsupported JUDGE_MODEL: {requested}. Use a gpt-* or gemini-* judge; the judge "
        "is never silently replaced by a different model, because comparing agents scored "
        "by different judges is not meaningful."
    )


def _parse_verdict(response):
    match = re.fullmatch(r"\W*(yes|no)\W*", response or "", flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"judge returned neither YES nor NO: {response!r}")
    return match.group(1).lower() == "yes"


def _judge_openai(api_key, model, judge_prompt):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a strict evaluation judge."},
            {"role": "user", "content": judge_prompt},
        ],
        temperature=0,
        max_tokens=JUDGE_MAX_TOKENS,
    )
    return _parse_verdict(resp.choices[0].message.content)


def _judge_gemini(api_key, model, judge_prompt):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=judge_prompt,
        config=types.GenerateContentConfig(temperature=0, max_output_tokens=JUDGE_MAX_TOKENS),
    )
    return _parse_verdict(resp.text)


def _judge_document(testbed_dir, args, doc_kind, default_criteria):
    """Keyword filter first, then the judge."""
    doc_type = args.get("doc_type", "txt")
    file_path = os.path.join(testbed_dir, args.get("file", ""))
    if not os.path.isfile(file_path):
        print(f"!!! {doc_kind} target not found:", file_path)
        return False

    text = _read_document(file_path, doc_type)

    if args.get("keywords") and not _evaluate_contain_text(
            text, {"doc_type": doc_type, "keywords": args["keywords"]}):
        return False

    return _llm_judge(text, doc_type, args.get("criteria", default_criteria))


def evaluate_email(testbed_dir, args):
    default_criteria = (
        "The text is a coherent, well-formed email written in natural language. "
        "It has an appropriate greeting and closing, a clear and relevant message body, "
        "and a consistent tone suitable for its purpose."
    )
    return _judge_document(testbed_dir, args, "email", default_criteria)


def evaluate_note(testbed_dir, args):
    default_criteria = (
        "The text is a coherent, well-formed note written in natural language. "
        "It clearly conveys the intended information in a concise and relevant manner."
    )
    return _judge_document(testbed_dir, args, "note", default_criteria)


def _mailbox_state(testbed_dir, recipient):
    """Messages in the recipient's mailbox as {(filename, digest): message}."""
    mailbox = _resolve_mailbox(testbed_dir, recipient) if testbed_dir else None
    state = {}
    for path in sorted(glob.glob(os.path.join(mailbox, "*.eml"))) if mailbox else []:
        with open(path, "rb") as f:
            raw = f.read()
        key = (os.path.basename(path), hashlib.sha256(raw).hexdigest())
        state[key] = BytesParser(policy=policy.default).parsebytes(raw)
    return state


def _addressed_to(message, recipients):
    found = {
        address.strip().casefold()
        for _, address in getaddresses(message.get_all("To", []) + message.get_all("Cc", []))
    }
    locals_ = {address.split("@")[0] for address in found}
    return all(
        (wanted.casefold() in found) if "@" in wanted else (wanted.casefold() in locals_)
        for wanted in recipients
    )


def evaluate_sent_email(testbed_dir, args, initial_dir=None):
    recipients = [args["recipient"]] if isinstance(args["recipient"], str) else list(args["recipient"])
    if not recipients:
        raise ValueError("'recipient' is empty")
    keywords = _keywords(args) if args.get("keywords") else []
    subject_keywords = _keywords(args, "subject_keywords") if args.get("subject_keywords") else []
    if not keywords and not subject_keywords:
        raise ValueError("evaluate_sent_email needs 'keywords' or 'subject_keywords'")

    mailbox_name = recipients[0].split("@")[0]
    before = _mailbox_state(initial_dir, mailbox_name)
    after = _mailbox_state(testbed_dir, mailbox_name)
    for key, message in after.items():
        if key in before or not message["From"] or not _addressed_to(message, recipients):
            continue
        subject = str(message["Subject"] or "")
        text = subject + "\n" + get_email_content(message)
        if (matching.contains_all(subject, subject_keywords, join_lines=True)
                and matching.contains_all(text, keywords)):
            return True
    return False


EVALUATION_FUNCTIONS = {
    "evaluate_contain": evaluate_contain,
    "evaluate_not_contain": evaluate_not_contain,
    "evaluate_excel_cell_value": evaluate_excel_cell_value,
    "evaluate_file_exist": evaluate_file_exist,
    "evaluate_calendar_no_overlap": evaluate_calendar_no_overlap,
    "evaluate_calendar_event": evaluate_calendar_event,
    "evaluate_sent_email": evaluate_sent_email,
    "evaluate_email": evaluate_email,
    "evaluate_note": evaluate_note,
}

INITIAL_STATE_FUNCTIONS = frozenset({"evaluate_sent_email"})


def active_judge_model():
    """The judge that will be used for this run."""
    return os.environ.get("JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
