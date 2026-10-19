import os


def testbed_root():
    return os.environ.get("TESTBED_ROOT", "/testbed")


def apps_root():
    return os.environ.get("APPS_ROOT", "/apps")


def sandbox_root():
    return os.environ.get("SANDBOX_ROOT", "/")


def resolve(path):
    """Resolve a path that may use the virtual /testbed or /apps mount points."""
    if path.startswith("/testbed"):
        return testbed_root() + path[len("/testbed"):]
    if path.startswith("/apps"):
        return apps_root() + path[len("/apps"):]
    return path


def calendar_file(user):
    """Calendar of a user: calendar/<user>.ics, or an existing data/<user>.ics."""
    name = user[:-4] if user.lower().endswith(".ics") else user
    primary = os.path.join(testbed_root(), "calendar", f"{name}.ics")
    fallback = os.path.join(testbed_root(), "data", f"{name}.ics")
    if not os.path.exists(primary) and os.path.isfile(fallback):
        return fallback
    return primary
