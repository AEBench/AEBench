from __future__ import annotations
from collections.abc import Sequence

from evaluator.oracles.reporting import BaseCheck
from evaluator.oracles.bases import CaseOracleArtifactBuildBase

class OracleArtifactBuild(CaseOracleArtifactBuildBase):
    def requirements(self) -> Sequence[BaseCheck]:
        return (
            # Checks if the `xx/failslow:0.1` Docker image was built using `docker image inspect`.
            self.command_check(
                name="docker_image_exists",
                cwd=self.workspace_path(),
                cmd=("docker", "image", "inspect", "xx/failslow:0.1"),
                timeout_seconds=30.0,
            ),
            # Checks if the `Sieve_net` Docker network exists.
            self.command_check(
                name="docker_network_exists",
                cwd=self.workspace_path(),
                cmd=("docker", "network", "inspect", "Sieve_net"),
                timeout_seconds=30.0,
            ),
            # Uses `docker exec failslow1` to check for `failslow-1.0-SNAPSHOT-jar-with-dependencies.jar`.
            self.command_check(
                name="sieve_jar_exists",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow1:/failslow/target/failslow-1.0-SNAPSHOT-jar-with-dependencies.jar - > /dev/null"),
                timeout_seconds=30.0,
            ),
            # Uses `docker exec failslow1` to check for `apache-zookeeper-3.10.0-SNAPSHOT-bin.tar.gz`.
            self.command_check(
                name="zookeeper_archive_exists",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow1:/apache-zookeeper-3.10.0-SNAPSHOT-bin.tar.gz - > /dev/null"),
                timeout_seconds=30.0,
            ),
            # Uses `docker exec failslow1` to check for `zoo.json`.
            self.command_check(
                name="zoo_json_exists",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow1:/failslow/zoo.json - > /dev/null"),
                timeout_seconds=30.0,
            ),
            # Uses `docker exec failslow1` to check for `zooTOP2IO.json`.
            self.command_check(
                name="zoo_top2io_json_exists",
                cwd=self.workspace_path(),
                cmd=("sh", "-c", "docker cp failslow1:/failslow/zooTOP2IO.json - > /dev/null"),
                timeout_seconds=30.0,
            ),
        )