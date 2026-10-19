import shlex


def build_command(py_file_path, **kwargs):
    """Build an app invocation with every argument shell-quoted."""
    parts = ["python3", shlex.quote(str(py_file_path))]
    for name, value in kwargs.items():
        if value is None:
            continue
        parts.append(f"--{name}")
        parts.append(shlex.quote(str(value)))
    return " ".join(parts)
