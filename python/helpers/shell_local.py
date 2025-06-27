import select
import subprocess
import time
import sys
from python.helpers.print_style import PrintStyle
from typing import Optional, Tuple

# Debug print function
def debug_print(message):
    PrintStyle(background_color="#E8F8F5", font_color="#1B4F72", bold=True).print(f"[DEBUG] [LocalShell] {message}")

class LocalInteractiveSession:
    def __init__(self):
        debug_print("Initializing LocalInteractiveSession")
        self.process = None
        self.full_output = ''

    async def connect(self):
        debug_print("Connecting to local shell")
        # Start a new subprocess with the appropriate shell for the OS
        PrintStyle.warning("Local execution mode active – commands will run directly on host. Ensure you trust the agent and inputs.")
        if sys.platform.startswith('win'):
            # Windows
            debug_print("Starting Windows cmd.exe shell")
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
            debug_print("Starting Unix/Linux /bin/bash shell")
            self.process = subprocess.Popen(
                ['/bin/bash'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
        debug_print("Shell process started successfully")

    def close(self):
        debug_print("Closing shell process")
        if self.process:
            debug_print("Terminating process")
            self.process.terminate()
            debug_print("Waiting for process to exit")
            self.process.wait()
            debug_print("Process closed successfully")
        else:
            debug_print("No process to close")

    def send_command(self, command: str):
        debug_print(f"Sending command: {command}")
        if not self.process:
            debug_print("Error: Shell not connected")
            raise Exception("Shell not connected")
        debug_print("Resetting full_output")
        self.full_output = ""
        debug_print("Writing command to stdin")
        self.process.stdin.write(command + '\n') # type: ignore
        debug_print("Flushing stdin")
        self.process.stdin.flush() # type: ignore
        debug_print("Command sent successfully")

    async def read_output(self, timeout: float = 0, reset_full_output: bool = False):
        debug_print(f"Reading output (timeout={timeout}s, reset_full_output={reset_full_output})")
        if not self.process:
            debug_print("Error: Shell not connected")
            raise Exception('Shell not connected')

        if reset_full_output:
            debug_print("Resetting full_output buffer")
            self.full_output = ''
        partial_output = ''
        eof = False
        start = time.time()
        debug_print(f"Starting read loop at {start}")

        while timeout <= 0 or time.time() - start < timeout:
            # Wait until either new data arrives in the FD OR we already have data
            if not self.process.stdout.buffer.peek(1):  # nothing buffered yet
                r, _, _ = select.select([self.process.stdout], [], [], 0.1)
                if not r:
                    continue  # poll again

            # Drain everything already buffered inside Python
            while True:
                line = self.process.stdout.readline()
                if line == "":  # EOF
                    eof = True
                    break
                partial_output += line
                self.full_output += line

                # Stop when the Python buffer is now empty
                if not self.process.stdout.buffer.peek(1):
                    break

            if eof:
                break

        total_elapsed = time.time() - start
        debug_print(f"Read completed after {total_elapsed:.2f}s")
        debug_print(f"Collected output: full={len(self.full_output)} bytes, partial={len(partial_output)} bytes")

        if not partial_output:
            debug_print("No partial output collected, returning full_output only")
            return self.full_output, None
        debug_print("Returning both full and partial output")
        return self.full_output, partial_output
