import select
import subprocess
import time
import sys
import asyncio
import os
from python.helpers.print_style import PrintStyle
from typing import Optional, Tuple, List, Any

# Debug print function
def debug_print(message):
    PrintStyle(background_color="#E8F8F5", font_color="#1B4F72", bold=True).print(f"[DEBUG] [LocalShell] {message}")

def safe_select(read_list: List[Any], timeout: float = 0.1) -> List[Any]:
    """
    Safely perform a select operation with fallback for Windows.

    On Windows, select() might not work correctly with file objects from subprocess,
    so we use a simple polling approach as a fallback.

    Args:
        read_list: List of file objects to check for readability
        timeout: Maximum time to wait for data

    Returns:
        List of file objects that are ready for reading
    """
    # Special case for Windows - select() is unreliable on Windows with pipes
    if sys.platform.startswith('win'):
        # Windows-specific approach - always assume data might be available
        # This is more aggressive but prevents missing data on Windows
        debug_print(f"Windows platform detected, using aggressive polling")

        # On Windows, we'll be more aggressive and assume data is available
        # This might cause more CPU usage but ensures we don't miss data
        result = []
        for fd in read_list:
            try:
                # For text mode file objects with buffer
                if hasattr(fd, 'buffer'):
                    # Try to peek at the buffer without blocking
                    peek_result = fd.buffer.peek(1)
                    if peek_result:
                        debug_print(f"Data detected in buffer peek: {len(peek_result)} bytes")
                        result.append(fd)
                    else:
                        # Even if peek returns empty, we'll still check
                        # Windows buffers can be tricky, so we'll be aggressive
                        debug_print(f"No data in buffer peek, but adding fd anyway (Windows)")
                        result.append(fd)
                # Direct check for objects with peek
                elif hasattr(fd, 'peek'):
                    peek_result = fd.peek(1)
                    if peek_result:
                        debug_print(f"Data detected in peek: {len(peek_result)} bytes")
                        result.append(fd)
                    else:
                        # Even if peek returns empty, we'll still check
                        debug_print(f"No data in peek, but adding fd anyway (Windows)")
                        result.append(fd)
                else:
                    # If we can't check, assume data might be available
                    debug_print(f"Can't check fd, assuming data might be available (Windows)")
                    result.append(fd)
            except Exception as e:
                debug_print(f"Error checking file descriptor: {str(e)}")
                # Even on error, we'll be aggressive and assume data might be available
                result.append(fd)

        return result

    # Non-Windows platforms can use standard select
    try:
        # Try standard select first
        rlist, _, _ = select.select(read_list, [], [], timeout)
        return rlist
    except (select.error, ValueError, TypeError) as e:
        # Fallback for if select fails
        debug_print(f"Select failed ({str(e)}), using fallback polling method")

        # Simple polling approach - check if any data is available
        result = []
        for fd in read_list:
            try:
                # For text mode file objects
                if hasattr(fd, 'buffer'):
                    # Try to peek at the buffer without blocking
                    if fd.buffer.peek(1):
                        result.append(fd)
                # Direct check
                elif hasattr(fd, 'peek'):
                    if fd.peek(1):
                        result.append(fd)
            except Exception as e:
                debug_print(f"Error checking file descriptor: {str(e)}")

        return result

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

        The solution uses a simpler approach with select() to check for data availability
        and direct reading from the stdout file object, avoiding the complex peek operations
        that might be causing issues with text mode vs binary mode.

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
        last_data_time = start  # Initialize last_data_time
        debug_print(f"Starting read loop at {start}")

        # Track if we've seen any output at all during this call
        got_any_output = False

        while timeout <= 0 or time.time() - start < timeout:
            current_time = time.time()
            elapsed = current_time - start
            debug_print(f"Outer loop iteration at {elapsed:.2f}s - checking for data")

            # Use safe_select to check if data is available for reading on stdout or stderr
            # This is more reliable and works on all platforms including Windows
            # Increased timeout to 0.2 seconds to give more time to detect data
            rlist = safe_select([self.process.stdout, self.process.stderr], timeout=0.2)
            if not rlist:
                # Only log every second to avoid excessive logging
                if int(elapsed) % 1 == 0:
                    debug_print(f"No data ready after {elapsed:.2f}s - continuing to poll")
                continue  # nothing ready – keep polling

            debug_print(f"Data available for reading on {len(rlist)} file descriptors - starting read loop")

            # Data is available, read it directly without additional checks
            drain_count = 0
            while True:
                # Check if more data is available without blocking on stdout or stderr
                rlist = safe_select([self.process.stdout, self.process.stderr], timeout=0)
                if not rlist:
                    debug_print(f"No more data available after reading {drain_count} lines - breaking inner loop")
                    break

                try:
                    # Determine which file descriptor to read from
                    read_from_stdout = self.process.stdout in rlist
                    read_from_stderr = self.process.stderr in rlist

                    line = ""
                    source = ""

                    # Read from the appropriate file descriptor
                    if read_from_stdout:
                        try:
                            # Try direct read first (more reliable, especially on Windows)
                            if hasattr(self.process.stdout, 'buffer'):
                                chunk = self.process.stdout.buffer.read1(4096)
                                if chunk:
                                    line = chunk.decode('utf-8', errors='replace')
                                    source = "stdout (direct)"
                                    debug_print(f"Direct read from stdout successful: {len(line)} bytes")
                                else:
                                    # Fallback to readline if direct read returns empty
                                    line = self.process.stdout.readline()
                                    source = "stdout (readline)"
                            else:
                                # If no buffer attribute, use readline
                                line = self.process.stdout.readline()
                                source = "stdout (readline)"
                        except Exception as e:
                            debug_print(f"Error reading from stdout: {str(e)}")
                            # Try another approach as last resort
                            try:
                                line = self.process.stdout.read(4096)
                                source = "stdout (read)"
                                debug_print(f"Read from stdout successful: {len(line)} bytes")
                            except Exception as e2:
                                debug_print(f"All read methods from stdout failed: {str(e2)}")
                                line = ""
                    elif read_from_stderr:
                        try:
                            # Try direct read first (more reliable, especially on Windows)
                            if hasattr(self.process.stderr, 'buffer'):
                                chunk = self.process.stderr.buffer.read1(4096)
                                if chunk:
                                    line = chunk.decode('utf-8', errors='replace')
                                    source = "stderr (direct)"
                                    debug_print(f"Direct read from stderr successful: {len(line)} bytes")
                                else:
                                    # Fallback to readline if direct read returns empty
                                    line = self.process.stderr.readline()
                                    source = "stderr (readline)"
                            else:
                                # If no buffer attribute, use readline
                                line = self.process.stderr.readline()
                                source = "stderr (readline)"
                        except Exception as e:
                            debug_print(f"Error reading from stderr: {str(e)}")
                            # Try another approach as last resort
                            try:
                                line = self.process.stderr.read(4096)
                                source = "stderr (read)"
                                debug_print(f"Read from stderr successful: {len(line)} bytes")
                            except Exception as e2:
                                debug_print(f"All read methods from stderr failed: {str(e2)}")
                                line = ""

                    if not line:  # EOF
                        debug_print(f"EOF detected on {source}")
                        if source.startswith("stdout"):  # Only consider EOF on stdout as true EOF
                            eof = True
                        break

                    line_len = len(line)
                    debug_print(f"Read line of {line_len} bytes from {source}: {line.strip()}")
                    partial_output += line
                    self.full_output += line
                    drain_count += 1

                    # Update the last_data_time whenever we receive new data
                    last_data_time = time.time()
                    debug_print(f"Updated last_data_time to {last_data_time:.2f}")

                    # If we've read a significant amount of data, break to allow processing
                    if drain_count > 100:
                        debug_print(f"Read {drain_count} lines - breaking to allow processing")
                        break

                except Exception as e:
                    debug_print(f"Error during readline: {str(e)} - breaking inner loop")
                    break

            debug_print(f"Completed read cycle, read {drain_count} lines")

            # Instead of returning immediately when we get output, continue reading
            # to ensure we get all available output
            if partial_output:
                debug_print(f"Got partial output ({len(partial_output)} bytes) - continuing to check for more data")

                # Mark that we've seen output during this call
                got_any_output = True

                # Accumulate the partial output into our full output
                # We don't reset partial_output here to ensure we don't lose data between iterations
                # The full_output already contains this data, so we're not duplicating

                # Continue reading until we haven't seen new data for a certain period
                # This ensures we capture all output, even if it comes in bursts
                # Note: last_data_time is updated whenever we receive new data in the inner loop

                # Add a longer sleep to give the subprocess more time to produce output
                # Increased to 0.2 seconds to give more time for buffering
                debug_print(f"Sleeping for 0.2s to allow more output to be produced")
                await asyncio.sleep(0.2)

                # Only break out if we've been reading for at least 2.0 seconds total
                # AND we haven't seen new data for at least 1.0 seconds
                # Increased both thresholds significantly to give more time for output
                if time.time() - start > 2.0 and time.time() - last_data_time > 1.0:
                    # Check if there's more data available with increased timeout
                    debug_print(f"Checking for more data after waiting {time.time() - last_data_time:.2f}s since last data")
                    rlist = safe_select([self.process.stdout, self.process.stderr], timeout=0.5)
                    if not rlist:
                        debug_print(f"No more data available after waiting {time.time() - last_data_time:.2f}s since last data - breaking outer loop")
                        break
                    else:
                        debug_print(f"More data detected after waiting - continuing to read")
                        # Don't break, continue reading

            if eof:
                debug_print("Breaking outer loop due to EOF")
                break

        total_elapsed = time.time() - start
        debug_print(f"Read completed after {total_elapsed:.2f}s")
        debug_print(f"Collected output: full={len(self.full_output)} bytes, partial={len(partial_output)} bytes")

        # If we got any output during this call, return the full output as both full and partial
        # This ensures the calling code gets all the output we collected
        if got_any_output:
            debug_print("Output was collected during this call, returning full output")
            # Return the full output as the partial output to ensure it's processed by the caller
            # This is critical to ensure all output is processed by the caller
            debug_print(f"Full output content: {self.full_output}")
            return self.full_output, self.full_output

        # If we didn't get any output, return None for partial_output
        debug_print("No output collected during this call")
        return self.full_output, None
