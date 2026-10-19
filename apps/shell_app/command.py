DEMO = (
   "run a shell command: "
   "{'app': 'shell', 'action': 'command', 'command': [THE_COMMAND_YOU_WISH_TO_RUN]}"
)

def construct_action(work_dir, args: dict):
    return args["command"]
