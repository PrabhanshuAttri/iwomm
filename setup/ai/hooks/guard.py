#!/usr/bin/env python3
"""PreToolUse guard for Claude Code.

Confines every tool call to the project root ($CLAUDE_PROJECT_DIR):
no reads, writes, searches, or shell paths outside it, no system changes,
no SSH or LAN access, and every download lands in <root>/tmp first.

Exit 0 allows the call. Exit 2 blocks it; stderr is fed back to Claude.
Fails closed on anything it cannot parse.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HOME = Path(os.path.realpath(Path.home()))
SCRATCH_DIR = "tmp"
DEV_OK = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}
EXEC_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/opt/homebrew/bin")
FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Glob", "Grep", "LS"}
# Shared AI config (rules, skills, plugins) that read tools may open from any project.
READABLE_DIRS = (HOME / ".ai", HOME / ".claude")
# Never readable, nor searchable through a parent directory.
SECRET_FILES = (HOME / ".claude" / ".credentials.json",)
GITIGNORE_ADDABLE = {"/tmp/", "tmp/", "/tmp", "tmp"}

PROTECTED_NAMES = {
    ".gitignore", "CODEOWNERS",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Cargo.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "go.sum",
    "composer.lock", "Gemfile.lock",
}

# Pattern rules applied to the raw command string.
BASH_RULES: list[tuple[str, str]] = [
    # Destructive filesystem
    (r"\brm\b(?=[^;&|]*(\s-[a-zA-Z]*[rR]|\s--recursive))(?=[^;&|]*(\s-[a-zA-Z]*f|\s--force))"
     r"[^;&|]*\s((~|\$HOME)(\s|$|/)|/\*?(\s|$)|\*(\s|$)|\.\.?/?\*?(\s|$))",
     "recursive force delete on root, home, cwd, parent, or bare glob"),
    (r"\b(mkfs|fdisk|diskutil\s+erase)\b|\bdd\s+[^;&|]*of=/dev/", "disk-level write"),
    (r"\bchmod\s+(-R\s+)?0?777\b", "world-writable permissions"),
    (r"(^|[\s;&|(])(sudo|doas|su|pkexec)(\s|$)", "privilege escalation"),
    # Git
    (r"\bgit\s+push\b[^;&|]*(\s-f\b|--force)", "force push"),
    (r"\bgit\s+push\b[^;&|]*\b(origin\s+)?(main|master)\b", "push to main/master"),
    (r"--no-verify\b|\bgit\s+commit\b[^;&|]*\s-n\b", "skipping git hooks"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\b[^;&|]*-[a-zA-Z]*f", "git clean -f"),
    (r"\bgit\s+checkout\s+(--\s+)?\.(\s|$)|\bgit\s+restore\s+(--staged\s+)?\.(\s|$)",
     "discarding all working changes"),
    (r"\bgit\s+stash\s+(drop|clear)\b", "dropping stashes"),
    (r"\bgit\s+(config\s+--(global|system)|filter-branch|filter-repo)\b",
     "global git config or history rewrite"),
    (r"(?i)\bgit_ssh(_command)?\b|core\.sshcommand", "custom git ssh transport"),
    # Supply chain
    (r"\b(curl|wget|fetch)\b[^;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "piping download into a shell"),
    (r"\b(curl|wget)\b[^;&|]*\|\s*(python3?|node|perl|ruby|bash|sh|zsh)\b",
     "piping download into an interpreter"),
    (r"\b(pastebin|transfer\.sh|0x0\.st|termbin|ix\.io)\b", "upload to paste service"),
    # Data / prod
    (r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE?)\b", "destructive SQL"),
    (r"\bDELETE\s+FROM\s+\w+\s*;?\s*($|[\"'])", "DELETE without WHERE"),
    (r"\bterraform\s+(apply|destroy)\b|\bpulumi\s+(up|destroy)\b", "infra apply/destroy"),
    (r"(--context|--profile|--project|--subscription)[=\s]+\S*prod", "targeting a prod context"),
    # Secrets and remote hosts
    (r"(^|[\s<'\"=])\.env(\.[\w-]+)?\b(?!\.example|\.sample|\.template)", "reading .env secrets"),
    (r"\b(printenv|env)\s*($|\|)|\bset\s*\|", "dumping environment"),
    (r"(?i)\b(ssh|sftp|scp|telnet|smb|cifs|nfs|afp|vnc|rdp|rsync)://", "remote shell or file-share URL"),
    (r"docker\.sock", "docker socket access"),
    (r"\b(DOCKER_HOST|CONTAINER_HOST|KUBECONFIG)=", "pointing a tool at another host"),
    (r"['\"]/['\"]", "filesystem root referenced in code"),
]

PRIVATE_NET = re.compile(
    r"(?<![\w.])(10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}"
    r"|169\.254(\.\d{1,3}){2}|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])(\.\d{1,3}){2})(?![\d])"
    r"|\b[\w-]+\.(local|lan|localdomain|home\.arpa|ts\.net)\b"
    r"|\b(fe80|fd[0-9a-f]{2}):[0-9a-f:]+",
    re.IGNORECASE,
)

NET_BINARIES = {
    "ssh", "scp", "sftp", "ssh-copy-id", "ssh-keyscan", "ssh-add", "ssh-agent", "sshpass",
    "sshfs", "autossh", "mosh", "telnet", "ftp", "tftp", "lftp", "rsh", "rlogin", "rcp",
    "nc", "ncat", "netcat", "socat", "nmap", "masscan", "zmap", "arp", "arp-scan", "arping",
    "avahi-browse", "avahi-resolve", "dns-sd", "smbclient", "smbutil", "showmount", "rpcinfo",
    "snmpwalk", "snmpget", "adb", "fastboot", "tailscale", "zerotier-cli", "wakeonlan", "wol",
    "etherwake", "kubectl", "helm", "k9s", "kubeadm", "k3s", "talosctl", "ansible",
    "ansible-playbook", "ansible-inventory", "ansible-galaxy",
}

SYSTEM_BINARIES = {
    "systemctl", "launchctl", "service", "crontab", "at", "apt", "apt-get", "dpkg", "dnf", "yum",
    "rpm", "pacman", "zypper", "apk", "snap", "flatpak", "port", "choco", "winget",
    "softwareupdate", "scutil", "networksetup", "pmset", "csrutil", "spctl", "ifconfig",
    "iptables", "ip6tables", "nft", "ufw", "pfctl", "route", "mount", "umount", "diskutil",
    "hdiutil", "fdisk", "parted", "useradd", "userdel", "usermod", "groupadd", "passwd", "chsh",
    "visudo", "shutdown", "reboot", "halt", "poweroff", "killall", "pkill", "modprobe", "insmod",
    "rmmod", "ldconfig", "update-alternatives", "osascript", "security", "secret-tool", "corepack",
}

TOOLCHAIN_MANAGERS = {
    "nvm", "pyenv", "rbenv", "asdf", "mise", "rustup", "volta", "fnm", "sdk", "pipx",
    "conda", "mamba", "micromamba", "brew", "ollama",
}
READONLY_SUBCOMMANDS = {
    "list", "ls", "current", "which", "version", "--version", "-v", "show", "info", "doctor",
    "help", "--help", "-h", "search", "outdated", "deps", "config", "env", "ps", "uses",
}

GIT_COMMIT_CREATORS = {"commit", "commit-tree", "merge", "rebase", "cherry-pick", "revert", "am", "pull"}
GIT_PUSHERS = {"push", "send-pack", "request-pull", "send-email"}
GH_PUBLISHERS = {("pr", "create"), ("pr", "merge"), ("repo", "sync"), ("release", "create"),
                 ("release", "upload"), ("repo", "create"), ("gist", "create")}

SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")"}
WRAPPERS = {"env", "nohup", "time", "nice", "command", "exec", "stdbuf", "xargs", "builtin",
            "timeout", "caffeinate", "ionice", "chronic", "watch"}
ASSIGN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)
SPECIAL_CHARS = r"\s'\"`;|&<>(){}\[\],"
EMBED_ABS = re.compile(rf"(?<![\w.:/~$@%+=\-])(/[^{SPECIAL_CHARS}=]+)")
EMBED_HOME = re.compile(rf"(?<![\w/.$-])(~/[^{SPECIAL_CHARS}]*|\$\{{?HOME\}}?[^{SPECIAL_CHARS}]*)")
EMBED_PARENT = re.compile(rf"(?<![\w./])(\.\.(?:/[^{SPECIAL_CHARS}]*)?)(?=[{SPECIAL_CHARS}]|$)")

CURL_ARG_SHORT = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
WGET_ARG_SHORT = set("OoaPiBetTwQUlARDIX")
GIT_CLONE_ARG = {"-b", "--branch", "-o", "--origin", "-c", "--config", "--depth", "--reference",
                 "--reference-if-able", "--separate-git-dir", "-j", "--jobs", "-u",
                 "--upload-pack", "--template", "--shallow-since", "--shallow-exclude",
                 "--server-option", "--bundle-uri"}


# --------------------------------------------------------------------------- helpers

def block(reason: str) -> None:
    print(f"BLOCKED by ~/.ai/hooks/guard.py: {reason}. "
          "Do not retry with an equivalent command, alias, script, or encoding. "
          "Ask the user.", file=sys.stderr)
    sys.exit(2)


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def project_root(cwd: str) -> Path:
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(os.path.realpath(env_root))
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return Path(os.path.realpath(out.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(os.path.realpath(cwd))


def expand(raw: str) -> str:
    if raw == "~" or raw.startswith("~/"):
        raw = str(HOME) + raw[1:]
    return re.sub(r"\$\{HOME\}|\$HOME\b", str(HOME), raw)


def resolve(raw: str, base: Path) -> Path:
    p = Path(expand(raw))
    if not p.is_absolute():
        p = base / p
    return Path(os.path.realpath(p))


def is_exec_path(raw: str) -> bool:
    norm = os.path.normpath(expand(raw))
    return any(norm == d or norm.startswith(d + "/") for d in EXEC_DIRS)


def is_external_abs(raw: str) -> bool:
    """True when an absolute-looking string names a real top-level location."""
    parts = Path(raw).parts
    if len(parts) == 1:
        return True
    top = parts[1]
    return any(c in top for c in "*?[") or os.path.lexists("/" + top)


def check_path(raw: str, base: Path, root: Path, ctx: str, allow_exec: bool = False) -> Path | None:
    if not raw:
        return None
    if raw in DEV_OK or raw.startswith("/dev/fd/"):
        return None
    if raw.startswith("~") and not (raw == "~" or raw.startswith("~/")):
        block(f"{ctx} references another user's home ({raw})")
    expanded = expand(raw)
    if expanded.startswith("/") and not is_external_abs(expanded):
        return None  # e.g. "/api/v1" in a grep pattern, not a filesystem path
    if allow_exec and is_exec_path(raw):
        return None
    p = resolve(raw, base)
    if str(p) in DEV_OK:
        return p
    if not is_within(p, root):
        block(f"{ctx} touches {p}, outside the project root {root}")
    return p


def tokenize(cmd: str) -> list[str]:
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def split_segments(toks: list[str]) -> list[tuple[str | None, list[str]]]:
    segs: list[tuple[str | None, list[str]]] = []
    cur: list[str] = []
    sep: str | None = None
    for t in toks:
        if t in SEPARATORS:
            if cur:
                segs.append((sep, cur))
            cur, sep = [], t
        else:
            cur.append(t)
    if cur:
        segs.append((sep, cur))
    return segs


def split_redirects(seg: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    args: list[str] = []
    redirs: list[tuple[str, str]] = []
    i = 0
    while i < len(seg):
        t = seg[i]
        if re.fullmatch(r"[<>&|]+", t) and ("<" in t or ">" in t):
            target = seg[i + 1] if i + 1 < len(seg) else ""
            i += 2
            if t in ("<<", "<<-", "<<<"):
                continue  # heredoc delimiter / here-string, not a path
            if t in (">&", "<&") and (target.isdigit() or target == "-"):
                continue
            redirs.append((t, target))
            continue
        if args and t.isdigit() and i + 1 < len(seg) and re.fullmatch(r"[<>&]+", seg[i + 1]):
            i += 1  # fd number before a redirect, e.g. 2>
            continue
        args.append(t)
        i += 1
    return args, redirs


def opt_value(toks: list[str], names: tuple[str, ...]) -> str | None:
    for i, t in enumerate(toks):
        if t in names and i + 1 < len(toks):
            return toks[i + 1]
        for n in names:
            if n.startswith("--") and t.startswith(n + "="):
                return t.split("=", 1)[1]
    return None


def positionals(toks: list[str], arg_opts: set[str]) -> list[str]:
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if t in arg_opts:
            skip = True
            continue
        if t.startswith("-"):
            continue
        out.append(t)
    return out


def repo_name(url: str) -> str:
    name = re.split(r"[/:]", url.rstrip("/"))[-1]
    return name[:-4] if name.endswith(".git") else name


# --------------------------------------------------------------------------- downloads

def curl_targets(toks: list[str], cwd: Path) -> list[Path]:
    outs: list[str] = []
    remote, outdir = False, None
    i = 1
    while i < len(toks):
        t = toks[i]
        if t == "--output" and i + 1 < len(toks):
            outs.append(toks[i + 1]); i += 2; continue
        if t.startswith("--output="):
            outs.append(t.split("=", 1)[1])
        elif t == "--output-dir" and i + 1 < len(toks):
            outdir = toks[i + 1]; i += 2; continue
        elif t.startswith("--output-dir="):
            outdir = t.split("=", 1)[1]
        elif t in ("--remote-name", "--remote-name-all", "--remote-header-name"):
            remote = True
        elif re.fullmatch(r"-[A-Za-z#:]+", t):
            flags = t[1:]
            for j, c in enumerate(flags):
                if c in ("O", "J"):
                    remote = True
                if c in CURL_ARG_SHORT:
                    val = flags[j + 1:]
                    if not val and i + 1 < len(toks):
                        val = toks[i + 1]; i += 1
                    if c == "o":
                        outs.append(val)
                    break
        i += 1
    base = resolve(outdir, cwd) if outdir else cwd
    targets = [resolve(o, base if outdir else cwd) for o in outs if o and o != "-"]
    if remote:
        targets.append(base)
    return targets


def wget_targets(toks: list[str], cwd: Path) -> list[Path]:
    out, prefix = None, None
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in ("-nv", "-nc", "-nd", "-nH", "-np", "-nH"):
            i += 1; continue
        if t.startswith("--output-document="):
            out = t.split("=", 1)[1]
        elif t == "--output-document" and i + 1 < len(toks):
            out = toks[i + 1]; i += 1
        elif t.startswith("--directory-prefix="):
            prefix = t.split("=", 1)[1]
        elif t == "--directory-prefix" and i + 1 < len(toks):
            prefix = toks[i + 1]; i += 1
        elif re.fullmatch(r"-[A-Za-z]+", t):
            flags = t[1:]
            for j, c in enumerate(flags):
                if c in WGET_ARG_SHORT:
                    val = flags[j + 1:]
                    if not val and i + 1 < len(toks):
                        val = toks[i + 1]; i += 1
                    if c == "O":
                        out = val
                    elif c == "P":
                        prefix = val
                    break
        i += 1
    if out == "-":
        return []
    if out:
        return [resolve(out, cwd)]
    return [resolve(prefix, cwd) if prefix else cwd]


def download_targets(cmd: str, toks: list[str], cwd: Path) -> list[Path] | None:
    """Paths a download command writes to. None when the command is not a download."""
    if cmd == "curl":
        return curl_targets(toks, cwd)
    if cmd == "wget":
        return wget_targets(toks, cwd)
    if cmd == "aria2c":
        d = opt_value(toks, ("-d", "--dir"))
        return [resolve(d, cwd) if d else cwd]
    if cmd == "git":
        base = cwd
        c_dir = opt_value(toks[:toks.index("clone")] if "clone" in toks else toks, ("-C",))
        if c_dir:
            base = resolve(c_dir, cwd)
        if "clone" not in toks:
            return None
        pos = positionals(toks[toks.index("clone") + 1:], GIT_CLONE_ARG)
        if not pos:
            return None
        return [resolve(pos[1], base) if len(pos) > 1 else base / repo_name(pos[0])]
    if cmd == "gh" and len(toks) > 2:
        sub = toks[1:3]
        if sub in (["release", "download"], ["run", "download"]):
            d = opt_value(toks, ("-D", "--dir"))
            return [resolve(d, cwd) if d else cwd]
        if sub in (["repo", "clone"], ["gist", "clone"]):
            pos = positionals(toks[3:], set())
            if not pos:
                return None
            return [resolve(pos[1], cwd) if len(pos) > 1 else cwd / repo_name(pos[0])]
    if cmd in ("pip", "pip3") and "download" in toks:
        d = opt_value(toks, ("-d", "--dest"))
        return [resolve(d, cwd) if d else cwd]
    if cmd in ("huggingface-cli", "hf") and "download" in toks:
        d = opt_value(toks, ("--local-dir",))
        if not d:
            block("model download without --local-dir writes to ~/.cache; use --local-dir tmp/<name>")
        return [resolve(d, cwd)]
    return None


# --------------------------------------------------------------------------- command checks

def git_subcommand(toks: list[str], cwd: Path) -> tuple[str | None, list[str]]:
    """Return git's subcommand (aliases expanded) and the tokens after it."""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            if t == "-c" and i + 1 < len(toks) and toks[i + 1].lower().startswith("alias."):
                block("defining a git alias inline")
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break
    if i >= len(toks):
        return None, []
    sub, rest = toks[i], toks[i + 1:]
    try:
        out = subprocess.run(["git", "config", "--get", f"alias.{sub}"], cwd=cwd,
                             capture_output=True, text=True, timeout=3)
        alias = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        alias = ""
    if alias:
        if alias.startswith("!"):
            block(f"git alias `{sub}` runs a shell command")
        expanded = alias.split()
        return expanded[0], expanded[1:] + rest
    return sub, rest


