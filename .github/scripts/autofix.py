#!/usr/bin/env python3
"""
Auto-fix CI failures using Claude.
Triggered by workflow_run events. Reads failure logs, runs an agentic
repair loop with Claude, commits any fixes, and posts a PR comment.

Only runs on same-repo PRs (not forks), since it pushes commits back
to the PR branch using the GITHUB_TOKEN.
"""
import json, os, subprocess, sys, urllib.request, urllib.error

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from the checked-out repository",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file in the repository. Cannot write to .github/ or use path traversal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a read-only bash command to inspect the repository (ls, find, grep, cat, linting tools, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]

# Patterns that could exfiltrate secrets or make unintended network requests
_BASH_BLOCKED = ("curl", "wget", "nc ", "ncat", "netcat", "/dev/tcp",
                 "ANTHROPIC", "GH_TOKEN", "GITHUB_TOKEN", "SECRET")


def claude(messages, system):
    payload = json.dumps({
        "model": "claude-opus-4-7",
        "max_tokens": 4096,
        "system": system,
        "tools": TOOLS,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode()}")


def use_tool(name, inp):
    if name == "read_file":
        try:
            with open(inp["path"].lstrip("/")) as f:
                return f.read()
        except Exception as e:
            return str(e)

    if name == "write_file":
        path = inp["path"].lstrip("/")
        if not path:
            return "Error: empty path"
        if ".." in path.split("/"):
            return "Error: path traversal not allowed"
        if path.startswith(".github/"):
            return "Error: writes to .github/ are not permitted"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(inp["content"])
        return f"wrote {path}"

    if name == "run_bash":
        cmd = inp["command"]
        for blocked in _BASH_BLOCKED:
            if blocked in cmd:
                return f"Error: command contains blocked pattern '{blocked}'"
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            return (r.stdout + r.stderr)[:8000]
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 60 seconds"

    return f"unknown tool: {name}"


def main():
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        event = json.load(f)

    repo = os.environ["REPO"]
    run = event["workflow_run"]
    prs = run.get("pull_requests", [])

    if not prs:
        print("No PR associated with this run, skipping")
        return

    pr = prs[0]
    pr_number = str(pr["number"])
    head_ref = pr["head"]["ref"]
    head_sha = run["head_sha"]
    run_id = str(run["id"])

    # Only process same-repo PRs — forks can't receive pushes via GITHUB_TOKEN
    if pr["head"]["repo"]["full_name"] != repo:
        print(f"Skipping fork PR from {pr['head']['repo']['full_name']}")
        return

    # Anti-loop: skip if head commit was already an autofix
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/commits/{head_sha}", "--jq", ".commit.message"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and "[autofix]" in r.stdout:
        print("Head commit is already an autofix, skipping")
        return

    # Fetch failure logs
    print(f"Fetching logs for run {run_id}...")
    r = subprocess.run(
        ["gh", "run", "view", run_id, "--log-failed", "--repo", repo],
        capture_output=True, text=True,
    )
    logs = r.stdout.strip() if r.returncode == 0 else r.stderr.strip()
    if not logs:
        print("No failure logs found, skipping")
        return

    MAX_LOG = 25000
    if len(logs) > MAX_LOG:
        cut = logs.rfind("\n", 0, MAX_LOG)
        logs = logs[:cut if cut > 0 else MAX_LOG] + "\n...(truncated)"

    # PR diff for context
    r = subprocess.run(["gh", "pr", "diff", pr_number, "--repo", repo], capture_output=True, text=True)
    diff = r.stdout
    MAX_DIFF = 15000
    if len(diff) > MAX_DIFF:
        cut = diff.rfind("\n", 0, MAX_DIFF)
        diff = diff[:cut if cut > 0 else MAX_DIFF] + "\n...(truncated)"

    system = (
        "You are an automated CI repair bot. Fix CI failures with minimal, targeted changes. "
        "Do not refactor, add features, or make unrelated stylistic changes."
    )
    prompt = (
        f"CI failed on PR #{pr_number}.\n\n"
        "## Failure Logs\n```\n" + logs + "\n```\n\n"
        "## PR Diff\n```diff\n" + diff + "\n```\n\n"
        "Use the tools to read relevant files and fix what's failing. "
        "After all changes are made, summarize what you fixed in 1-2 sentences."
    )

    messages = [{"role": "user", "content": prompt}]
    written = []
    explanation = ""

    for _ in range(15):
        try:
            resp = claude(messages, system)
        except RuntimeError as e:
            print(f"Claude API error: {e}")
            break

        messages.append({"role": "assistant", "content": resp["content"]})
        stop_reason = resp.get("stop_reason")

        if stop_reason == "end_turn":
            for b in resp["content"]:
                if isinstance(b, dict) and b.get("type") == "text":
                    explanation = b["text"]
            break

        if stop_reason == "tool_use":
            results = []
            for b in resp["content"]:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                print(f"  [{b['name']}] {str(b['input'])[:120]}")
                out = use_tool(b["name"], b["input"])
                if b["name"] == "write_file" and not str(out).startswith("Error"):
                    written.append(b["input"]["path"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b["id"],
                    "content": str(out),
                })
            messages.append({"role": "user", "content": results})
        else:
            print(f"Stopping: unexpected stop_reason '{stop_reason}'")
            break

    if not written:
        body = (
            "## Auto-fix Attempt\n\n"
            "I analyzed the CI failures but could not determine an automated fix. "
            "Please review manually.\n\n---\n*Automated by Claude*"
        )
    else:
        subprocess.run(["git", "config", "user.name", "claude-autofix[bot]"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "claude-autofix@noreply.github.com"], check=True
        )
        subprocess.run(["git", "add", "--"] + [p.lstrip("/") for p in written], check=True)
        r = subprocess.run(
            ["git", "commit", "-m",
             f"fix: auto-fix CI failures [autofix]\n\n{explanation[:400]}"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"Nothing to commit or commit failed: {r.stderr}")
            return
        r = subprocess.run(
            ["git", "push", "origin", f"HEAD:refs/heads/{head_ref}"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"Push failed: {r.stderr}")
            return
        body = (
            "## Auto-fix Applied\n\n"
            f"Modified: {', '.join(f'`{p}`' for p in written)}\n\n"
            f"{explanation}\n\n---\n*Automated by Claude*"
        )

    with open("/tmp/autofix-comment.md", "w") as f:
        f.write(body)
    subprocess.run(
        ["gh", "pr", "comment", pr_number,
         "--body-file", "/tmp/autofix-comment.md",
         "--repo", repo],
        check=True,
    )
    print("Done")


main()
