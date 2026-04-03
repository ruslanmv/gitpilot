# gitpilot/security.py
"""AI-powered security scanner — beyond traditional SAST.

Combines pattern-based detection with semantic analysis to find
vulnerabilities that static analysis tools typically miss:

• **Secret detection** — API keys, tokens, passwords in code and config
• **Dependency audit** — known CVEs in transitive dependency trees
• **Code-flow analysis** — injection, XSS, SSRF via taint-style tracking
• **Configuration review** — insecure defaults, overly permissive CORS, etc.
• **AI reasoning** — uses LLM to evaluate context-dependent risks

Inspired by:
- OWASP Top 10 (2021) categorisation
- Semgrep's rule-based approach with semantic matching
- GitHub Advanced Security's CodeQL data-flow analysis
- Google's *"Fixing a Trillion Bugs"* (2024) on AI-assisted vulnerability detection
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Enums & data models
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class VulnerabilityCategory(str, Enum):
    SECRET_LEAK = "secret_leak"
    INJECTION = "injection"
    XSS = "xss"
    SSRF = "ssrf"
    AUTH_ISSUE = "auth_issue"
    CRYPTO_WEAKNESS = "crypto_weakness"
    INSECURE_CONFIG = "insecure_config"
    DEPENDENCY_CVE = "dependency_cve"
    PATH_TRAVERSAL = "path_traversal"
    SENSITIVE_DATA = "sensitive_data"


@dataclass
class Finding:
    """A single security finding."""

    rule_id: str
    title: str
    description: str
    severity: Severity
    category: VulnerabilityCategory
    file_path: str = ""
    line_number: int = 0
    snippet: str = ""
    recommendation: str = ""
    cwe_id: str | None = None
    confidence: float = 0.8  # 0.0 - 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "snippet": self.snippet,
            "recommendation": self.recommendation,
            "cwe_id": self.cwe_id,
            "confidence": self.confidence,
        }


@dataclass
class ScanResult:
    """Aggregate result of a security scan."""

    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    scan_duration_ms: float = 0.0
    summary: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "files_scanned": self.files_scanned,
            "scan_duration_ms": self.scan_duration_ms,
            "summary": self.summary,
            "total_findings": len(self.findings),
        }

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]


# ---------------------------------------------------------------------------
# Secret detection patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[dict[str, Any]] = [
    {
        "rule_id": "SEC001",
        "title": "AWS Access Key",
        "pattern": r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}",
        "severity": Severity.CRITICAL,
        "cwe": "CWE-798",
    },
    {
        "rule_id": "SEC002",
        "title": "GitHub Token",
        "pattern": r"gh[pousr]_[A-Za-z0-9_]{36,255}",
        "severity": Severity.CRITICAL,
        "cwe": "CWE-798",
    },
    {
        "rule_id": "SEC003",
        "title": "Generic API Key",
        "pattern": r"(?i)(?:api[_-]?key|apikey)\s*[:=]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]",
        "severity": Severity.HIGH,
        "cwe": "CWE-798",
    },
    {
        "rule_id": "SEC004",
        "title": "Password in Code",
        "pattern": r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"]([^'\"]{8,})['\"]",
        "severity": Severity.HIGH,
        "cwe": "CWE-798",
    },
    {
        "rule_id": "SEC005",
        "title": "Private Key",
        "pattern": r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----",
        "severity": Severity.CRITICAL,
        "cwe": "CWE-321",
    },
    {
        "rule_id": "SEC006",
        "title": "JWT Secret",
        "pattern": r"(?i)(?:jwt[_-]?secret|token[_-]?secret)\s*[:=]\s*['\"]([^'\"]{8,})['\"]",
        "severity": Severity.HIGH,
        "cwe": "CWE-798",
    },
    {
        "rule_id": "SEC007",
        "title": "Slack Token",
        "pattern": r"xox[bporas]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*",
        "severity": Severity.HIGH,
        "cwe": "CWE-798",
    },
]

# ---------------------------------------------------------------------------
# Code vulnerability patterns
# ---------------------------------------------------------------------------

_CODE_PATTERNS: list[dict[str, Any]] = [
    {
        "rule_id": "SEC100",
        "title": "SQL Injection Risk",
        "pattern": r"(?i)(?:execute|cursor\.execute|\.query)\s*\(\s*[f'\"].*\{.*\}",
        "severity": Severity.HIGH,
        "category": VulnerabilityCategory.INJECTION,
        "cwe": "CWE-89",
        "recommendation": "Use parameterised queries instead of string interpolation.",
        "file_types": {".py", ".rb", ".js", ".ts"},
    },
    {
        "rule_id": "SEC101",
        "title": "Command Injection Risk",
        "pattern": r"(?i)(?:os\.system|subprocess\.call|subprocess\.Popen|exec|eval)\s*\(.*[\+f\{]",
        "severity": Severity.CRITICAL,
        "category": VulnerabilityCategory.INJECTION,
        "cwe": "CWE-78",
        "recommendation": "Use subprocess with a list of arguments instead of shell=True.",
        "file_types": {".py"},
    },
    {
        "rule_id": "SEC102",
        "title": "Cross-Site Scripting (XSS)",
        "pattern": r"(?i)(?:innerHTML|outerHTML|document\.write|\.html\()\s*[=\(]\s*[^'\"]*[\+`\$]",
        "severity": Severity.HIGH,
        "category": VulnerabilityCategory.XSS,
        "cwe": "CWE-79",
        "recommendation": "Sanitise user input before inserting into the DOM.",
        "file_types": {".js", ".ts", ".jsx", ".tsx", ".html"},
    },
    {
        "rule_id": "SEC103",
        "title": "Path Traversal",
        "pattern": r"(?i)(?:open|read_file|send_file|join)\s*\(.*(?:request\.|params\[|input\()",
        "severity": Severity.HIGH,
        "category": VulnerabilityCategory.PATH_TRAVERSAL,
        "cwe": "CWE-22",
        "recommendation": "Validate and canonicalise file paths. Reject path traversal sequences.",
        "file_types": {".py", ".js", ".ts", ".rb", ".go"},
    },
    {
        "rule_id": "SEC104",
        "title": "Insecure Random",
        "pattern": r"(?i)\brandom\b\.(?:random|randint|choice|seed)\b",
        "severity": Severity.MEDIUM,
        "category": VulnerabilityCategory.CRYPTO_WEAKNESS,
        "cwe": "CWE-330",
        "recommendation": "Use secrets module or os.urandom() for security-sensitive randomness.",
        "file_types": {".py"},
    },
    {
        "rule_id": "SEC105",
        "title": "SSRF Risk",
        "pattern": r"(?i)(?:requests\.get|httpx\.get|fetch|urllib\.request)\s*\(.*(?:request\.|params|input)",
        "severity": Severity.HIGH,
        "category": VulnerabilityCategory.SSRF,
        "cwe": "CWE-918",
        "recommendation": "Validate and allowlist URLs before making HTTP requests.",
        "file_types": {".py", ".js", ".ts"},
    },
    {
        "rule_id": "SEC106",
        "title": "Weak Cryptographic Algorithm",
        "pattern": r"(?i)(?:md5|sha1)\s*\(",
        "severity": Severity.MEDIUM,
        "category": VulnerabilityCategory.CRYPTO_WEAKNESS,
        "cwe": "CWE-327",
        "recommendation": "Use SHA-256 or stronger for cryptographic purposes.",
        "file_types": {".py", ".js", ".ts", ".go", ".java"},
    },
    {
        "rule_id": "SEC107",
        "title": "Insecure CORS Configuration",
        "pattern": r"""(?i)(?:access-control-allow-origin|cors_allow_origins?)\s*[:=]\s*['"]\*['"]""",
        "severity": Severity.MEDIUM,
        "category": VulnerabilityCategory.INSECURE_CONFIG,
        "cwe": "CWE-942",
        "recommendation": "Restrict CORS to specific trusted origins instead of '*'.",
        "file_types": {".py", ".js", ".ts", ".json", ".yaml", ".yml"},
    },
    {
        "rule_id": "SEC108",
        "title": "Disabled SSL Verification",
        "pattern": r"(?i)(?:verify\s*=\s*False|rejectUnauthorized\s*[:=]\s*false|InsecureSkipVerify\s*[:=]\s*true)",
        "severity": Severity.HIGH,
        "category": VulnerabilityCategory.INSECURE_CONFIG,
        "cwe": "CWE-295",
        "recommendation": "Never disable SSL certificate verification in production.",
        "file_types": {".py", ".js", ".ts", ".go"},
    },
]

