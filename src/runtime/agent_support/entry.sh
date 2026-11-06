# AEBench agent entry point, the same for every agent; agent-specific setup
# belongs in agents/<name>/agent.sh. Runs as the agent user with stdin from
# /dev/null, inside the workspace.

# Credentials declared in agent.toml. They are kept off the `docker exec` command
# line, and the file is removed once loaded.
secrets_file="$AEBENCH_AGENT_SUPPORT_DIR/aebench/secrets.sh"
if [ -f "$secrets_file" ]; then
	. "$secrets_file"
	rm -f "$secrets_file"
fi

PROMPT="$(cat "$AEBENCH_PROMPT_FILE")"
export PROMPT

# $BASH is this shell; under command monitoring it is the real bash, so the
# agent's own script is not recorded as one of its commands.
exec "$BASH" "$AEBENCH_AGENT_DIR/agent.sh"
