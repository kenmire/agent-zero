import asyncio
from dataclasses import dataclass
import shlex
import time
from python.helpers.tool import Tool, Response
from python.helpers import files, rfc_exchange
from python.helpers.print_style import PrintStyle
from python.helpers.shell_local import LocalInteractiveSession
from python.helpers.shell_ssh import SSHInteractiveSession
from python.helpers.docker import DockerContainerManager
from python.helpers.messages import truncate_text
import re

# Debug print function
def debug_print(message):
    PrintStyle(background_color="#E8F8F5", font_color="#1B4F72", bold=True).print(f"[DEBUG] {message}")


@dataclass
class State:
    shells: dict[int, LocalInteractiveSession | SSHInteractiveSession]
    docker: DockerContainerManager | None


class CodeExecution(Tool):

    async def execute(self, **kwargs):
        debug_print(f"Executing CodeExecution tool with args: {self.args}")

        await self.agent.handle_intervention()  # wait for intervention and handle it, if paused
        debug_print("Intervention handled")

        await self.prepare_state()
        debug_print("State prepared")

        # os.chdir(files.get_abs_path("./work_dir")) #change CWD to work_dir

        runtime = self.args.get("runtime", "").lower().strip()
        session = int(self.args.get("session", 0))
        debug_print(f"Runtime: {runtime}, Session: {session}")

        if runtime == "python":
            debug_print(f"Executing Python code in session {session}")
            response = await self.execute_python_code(
                code=self.args["code"], session=session
            )
        elif runtime == "nodejs":
            debug_print(f"Executing NodeJS code in session {session}")
            response = await self.execute_nodejs_code(
                code=self.args["code"], session=session
            )
        elif runtime == "terminal":
            debug_print(f"Executing terminal command in session {session}")
            response = await self.execute_terminal_command(
                command=self.args["code"], session=session
            )
        elif runtime == "output":
            debug_print(f"Getting terminal output from session {session}")
            response = await self.get_terminal_output(
                session=session, first_output_timeout=60, between_output_timeout=5
            )
        elif runtime == "reset":
            debug_print(f"Resetting terminal session {session}")
            response = await self.reset_terminal(session=session)
        else:
            debug_print(f"Unknown runtime: {runtime}")
            response = self.agent.read_prompt(
                "fw.code.runtime_wrong.md", runtime=runtime
            )

        if not response:
            debug_print("No response received, generating default response")
            response = self.agent.read_prompt(
                "fw.code.info.md", info=self.agent.read_prompt("fw.code.no_output.md")
            )
        debug_print(f"Returning final response (length: {len(response) if response else 0})")
        return Response(message=response, break_loop=False)

    def get_log_object(self):
        debug_print("Creating log object for code execution")
        return self.agent.context.log.log(
            type="code_exe",
            heading=f"{self.agent.agent_name}: Using tool '{self.name}'",
            content="",
            kvps=self.args,
        )

    async def after_execution(self, response, **kwargs):
        debug_print("Adding tool result to history")
        self.agent.hist_add_tool_result(self.name, response.message)

    async def prepare_state(self, reset=False, session=None):
        debug_print(f"Preparing state (reset={reset}, session={session})")
        self.state = self.agent.get_data("_cet_state")
        debug_print(f"Current state exists: {self.state is not None}")

        if not self.state or reset:
            debug_print("Initializing or resetting state")

            # initialize docker container if execution in docker is configured
            if not self.state and self.agent.config.code_exec_docker_enabled:
                debug_print("Initializing Docker container")
                docker = DockerContainerManager(
                    logger=self.agent.context.log,
                    name=self.agent.config.code_exec_docker_name,
                    image=self.agent.config.code_exec_docker_image,
                    ports=self.agent.config.code_exec_docker_ports,
                    volumes=self.agent.config.code_exec_docker_volumes,
                )
                docker.start_container()
                debug_print("Docker container started")
            else:
                docker = self.state.docker if self.state else None
                debug_print(f"Using existing Docker container: {docker is not None}")

            # initialize shells dictionary if not exists
            shells = {} if not self.state else self.state.shells.copy()
            debug_print(f"Initial shells count: {len(shells)}")

            # Only reset the specified session if provided
            if session is not None and session in shells:
                debug_print(f"Closing specific session: {session}")
                shells[session].close()
                del shells[session]
            elif reset and not session:
                # Close all sessions if full reset requested
                debug_print("Closing all sessions for full reset")
                for s in list(shells.keys()):
                    debug_print(f"Closing session {s}")
                    shells[s].close()
                shells = {}

            # initialize local or remote interactive shell interface for session 0 if needed
            if 0 not in shells:
                debug_print("Initializing session 0")
                if self.agent.config.code_exec_ssh_enabled:
                    debug_print("Using SSH for session 0")
                    pswd = (
                        self.agent.config.code_exec_ssh_pass
                        if self.agent.config.code_exec_ssh_pass
                        else await rfc_exchange.get_root_password()
                    )
                    shell = SSHInteractiveSession(
                        self.agent.context.log,
                        self.agent.config.code_exec_ssh_addr,
                        self.agent.config.code_exec_ssh_port,
                        self.agent.config.code_exec_ssh_user,
                        pswd,
                    )
                else:
                    debug_print("Using local shell for session 0")
                    shell = LocalInteractiveSession()

                shells[0] = shell
                debug_print("Connecting to shell for session 0")
                await shell.connect()
                debug_print("Connected to shell for session 0")

            self.state = State(shells=shells, docker=docker)
            debug_print(f"New state created with {len(shells)} shells")
        else:
            debug_print(f"Using existing state with {len(self.state.shells)} shells")

        self.agent.set_data("_cet_state", self.state)
        debug_print("State preparation completed")

    async def execute_python_code(self, session: int, code: str, reset: bool = False):
        debug_print(f"Preparing Python code execution in session {session}")
        escaped_code = shlex.quote(code)
        command = f"ipython -c {escaped_code}"
        debug_print(f"Python command: {command}")
        return await self.terminal_session(session, command, reset)

    async def execute_nodejs_code(self, session: int, code: str, reset: bool = False):
        debug_print(f"Preparing NodeJS code execution in session {session}")
        escaped_code = shlex.quote(code)
        command = f"node /exe/node_eval.js {escaped_code}"
        debug_print(f"NodeJS command: {command}")
        return await self.terminal_session(session, command, reset)

    async def execute_terminal_command(
        self, session: int, command: str, reset: bool = False
    ):
        debug_print(f"Executing terminal command in session {session}: {command}")
        return await self.terminal_session(session, command, reset)

    async def terminal_session(self, session: int, command: str, reset: bool = False):
        debug_print(f"Starting terminal session {session} with command: {command}")

        await self.agent.handle_intervention()  # wait for intervention and handle it, if paused
        debug_print("Intervention handled in terminal_session")

        # try again on lost connection
        for i in range(2):
            try:
                if reset:
                    debug_print(f"Resetting terminal before execution (session {session})")
                    await self.reset_terminal()

                if session not in self.state.shells:
                    debug_print(f"Session {session} not found in state.shells, creating new session")
                    if self.agent.config.code_exec_ssh_enabled:
                        debug_print("Using SSH session")
                        pswd = (
                            self.agent.config.code_exec_ssh_pass
                            if self.agent.config.code_exec_ssh_pass
                            else await rfc_exchange.get_root_password()
                        )
                        shell = SSHInteractiveSession(
                            self.agent.context.log,
                            self.agent.config.code_exec_ssh_addr,
                            self.agent.config.code_exec_ssh_port,
                            self.agent.config.code_exec_ssh_user,
                            pswd,
                        )
                    else:
                        debug_print("Using local session")
                        shell = LocalInteractiveSession()
                    self.state.shells[session] = shell
                    debug_print(f"Connecting to shell for session {session}")
                    await shell.connect()
                else:
                    debug_print(f"Using existing session {session}")

                debug_print(f"Sending command to session {session}: {command}")
                self.state.shells[session].send_command(command)

                PrintStyle(
                    background_color="white", font_color="#1B4F72", bold=True
                ).print(f"{self.agent.agent_name} code execution output")
                debug_print(f"Getting terminal output for session {session}")
                return await self.get_terminal_output(session)

            except Exception as e:
                debug_print(f"Exception in terminal_session: {str(e)}")
                if i == 1:
                    # try again on lost connection
                    PrintStyle.error(str(e))
                    debug_print("Attempting to reset state after exception")
                    await self.prepare_state(reset=True)
                    continue
                else:
                    debug_print(f"Retrying after exception: {str(e)}")
                    raise e

    async def get_terminal_output(
        self,
        session=0,
        reset_full_output=True,
        first_output_timeout=30,  # Wait up to x seconds for first output
        between_output_timeout=30,  # Wait up to x seconds between outputs
        max_exec_timeout=180,  #hard cap on total runtime
        sleep_time=0.1,
    ):
        debug_print(f"Starting get_terminal_output for session {session}")
        debug_print(f"Timeouts: first={first_output_timeout}s, between={between_output_timeout}s, max={max_exec_timeout}s")

        # Common shell prompt regex patterns (add more as needed)
        # Extend prompt patterns to cover Windows cmd.exe, PowerShell, and WSL prompts as well
        prompt_patterns = [
            # Linux/Unix patterns
            re.compile(r"\\(venv\\).+[$#] ?$"),  # (venv) ...$ or (venv) ...#
            re.compile(r"root@[^:]+:[^#]+# ?$"),    # root@container:~#
            re.compile(r"[a-zA-Z0-9_.-]+@[^:]+:[^$#]+[$#] ?$"),  # user@host:~$

            # Windows CMD patterns
            re.compile(r"[A-Z]:\\\\[^>]*> ?$"),              # Windows cmd.exe prompt like C:\path>
            re.compile(r"[A-Z]:[^>]*> ?$"),                  # Simple Windows prompt like C:>
            re.compile(r"[^>]*> ?$"),                        # Generic command prompt ending with >

            # Windows PowerShell patterns
            re.compile(r"PS [A-Z]:\\\\[^>]*> ?$"),           # Windows PowerShell prompt like PS C:\path>
            re.compile(r"PS [^>]*> ?$"),                     # Simple PowerShell prompt like PS>

            # WSL patterns
            re.compile(r"[a-zA-Z0-9_.-]+@[^:]+:/[^$]+ ?[$#] ?$"),  # WSL prompt like user@host:/path$
            re.compile(r"[a-zA-Z0-9_.-]+@[^:]+:~[$#] ?$"),         # WSL home prompt like user@host:~$

            # Generic patterns that might catch other prompts
            re.compile(r"[$#>] ?$"),                         # Lines ending with $, #, or >
        ]
        debug_print(f"Initialized {len(prompt_patterns)} prompt patterns for detection")

        start_time = time.time()
        last_output_time = start_time
        full_output = ""
        truncated_output = ""
        got_output = False
        debug_print("Starting output collection loop")

        while True:
            await asyncio.sleep(sleep_time)
            debug_print(f"Reading output from session {session}")
            full_output, partial_output = await self.state.shells[session].read_output(
                timeout=3, reset_full_output=reset_full_output
            )
            reset_full_output = False  # only reset once

            await self.agent.handle_intervention()

            now = time.time()
            elapsed = now - start_time
            since_last = now - last_output_time
            debug_print(f"Time elapsed: {elapsed:.2f}s, Time since last output: {since_last:.2f}s")

            if partial_output:
                debug_print(f"Received partial output ({len(partial_output)} chars)")
                PrintStyle(font_color="#85C1E9").stream(partial_output)
                # full_output += partial_output # Append new output
                truncated_output = truncate_text(
                    agent=self.agent, output=full_output, threshold=10000
                )
                debug_print(f"Full output size: {len(full_output)}, Truncated size: {len(truncated_output)}")
                self.log.update(content=truncated_output)
                last_output_time = now
                got_output = True

                # Check for shell prompt at the end of output
                last_lines = truncated_output.splitlines()[-5:] if truncated_output else []
                debug_print(f"Checking last {len(last_lines)} lines for shell prompt")
                for i, line in enumerate(last_lines):
                    stripped_line = line.strip()
                    debug_print(f"Checking line {i+1}/{len(last_lines)}: '{stripped_line}'")
                    for j, pat in enumerate(prompt_patterns):
                        pattern_str = pat.pattern
                        debug_print(f"  Testing pattern {j+1}/{len(prompt_patterns)}: {pattern_str}")
                        if pat.search(stripped_line):
                            debug_print(f"Shell prompt detected: '{stripped_line}' matched pattern: {pattern_str}")
                            PrintStyle.info(
                                f"Detected shell prompt: '{stripped_line}', returning output early."
                            )
                            return truncated_output

            # Check for max execution time
            if now - start_time > max_exec_timeout:
                debug_print(f"Max execution timeout reached: {max_exec_timeout}s")
                sysinfo = self.agent.read_prompt(
                    "fw.code.max_time.md", timeout=max_exec_timeout
                )
                response = self.agent.read_prompt("fw.code.info.md", info=sysinfo)
                if truncated_output:
                    response = truncated_output + "\n\n" + response
                PrintStyle.warning(sysinfo)
                self.log.update(content=response)
                debug_print("Returning response due to max execution timeout")
                return response

            # Waiting for first output
            if not got_output:
                if now - start_time > first_output_timeout:
                    debug_print(f"First output timeout reached: {first_output_timeout}s")
                    sysinfo = self.agent.read_prompt(
                        "fw.code.no_out_time.md", timeout=first_output_timeout
                    )
                    response = self.agent.read_prompt("fw.code.info.md", info=sysinfo)
                    PrintStyle.warning(sysinfo)
                    self.log.update(content=response)
                    debug_print("Returning response due to first output timeout")
                    return response
            else:
                # Waiting for more output after first output
                if now - last_output_time > between_output_timeout:
                    debug_print(f"Between outputs timeout reached: {between_output_timeout}s")
                    sysinfo = self.agent.read_prompt(
                        "fw.code.pause_time.md", timeout=between_output_timeout
                    )
                    response = self.agent.read_prompt("fw.code.info.md", info=sysinfo)
                    if truncated_output:
                        response = truncated_output + "\n\n" + response
                    PrintStyle.warning(sysinfo)
                    self.log.update(content=response)
                    debug_print("Returning response due to between outputs timeout")
                    return response

    async def reset_terminal(self, session=0, reason: str | None = None):
        debug_print(f"Resetting terminal session {session}" + (f" - Reason: {reason}" if reason else ""))

        # Print the reason for the reset to the console if provided
        if reason:
            PrintStyle(font_color="#FFA500", bold=True).print(
                f"Resetting terminal session {session}... Reason: {reason}"
            )
        else:
            PrintStyle(font_color="#FFA500", bold=True).print(
                f"Resetting terminal session {session}..."
            )

        # Only reset the specified session while preserving others
        debug_print(f"Calling prepare_state with reset=True for session {session}")
        await self.prepare_state(reset=True, session=session)

        debug_print("Preparing reset response")
        response = self.agent.read_prompt(
            "fw.code.info.md", info=self.agent.read_prompt("fw.code.reset.md")
        )
        self.log.update(content=response)
        debug_print("Terminal reset completed")
        return response