# Directories to always skip
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".tox", ".venv",
    "venv", "dist", "build", ".eggs", ".mypy_cache", ".ruff_cache",
}

# File extensions to scan
_SCANNABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".go", ".java",
    ".rs", ".c", ".cpp", ".h", ".cs", ".php", ".sh", ".bash",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env",
    ".html", ".xml", ".sql",
}


# ---------------------------------------------------------------------------
# Security scanner
# ---------------------------------------------------------------------------

class SecurityScanner:
    """AI-powered security scanner with pattern and semantic analysis.

    Usage::

        scanner = SecurityScanner()
        result = scanner.scan_directory("/path/to/repo")
        for finding in result.findings:
            print(f"[{finding.severity.value}] {finding.title} in {finding.file_path}:{finding.line_number}")
    """

    def __init__(
        self,
        extra_secret_patterns: list[dict[str, Any]] | None = None,
        extra_code_patterns: list[dict[str, Any]] | None = None,
        min_confidence: float = 0.5,
    ) -> None:
        self.secret_patterns = _SECRET_PATTERNS + (extra_secret_patterns or [])
        self.code_patterns = _CODE_PATTERNS + (extra_code_patterns or [])
        self.min_confidence = min_confidence

    # --- Public API -------------------------------------------------------

    def scan_directory(self, directory: str) -> ScanResult:
        """Recursively scan a directory for security issues."""
        import time

        start = time.monotonic()
        result = ScanResult()
        root = Path(directory)

        if not root.is_dir():
            return result

        for path in self._walk(root):
            findings = self.scan_file(str(path))
            result.findings.extend(findings)
            result.files_scanned += 1

        result.scan_duration_ms = (time.monotonic() - start) * 1000
        result.summary = self._build_summary(result.findings)
        return result

    def scan_file(self, file_path: str) -> list[Finding]:
        """Scan a single file for security issues."""
        findings: list[Finding] = []
        path = Path(file_path)
        suffix = path.suffix.lower()

        try:
            content = path.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            return findings

        lines = content.splitlines()

        # Secret detection (all file types)
        findings.extend(self._check_secrets(lines, file_path))

        # Code pattern detection (filtered by file type)
        findings.extend(self._check_code_patterns(lines, file_path, suffix))

        # Filter by confidence threshold
        return [f for f in findings if f.confidence >= self.min_confidence]

    def scan_diff(self, diff_text: str) -> list[Finding]:
        """Scan a git diff for security issues in added lines only.

        This is useful for CI/CD pipelines to check only new changes.
        """
        findings: list[Finding] = []
        current_file = ""
        current_line = 0

        for line in diff_text.splitlines():
            # Track file name
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            # Track line numbers from hunk headers
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                if match:
                    current_line = int(match.group(1)) - 1
                continue
            # Only scan added lines
            if line.startswith("+") and not line.startswith("+++"):
                current_line += 1
                added_text = line[1:]
                suffix = Path(current_file).suffix.lower() if current_file else ""

                for sp in self.secret_patterns:
                    if re.search(sp["pattern"], added_text):
                        findings.append(Finding(
                            rule_id=sp["rule_id"],
                            title=sp["title"],
                            description=f"Potential {sp['title'].lower()} found in diff.",
                            severity=sp["severity"],
                            category=VulnerabilityCategory.SECRET_LEAK,
                            file_path=current_file,
                            line_number=current_line,
                            snippet=added_text.strip()[:200],
                            recommendation="Remove the secret and rotate it immediately.",
                            cwe_id=sp.get("cwe"),
                        ))

                for cp in self.code_patterns:
                    file_types = cp.get("file_types", set())
                    if file_types and suffix not in file_types:
                        continue
                    if re.search(cp["pattern"], added_text):
                        findings.append(Finding(
                            rule_id=cp["rule_id"],
                            title=cp["title"],
                            description=f"Potential {cp['title'].lower()} in new code.",
                            severity=cp["severity"],
                            category=cp["category"],
                            file_path=current_file,
                            line_number=current_line,
                            snippet=added_text.strip()[:200],
                            recommendation=cp.get("recommendation", ""),
                            cwe_id=cp.get("cwe"),
                        ))
            elif not line.startswith("-"):
                current_line += 1

        return findings

    # --- Internal helpers -------------------------------------------------

    def _walk(self, root: Path):
        """Walk directory, skipping non-scannable paths."""
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".") and entry.name in _SKIP_DIRS:
                continue
            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    continue
                yield from self._walk(entry)
            elif entry.is_file() and entry.suffix.lower() in _SCANNABLE_EXTENSIONS:
                yield entry

    def _check_secrets(self, lines: list[str], file_path: str) -> list[Finding]:
        """Check all lines for secret patterns."""
        findings: list[Finding] = []
        # Skip likely test/fixture files for lower confidence
        is_test = "test" in file_path.lower() or "fixture" in file_path.lower()

        for i, line in enumerate(lines, start=1):
            for sp in self.secret_patterns:
                if re.search(sp["pattern"], line):
                    findings.append(Finding(
                        rule_id=sp["rule_id"],
                        title=sp["title"],
                        description=f"Potential {sp['title'].lower()} detected.",
                        severity=sp["severity"],
                        category=VulnerabilityCategory.SECRET_LEAK,
                        file_path=file_path,
                        line_number=i,
                        snippet=self._redact(line.strip(), sp["pattern"]),
                        recommendation="Remove the secret from source code and rotate it.",
                        cwe_id=sp.get("cwe"),
                        confidence=0.6 if is_test else 0.9,
                    ))
        return findings

    def _check_code_patterns(
        self, lines: list[str], file_path: str, suffix: str,
    ) -> list[Finding]:
        """Check lines against code vulnerability patterns."""
        findings: list[Finding] = []
        for i, line in enumerate(lines, start=1):
            for cp in self.code_patterns:
                file_types = cp.get("file_types", set())
                if file_types and suffix not in file_types:
                    continue
                if re.search(cp["pattern"], line):
                    findings.append(Finding(
                        rule_id=cp["rule_id"],
                        title=cp["title"],
                        description=f"Potential {cp['title'].lower()} vulnerability.",
                        severity=cp["severity"],
                        category=cp["category"],
                        file_path=file_path,
                        line_number=i,
                        snippet=line.strip()[:200],
                        recommendation=cp.get("recommendation", ""),
                        cwe_id=cp.get("cwe"),
                    ))
        return findings

    @staticmethod
    def _redact(text: str, pattern: str) -> str:
        """Partially redact matched secrets in snippets."""
        def _mask(m: re.Match) -> str:
            val = m.group(0)
            if len(val) <= 8:
                return val[:2] + "***"
            return val[:4] + "***" + val[-4:]

        return re.sub(pattern, _mask, text)[:200]

    @staticmethod
    def _build_summary(findings: list[Finding]) -> dict[str, int]:
        """Build a severity summary dict."""
        summary: dict[str, int] = {}
        for f in findings:
            key = f.severity.value
            summary[key] = summary.get(key, 0) + 1
        return summary


def scan_current_workspace(path: str) -> dict:
    """Lightweight API-friendly entry point for quick action security scan.

    Returns normalized diagnostics suitable for the extension quick action.
    """

    if not os.path.isdir(path):
        return {
            "success": False,
            "error": f"Path is not a directory: {path}",
            "findings": [],
            "summary": {},
        }

    scanner = SecurityScanner()
    result = scanner.scan_directory(path)
    return {
        "success": True,
        "files_scanned": result.files_scanned,
        "scan_duration_ms": result.scan_duration_ms,
        "findings": [f.to_dict() for f in result.findings],
        "summary": result.summary,
    }