def check_git(cmd: str, toks: list[str], cwd: Path) -> None:
    if cmd == "git":
        sub, rest = git_subcommand(toks, cwd)
        if sub in GIT_COMMIT_CREATORS:
            block(f"`git {sub}` creates commits; committing is reserved for the user")
        if sub in GIT_PUSHERS or (sub in ("lfs", "subtree") and "push" in rest):
            block(f"`git {sub}` publishes to a remote; pushing is reserved for the user")
        if sub == "notes" and rest[:1] in (["add"], ["append"], ["edit"], ["copy"]):
            block("`git notes` creates commits")
    if cmd == "gh" and len(toks) > 2:
        if (toks[1], toks[2]) in GH_PUBLISHERS:
            block(f"`gh {toks[1]} {toks[2]}` pushes or publishes; reserved for the user")


def check_system_and_network(cmd: str, toks: list[str], root: Path, cwd: Path, venv_active: bool) -> None:
    check_git(cmd, toks, cwd)
    if cmd in NET_BINARIES:
        block(f"`{cmd}` reaches other machines or devices")
    if cmd in SYSTEM_BINARIES:
        block(f"`{cmd}` changes system state")
    if cmd == "ip" and set(toks) & {"add", "del", "delete", "flush", "set", "change", "replace"}:
        block("changing network configuration")
    if cmd == "sysctl" and any(t == "-w" or "=" in t for t in toks[1:]):
        block("changing kernel parameters")
    if cmd == "defaults" and set(toks) & {"write", "delete", "import"}:
        block("changing macOS defaults")
    if cmd in TOOLCHAIN_MANAGERS:
        sub = next((t for t in toks[1:] if not t.startswith("-")), toks[1] if len(toks) > 1 else "")
        if sub not in READONLY_SUBCOMMANDS:
            block(f"`{cmd} {sub}` changes global toolchains or system storage")
    if cmd in ("rsync", "scp") and any(":" in t and not t.startswith(("-", "/", ".")) for t in toks[1:]):
        block("copying to or from another host")
    if cmd == "rsync" and ("-e" in toks or any(t.startswith("--rsh") for t in toks)):
        block("rsync over a remote shell")

    # Global package installs
    if cmd in ("npm", "pnpm", "bun", "yarn") and (
        set(toks) & {"-g", "--global", "global"} or (len(toks) > 1 and toks[1] == "link")
    ):
        block(f"global {cmd} install or link")
    if cmd in ("npm", "pnpm") and len(toks) > 2 and toks[1] == "config" and toks[2] in ("set", "delete", "edit") \
            and "--location=project" not in toks:
        block("changing user-level package manager config")
    if cmd in ("npm", "pnpm", "yarn") and len(toks) > 1 and toks[1] in ("login", "adduser", "logout"):
        block("changing registry credentials")
    if cmd == "cargo" and len(toks) > 1 and toks[1] == "install":
        r = opt_value(toks, ("--root",))
        if not r:
            block("cargo install writes to ~/.cargo; pass --root inside the project or ask")
        check_path(r, cwd, root, "cargo --root")
    if cmd == "go" and len(toks) > 1 and toks[1] == "install":
        block("go install writes to GOBIN outside the project")
    if cmd == "gem" and len(toks) > 1 and toks[1] == "install":
        d = opt_value(toks, ("-i", "--install-dir"))
        if not d:
            block("gem install outside the project; use bundler with a vendor path")
        check_path(d, cwd, root, "gem --install-dir")
    if cmd == "composer" and len(toks) > 1 and toks[1] == "global":
        block("global composer install")
    if cmd == "dotnet" and "tool" in toks and set(toks) & {"-g", "--global"}:
        block("global dotnet tool install")
    if cmd == "deno" and "install" in toks and set(toks) & {"-g", "--global"}:
        block("global deno install")
    if cmd == "uv" and (
        (len(toks) > 1 and toks[1] == "tool" and "run" not in toks)
        or (len(toks) > 2 and toks[1] == "python" and toks[2] in ("install", "uninstall", "pin"))
        or "--system" in toks
    ):
        block("uv command that changes global tools or the system interpreter")

    # pip outside a project venv
    pip_toks, interp = None, toks[0]
    if cmd in ("pip", "pip3"):
        pip_toks = toks
    elif re.fullmatch(r"python(3(\.\d+)?)?", cmd) and len(toks) > 2 and toks[1:3] == ["-m", "pip"]:
        pip_toks = toks[2:]
    if pip_toks and set(pip_toks) & {"install", "uninstall"}:
        if "--user" in pip_toks or "--break-system-packages" in pip_toks:
            block("pip install into user or system site-packages")
        target = opt_value(pip_toks, ("-t", "--target", "--prefix", "--root"))
        interp_in_root = "/" in interp and is_within(resolve(interp, cwd), root)
        if target:
            check_path(target, cwd, root, "pip target")
        elif not (interp_in_root or venv_active):
            block("pip install outside a project venv; use .venv/bin/pip (or activate .venv in the same command)")

    # Containers
    if cmd in ("docker", "podman", "nerdctl"):
        if any(t in ("-H", "--host", "--context", "-c") or t.startswith(("--host=", "--context=")) for t in toks):
            block("container CLI pointed at another host or context")
        if set(toks) & {"--privileged", "--network=host", "--net=host", "--pid=host", "--ipc=host",
                        "--userns=host", "--uts=host", "--cap-add=ALL"}:
            block("container with host-level access")
        for i, t in enumerate(toks):
            if t in ("--network", "--net", "--pid", "--ipc") and i + 1 < len(toks) and toks[i + 1] == "host":
                block("container with host-level access")
        if len(toks) > 1 and toks[1] in ("login", "logout", "rmi"):
            block(f"`{cmd} {toks[1]}` changes credentials or shared images")
        if "prune" in toks or (set(toks[1:3]) & {"volume", "image", "network", "system"} and set(toks) & {"rm", "prune"}):
            block("pruning or removing shared container resources")
        for i, t in enumerate(toks):
            spec = None
            if t in ("-v", "--volume") and i + 1 < len(toks):
                spec = toks[i + 1]
            elif t.startswith("--volume="):
                spec = t.split("=", 1)[1]
            elif re.fullmatch(r"-v[/~.$].*", t):
                spec = t[2:]
            elif t == "--mount" and i + 1 < len(toks):
                m = re.search(r"(?:source|src)=([^,]+)", toks[i + 1])
                spec = m.group(1) if m else None
            elif t.startswith("--mount="):
                m = re.search(r"(?:source|src)=([^,]+)", t)
                spec = m.group(1) if m else None
            if spec:
                src = spec.split(":", 1)[0]
                if src.startswith(("/", "~", ".", "$")):
                    p = resolve(src, cwd)
                    if not is_within(p, root):
                        block(f"container mount of {p}, outside the project root")


