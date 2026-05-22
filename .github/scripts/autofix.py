#!/usr/bin/env python3
"""
Auto-fix CI failures using Claude.
Triggered by workflow_run events. Reads failure logs, runs an agentic
repair loop with Claude, commits any fixes, and posts a PR comment.
"""
import json, os, subprocess, urllib.request

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
        "description": "Write or overwrite a file in the repository",
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
        "description": "Run a bash command to inspect the repository (ls, find, grep, cat, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


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
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def use_tool(name, inp):
    if name == "read_file":
        try:
            return open(inp["path"].lstrip("/")).read()
        except Exception as e:
            return str(e)
    if name == "write_file":
        p = inp["path"].lstrip("/")
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        open(p, "w").write(inp["content"])
        return f"wrote {p}"
    if name == "run_bash":
        r = subprocess.run(
            inp["command"], shell=True, capture_output=True, text=True, timeout=60
        )
        return (r.stdout + r.stderr)[:8000]
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

    pr_number = str(prs[0]["number"])
    head_ref = prs[0]["head"]["ref"]
    head_sha = run["head_sha"]
    run_id = str(run["id"])

    # Anti-loop: skip if the head commit was already an autofix
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/commits/{head_sha}", "--jq", ".commit.message"],
        capture_output=True, text=True,
    )
    if "[autofix]" in r.stdout:
        print("Head commit is already an autofix, skipping")
        return

    # Fetch failure logs
    print(f"Fetching logs for run {run_id}...")
    r = subprocess.run(
        ["gh", "run", "view", run_id, "--log-failed", "--repo", repo],
        capture_output=True, text=True,
    )
    logs = (r.stdout or r.stderr).strip()[:25000]
    if not logs:
        print("No failure logs found, skipping")
        return

    # PR diff for context
    r = subprocess.run(
        ["gh", "pr", "diff", pr_number, "--repo", repo], capture_output=True, text=True
    )
    diff = r.stdout[:15000]

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
        resp = claude(messages, system)
        messages.append({"role": "assistant", "content": resp["content"]})

        if resp["stop_reason"] == "end_turn":
            for b in resp["content"]:
                if isinstance(b, dict) and b.get("type") == "text":
                    explanation = b["text"]
            break

        if resp["stop_reason"] == "tool_use":
            results = []
            for b in resp["content"]:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                print(f"  [{b['name']}] {str(b['input'])[:120]}")
                out = use_tool(b["name"], b["input"])
                if b["name"] == "write_file":
                    written.append(b["input"]["path"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b["id"],
                    "content": str(out),
                })
            messages.append({"role": "user", "content": results})

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
        subprocess.run(
            ["git", "add", "--"] + [p.lstrip("/") for p in written], check=True
        )
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

    open("/tmp/autofix-comment.md", "w").write(body)
    subprocess.run(
        ["gh", "pr", "comment", pr_number,
         "--body-file", "/tmp/autofix-comment.md",
         "--repo", repo],
        check=True,
    )
    print("Done")


main()
