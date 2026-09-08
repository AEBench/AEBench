import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from models import RuntimeConfig
from runtime.backend import DockerRuntime


@pytest.mark.parametrize("timeout", [True, False])
def test_failed_snapshot_preserves_container_until_retry(timeout: bool) -> None:
	session = SimpleNamespace(
		task_id="snapshot-test",
		run_spec=SimpleNamespace(
			runtime=RuntimeConfig(mode="docker", keep_committed_snapshot=True)
		),
	)
	runtime = DockerRuntime(container_id="test-container", container_stopped=True)
	with patch("runtime.backend.subprocess.run") as run:
		if timeout:
			run.side_effect = subprocess.TimeoutExpired("docker commit", 1200)
		else:
			run.return_value = subprocess.CompletedProcess([], 1, "", "disk full")
		with pytest.raises((subprocess.TimeoutExpired, RuntimeError)):
			runtime.snapshot(session)
		run.reset_mock()
		runtime.cleanup(session)
		run.assert_not_called()
		assert runtime.container_id == "test-container"

		run.side_effect = None
		run.return_value = subprocess.CompletedProcess([], 0, "", "")
		assert runtime.snapshot(session)
		runtime.cleanup(session)
		assert run.call_args.args[0] == ["docker", "rm", "-f", "test-container"]
		assert runtime.container_removed