def check_bash(cmd_str: str, cwd: Path, root: Path) -> None:
    for pattern, reason in BASH_RULES:
        if re.search(pattern, cmd_str, re.IGNORECASE):
            block(reason)
    m = PRIVATE_NET.search(cmd_str)
    if m:
        block(f"private network address or LAN hostname ({m.group(0)}); local devices are off limits")

    for m in EMBED_ABS.finditer(cmd_str):
        check_path(m.group(1), cwd, root, "command", allow_exec=True)
    for m in EMBED_HOME.finditer(cmd_str):
        check_path(m.group(1), cwd, root, "command")
    for m in EMBED_PARENT.finditer(cmd_str):
        check_path(m.group(1), cwd, root, "command")

    try:
        toks = tokenize(cmd_str)
    except ValueError as e:
        block(f"could not parse command ({e})")
        return

    tmp_dir = root / SCRATCH_DIR
    eff_cwd = cwd
    venv_active = False
    prev_download = False

    for sep, seg in split_segments(toks):
        args, redirs = split_redirects(seg)

        i = 0
        while i < len(args):
            t = args[i]
            a = ASSIGN.match(t)
            if a:
                for part in a.group(2).split(":"):
                    if part.startswith(("/", "~", "$HOME", "${HOME}", "..")):
                        check_path(part, eff_cwd, root, f"variable {a.group(1)}", allow_exec=True)
                i += 1
                continue
            if os.path.basename(t) in WRAPPERS:
                wrapper = os.path.basename(t)
                i += 1
                while i < len(args) and (args[i].startswith("-") or ASSIGN.match(args[i])):
                    i += 1
                if wrapper == "timeout" and i < len(args) and re.match(r"^\d", args[i]):
                    i += 1
                continue
            break
        ctoks = args[i:]

        for op, target in redirs:
            check_path(target, eff_cwd, root, f"redirect {op}")
        if not ctoks:
            prev_download = False
            continue

        cmd = os.path.basename(ctoks[0])
        check_path(ctoks[0], eff_cwd, root, "executable", allow_exec=True)

        if cmd in ("cd", "pushd"):
            dest = next((t for t in ctoks[1:] if not t.startswith("-")), None)
            if dest is None:
                block("`cd` with no argument goes to $HOME, outside the project")
            new = check_path(dest, eff_cwd, root, "cd")
            eff_cwd = new or eff_cwd
            prev_download = False
            continue
        if cmd == "popd":
            block("`popd` changes to an untracked directory")

        if cmd in ("source", ".") and len(ctoks) > 1 and ctoks[1].endswith("activate"):
            if is_within(resolve(ctoks[1], eff_cwd), root):
                venv_active = True

        check_system_and_network(cmd, ctoks, root, eff_cwd, venv_active)

        for t in ctoks[1:]:
            if t.startswith("-"):
                if "=" in t:
                    val = t.split("=", 1)[1]
                    if val.startswith(("/", "~", "$HOME", "${HOME}", "..")):
                        check_path(val, eff_cwd, root, f"option {t.split('=', 1)[0]}")
                continue
            check_path(t, eff_cwd, root, f"`{cmd}` argument")

        targets = download_targets(cmd, ctoks, eff_cwd)
        if targets is not None:
            targets += [resolve(t, eff_cwd) for op, t in redirs if ">" in op and t not in DEV_OK]
            for p in targets:
                if not is_within(p, tmp_dir):
                    block(f"downloads must land in {tmp_dir} first, not {p}")
        if cmd == "tee" and prev_download and sep == "|":
            for t in ctoks[1:]:
                if not t.startswith("-") and not is_within(resolve(t, eff_cwd), tmp_dir):
                    block(f"downloads must land in {tmp_dir} first")
        prev_download = cmd in ("curl", "wget")


