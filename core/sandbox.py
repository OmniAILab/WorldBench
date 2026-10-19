import os
import shutil
import subprocess
import signal
import re
import shlex


class TimeoutException(Exception):
    pass


class timeout:
    def __init__(self, seconds=30, error_message="Timeout"):
        self.seconds = seconds
        self.error_message = error_message

    def handle_timeout(self, signum, frame):
        raise TimeoutException(self.error_message)

    def __enter__(self):
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, self.handle_timeout)
            signal.alarm(self.seconds)

    def __exit__(self, exc_type, exc_value, traceback):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)


class LocalSandbox:
    """Working directory for agent operations.

    Layout under `root_dir`: `testbed/{data,emails,calendar}` and `apps`. The
    agent references files through the virtual mount point /testbed, rewritten
    to the real path before execution.

    This is a working directory, not a security boundary: the shell app runs
    real commands as the current user. The subprocess environment is scrubbed
    and no credentials are written here, but untrusted models belong in a
    disposable container.
    """

    VIRTUAL_TESTBED = "/testbed"
    VIRTUAL_APPS = "/apps"

    ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "SYSTEMROOT")
    ENV_DEFAULTS = {"PYTHONWARNINGS": "ignore", "PYTHONIOENCODING": "utf-8"}

    def __init__(self, root_dir):
        self.expose_keys = False
        self.openai_key = None
        self.gemini_key = None
        self.root_dir = os.path.abspath(root_dir)
        self.testbed_dir = os.path.join(self.root_dir, "testbed")
        self.apps_dir = os.path.join(self.root_dir, "apps")
        self.workdir = self.VIRTUAL_TESTBED

    def setup(self, source_testbed_dir, source_apps_dir, openai_key=None, gemini_key=None,
              expose_keys=False):
        if os.path.exists(self.root_dir):
            shutil.rmtree(self.root_dir)
        os.makedirs(self.root_dir, exist_ok=True)

        os.makedirs(os.path.join(self.testbed_dir, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.testbed_dir, "emails"), exist_ok=True)
        os.makedirs(os.path.join(self.testbed_dir, "calendar"), exist_ok=True)

        if source_testbed_dir and os.path.isdir(source_testbed_dir):
            for name in os.listdir(source_testbed_dir):
                src = os.path.join(source_testbed_dir, name)
                dst = os.path.join(self.testbed_dir, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

        shutil.copytree(source_apps_dir, self.apps_dir, dirs_exist_ok=True)

        self.expose_keys = expose_keys
        self.openai_key = openai_key
        self.gemini_key = gemini_key
        self.workdir = self.VIRTUAL_TESTBED

    def to_virtual(self, text):
        """Map real sandbox paths back to the mount points the agent addresses."""
        return (str(text)
                .replace(self.apps_dir, self.VIRTUAL_APPS)
                .replace(self.testbed_dir, self.VIRTUAL_TESTBED))

    @staticmethod
    def _under(virtual_path, mount):
        return virtual_path == mount or virtual_path.startswith(mount + "/")

    def _to_real_path(self, virtual_path):
        if self._under(virtual_path, self.VIRTUAL_TESTBED):
            return self.testbed_dir + virtual_path[len(self.VIRTUAL_TESTBED):]
        if self._under(virtual_path, self.VIRTUAL_APPS):
            return self.apps_dir + virtual_path[len(self.VIRTUAL_APPS):]
        return virtual_path

    def _rewrite_command(self, command, structured=False):
        if not structured:
            for mount, real in ((self.VIRTUAL_APPS, self.apps_dir),
                                (self.VIRTUAL_TESTBED, self.testbed_dir)):
                command = re.sub(f"{re.escape(mount)}(?=/|$|[^\\w.-])", real, command)
            return command

        parts = shlex.split(command)
        for i, part in enumerate(parts):
            previous = parts[i - 1] if i else ''
            if i == 1 or previous.endswith('_path'):
                parts[i] = self._to_real_path(part)
        return shlex.join(parts)

    def real_workdir(self):
        return self._to_real_path(self.workdir)

    def exec(self, command, seconds=30, structured=False, expose_keys=False):
        real_command = self._rewrite_command(command, structured=structured)
        real_cwd = self.real_workdir()
        if not os.path.isdir(real_cwd):
            real_cwd = self.testbed_dir

        env = {k: v for k, v in os.environ.items() if k in self.ENV_PASSTHROUGH}
        env.update(self.ENV_DEFAULTS)
        env["TESTBED_ROOT"] = self.testbed_dir
        env["APPS_ROOT"] = self.apps_dir
        env["SANDBOX_ROOT"] = self.root_dir
        if self.expose_keys and expose_keys:
            if self.openai_key:
                env["OPENAI_API_KEY"] = self.openai_key
            if self.gemini_key:
                env["GEMINI_API_KEY"] = self.gemini_key
                env["GOOGLE_API_KEY"] = self.gemini_key
        try:
            with timeout(seconds):
                result = subprocess.run(
                    ["/bin/bash", "-c", real_command],
                    cwd=real_cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                )
            output = result.stdout + result.stderr
            return result.returncode, output
        except TimeoutException:
            return 1, "Timeout: command took too long to execute."
        except Exception as e:
            return 1, f"Execution error: {e}"

    def write_answer(self, answer, virtual_path):
        real_path = self._to_real_path(virtual_path)
        os.makedirs(os.path.dirname(real_path), exist_ok=True)
        with open(real_path, "w", encoding="utf-8") as f:
            f.write(str(answer) + "\n")

    def simplify_path(self, current, changed):
        if not changed:
            return current
        if changed[0] == "/":
            current = ""
        path = []
        for segment in (current + "/" + changed).split("/"):
            if segment == "..":
                if path:
                    path.pop()
            elif segment and segment != ".":
                path.append(segment)
        return "/" + "/".join(path)

    def snapshot_testbed(self, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        dst = os.path.join(dest_dir, "testbed")
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(self.testbed_dir, dst)
        return dst
