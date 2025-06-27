import select
import subprocess
import time
import sys
from python.helpers.print_style import PrintStyle
from typing import Optional, Tuple

class LocalInteractiveSession:
    def __init__(self):
        self.process = None
        self.full_output = ''

    async def connect(self):
        # Start a new subprocess with the appropriate shell for the OS
        PrintStyle.warning("Local execution mode active – commands will run directly on host. Ensure you trust the agent and inputs.")
        if sys.platform.startswith('win'):
            # Windows
            self.process = subprocess.Popen(
                ['cmd.exe'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
        else:
            # macOS and Linux
            self.process = subprocess.Popen(
                ['/bin/bash'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

    def close(self):
        if self.process:
            self.process.terminate()
            self.process.wait()

    def send_command(self, command: str):
        if not self.process:
            raise Exception("Shell not connected")
        self.full_output = ""
        self.process.stdin.write(command + '\n') # type: ignore
        self.process.stdin.flush() # type: ignore

    async def read_output(self, timeout: float = 0, reset_full_output: bool = False):
        if not self.process:
            raise Exception('Shell not connected')

        if reset_full_output:
            self.full_output = ''
        partial_output = ''
        eof = False
        start = time.time()

        while timeout <= 0 or time.time() - start < timeout:
            rlist, _, _ = select.select([self.process.stdout], [], [], 0.1)
            if not rlist:
                continue  # nothing ready – keep polling

            while True:  # <-- drain everything available *now*
                chunk = self.process.stdout.readline()
                if chunk == "":  # EOF
                    eof = True
                    break
                partial_output += chunk
                self.full_output += chunk

                # still buffered?
                if not select.select([self.process.stdout], [], [], 0)[0]:
                    break  # buffer empty – go back to outer poll
            if eof:
                break

        if not partial_output:
            return self.full_output, None
        return self.full_output, partial_output