# --------------------------------------------------------------------------- file tools

def gitignore_change_ok(tool: str, args: dict, path: Path) -> bool:
    def allowed(line: str) -> bool:
        s = line.strip()
        return not s or s.startswith("#") or s in GITIGNORE_ADDABLE

    def only_adds(old: str, new: str) -> bool:
        if old not in new:
            return False
        return all(allowed(l) for l in new.replace(old, "", 1).splitlines())

    if tool == "Edit":
        return only_adds(args.get("old_string", ""), args.get("new_string", ""))
    if tool == "MultiEdit":
        return all(only_adds(e.get("old_string", ""), e.get("new_string", "")) for e in args.get("edits", []))
    if tool == "Write":
        old_lines = path.read_text().splitlines() if path.exists() else []
        j, extras = 0, []
        for line in args.get("content", "").splitlines():
            if j < len(old_lines) and line == old_lines[j]:
                j += 1
            else:
                extras.append(line)
        return j == len(old_lines) and all(allowed(e) for e in extras)
    return False


def glob_prefix(pattern: str) -> str:
    m = re.search(r"[*?\[{]", pattern)
    prefix = pattern[:m.start()] if m else pattern
    return prefix.rsplit("/", 1)[0] if "/" in prefix else prefix or "."


def check_file_tool(tool: str, args: dict, cwd: Path, root: Path) -> None:
    raws = [args.get(k) for k in ("file_path", "notebook_path", "path") if args.get(k)]
    if tool == "Glob" and args.get("pattern"):
        raws.append(glob_prefix(args["pattern"]))
    for raw in raws:
        p = resolve(raw, cwd)
        if any(is_within(secret, p) for secret in SECRET_FILES):
            block(f"{tool} on {p} would expose credentials")
        if not is_within(p, root):
            if tool in READ_TOOLS and any(is_within(p, d) for d in READABLE_DIRS):
                continue
            block(f"{tool} on {p}, outside the project root {root}")
        if tool in WRITE_TOOLS:
            if p.name == ".gitignore" and p.parent == root and gitignore_change_ok(tool, args, p):
                continue
            if p.name in PROTECTED_NAMES:
                block(f"hand-editing {p.name}; regenerate it with the owning tool or ask")
            if re.fullmatch(r"\.env(\..+)?", p.name) and not p.name.endswith((".example", ".sample", ".template")):
                block("writing a .env file")
            if ".github/workflows" in p.as_posix() or ".gitlab-ci" in p.name:
                block("editing CI config; confirm with the user first")
    if tool == "Read":
        name = resolve(args.get("file_path", ""), cwd).name
        if re.fullmatch(r"\.env(\..+)?", name) and not name.endswith((".example", ".sample", ".template")):
            block("reading .env secrets; ask for the specific value")


# --------------------------------------------------------------------------- main

def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        block("unparseable hook payload")
        return

    tool = payload.get("tool_name", "")
    args = payload.get("tool_input", {}) or {}
    cwd = Path(os.path.realpath(payload.get("cwd") or os.getcwd()))
    root = project_root(str(cwd))

    if root == Path("/") or is_within(HOME, root):
        block(f"project root resolved to {root}; launch Claude Code inside a project directory")
    if not is_within(cwd, root):
        cwd = root

    if tool == "Bash":
        check_bash(args.get("command", ""), cwd, root)
    elif tool in FILE_TOOLS:
        check_file_tool(tool, args, cwd, root)
    elif tool == "WebFetch":
        url = args.get("url", "")
        if PRIVATE_NET.search(url) or re.search(r"//(localhost|127\.|0\.0\.0\.0|\[::1\])", url):
            block("WebFetch to a local or private network address")

    sys.exit(0)


if __name__ == "__main__":
    main()
