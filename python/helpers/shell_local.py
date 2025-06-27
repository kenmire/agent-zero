import select
import subprocess
import time
import sys
import asyncio
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
        """
        Read output from the subprocess with deadlock prevention.

        This method has been modified to prevent deadlocks that could occur when:
        1. The subprocess writes a partial line (without a newline)
        2. The readline() call reads that partial line and waits for more data
        3. The peek(1) operation also waits indefinitely for more data

        The solution adds timeouts to peek operations and uses select() to check
        for data availability before attempting to read, preventing indefinite blocking.

        Args:
            timeout: Maximum time to wait for output (0 = wait indefinitely)
            reset_full_output: Whether to clear the full_output buffer before reading

        Returns:
            Tuple of (full_output, partial_output)
        """
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

        # Maximum time to wait for a peek operation in seconds
        peek_timeout = 0.5

        while timeout <= 0 or time.time() - start < timeout:
            # Drain everything already buffered inside Python
            buf = self.process.stdout.buffer  # underlying BufferedReader
            current_time = time.time()
            elapsed = current_time - start
            debug_print(f"Outer loop iteration at {elapsed:.2f}s - checking buffer")

            # Use select to check if data is available before peeking
            rlist, _, _ = select.select([self.process.stdout], [], [], 0.1)
            if not rlist:
                # Only log every second to avoid excessive logging
                if int(elapsed) % 1 == 0:
                    debug_print(f"No data ready after {elapsed:.2f}s - continuing to poll")
                continue  # nothing ready – keep polling

            # Safe peek with timeout
            peek_start = time.time()
            try:
                # Set a timeout for the peek operation
                peek_result = b''
                while time.time() - peek_start < peek_timeout:
                    peek_result = buf.peek(1)
                    if peek_result:
                        break
                    await asyncio.sleep(0.01)  # Small sleep to avoid CPU spinning

                debug_print(f"Buffer peek result: {len(peek_result)} bytes (took {time.time() - peek_start:.3f}s)")

                # If no data after timeout, continue to next iteration
                if not peek_result:
                    debug_print(f"Peek timeout after {peek_timeout}s - continuing to next iteration")
                    continue
            except Exception as e:
                debug_print(f"Error during peek: {str(e)} - continuing")
                continue

            drain_count = 0
            while True:  # keep draining
                debug_print(f"Inner loop iteration {drain_count} - reading line")

                # Use select with a short timeout before readline to avoid blocking
                rlist, _, _ = select.select([self.process.stdout], [], [], 0.1)
                if not rlist:
                    debug_print("No data available for reading - breaking inner loop")
                    break

                try:
                    line = self.process.stdout.readline()
                    if line == "":  # EOF / would-block
                        debug_print("EOF or would-block detected")
                        eof = True
                        break
                    line_len = len(line)
                    debug_print(f"Read line of {line_len} bytes")
                    partial_output += line
                    self.full_output += line
                    drain_count += 1
                except Exception as e:
                    debug_print(f"Error during readline: {str(e)} - breaking inner loop")
                    break

                # Safe peek with timeout to check if buffer is empty
                peek_start = time.time()
                try:
                    peek_result = b''
                    while time.time() - peek_start < peek_timeout:
                        peek_result = buf.peek(1)
                        if peek_result or time.time() - peek_start >= peek_timeout:
                            break
                        await asyncio.sleep(0.01)

                    debug_print(f"Buffer peek after read: {len(peek_result)} bytes")
                    if not peek_result:  # returns b'' if buffer empty
                        debug_print(f"Buffer empty after {drain_count} reads - breaking inner loop")
                        break
                except Exception as e:
                    debug_print(f"Error during peek after read: {str(e)} - breaking inner loop")
                    break

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
