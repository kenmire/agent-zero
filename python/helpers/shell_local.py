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
            elapsed = time.time() - start
            debug_print(f"Read loop iteration at {elapsed:.2f}s")
            rlist, _, _ = select.select([self.process.stdout], [], [], 0.1)
            if not rlist:
                # Only log every second to avoid excessive logging
                if int(elapsed) % 1 == 0 and elapsed > 0:
                    debug_print(f"No data ready after {elapsed:.2f}s - continuing to poll")
                continue  # nothing ready – keep polling

            debug_print("Data available - reading")
            while True:  # <-- drain everything available *now*
                chunk = self.process.stdout.readline()
                if chunk == "":  # EOF
                    debug_print("EOF detected")
                    eof = True
                    break
                chunk_len = len(chunk)
                debug_print(f"Read chunk of {chunk_len} bytes")
                partial_output += chunk
                self.full_output += chunk

                # still buffered?
                if not select.select([self.process.stdout], [], [], 0)[0]:
                    debug_print("Buffer empty - breaking inner read loop")
                    break  # buffer empty – go back to outer poll
            if eof:
                debug_print("Breaking outer loop due to EOF")
                break

        total_elapsed = time.time() - start
        debug_print(f"Read completed after {total_elapsed:.2f}s")
        debug_print(f"Collected output: full={len(self.full_output)} bytes, partial={len(partial_output)} bytes")

        if not partial_output:
            debug_print("No partial output collected, returning full_output only")
            return self.full_output, None
        debug_print("Returning both full and partial output")
        return self.full_output, partial_output
