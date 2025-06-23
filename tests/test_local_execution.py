import importlib

from python.helpers import runtime
import initialize

# Reload initialize to ensure latest edits are picked up (handy when running test via pytest)
importlib.reload(initialize)


def _sandbox_state(local_execution_flag: bool | None, dockerized_flag: bool | None):
    """Utility to run _args_override with a dummy config and return resulting sandbox state."""
    # Prepare runtime args
    runtime.args = {}
    if local_execution_flag is not None:
        runtime.args["local_execution"] = "true" if local_execution_flag else "false"
    if dockerized_flag is not None:
        runtime.args["dockerized"] = dockerized_flag

    # Dummy config with only the field we care about
    class DummyConfig:
        def __init__(self):
            self.code_exec_ssh_enabled = True  # default (sandbox on)

    cfg = DummyConfig()
    initialize._args_override(cfg)  # type: ignore  # We intentionally bypass full AgentConfig
    return cfg.code_exec_ssh_enabled


def test_disable_sandbox_when_local_execution_true():
    assert _sandbox_state(local_execution_flag=True, dockerized_flag=None) is False


def test_keep_sandbox_when_local_execution_false():
    assert _sandbox_state(local_execution_flag=False, dockerized_flag=None) is True


def test_ignore_local_execution_inside_docker():
    # Even if user passes --local_execution inside Docker, sandbox should remain ON
    assert _sandbox_state(local_execution_flag=True, dockerized_flag=True) is True
