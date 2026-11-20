"""Basic privacy guard for tracked files. Findings never print matched secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".sqlite", ".sqlite3", ".db",
           ".pem", ".key", ".p12", ".pfx", ".zip", ".7z"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PATTERNS = {
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "service key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    "AWS access key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal home path": re.compile(r"(?:[A-Z]:[\\/]" + r"Users[\\/]|/" + r"Users/|/" + r"home/)[^\s\"'<>]+", re.I),
}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def private_email(address):
    domain = address.rsplit("@", 1)[-1].lower()
    return not (domain == "users.noreply.github.com" or
                domain in {"example.com", "example.org", "example.net", "localhost"})


def inspect_text(label, text, findings):
    for line_number, line in enumerate(text.splitlines(), 1):
        for name, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(f"{label}:{line_number}: {name}")
        if any(private_email(match.group()) for match in EMAIL.finditer(line)):
            findings.append(f"{label}:{line_number}: personal email")


def main():
    paths = [p.decode("utf-8") for p in git("ls-files", "-z").split(b"\0") if p]
    findings = []
    for name in paths:
        path = ROOT / name
        if (path.suffix.lower() in BLOCKED or
                (path.name.startswith(".env") and path.name != ".env.example") or
                any(part in {".var", "artifacts", "node_modules", ".venv"} for part in Path(name).parts)):
            findings.append(f"{name}: excluded data or credential file")
            continue
        try:
            inspect_text(name, path.read_text(encoding="utf-8"), findings)
        except (UnicodeError, OSError):
            findings.append(f"{name}: cannot inspect as UTF-8 source")
    metadata = git("log", "HEAD", "--format=%ae%n%ce%n%B").decode("utf-8")
    inspect_text("commit history", metadata, findings)
    if findings:
        print("\n".join(findings))
        return 1
    print(f"Privacy check passed: {len(paths)} tracked files and current branch history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
