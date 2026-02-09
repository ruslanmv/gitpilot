"""Tests for gitpilot.security module."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gitpilot.security import (
    Finding,
    ScanResult,
    SecurityScanner,
    Severity,
    VulnerabilityCategory,
)


# ---------------------------------------------------------------------------
# Severity / VulnerabilityCategory enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_severity_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
        assert Severity.INFO.value == "info"

    def test_vulnerability_category_values(self):
        assert VulnerabilityCategory.SECRET_LEAK.value == "secret_leak"
        assert VulnerabilityCategory.INJECTION.value == "injection"
        assert VulnerabilityCategory.XSS.value == "xss"


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

class TestFinding:
    def test_to_dict(self):
        f = Finding(
            rule_id="SEC001", title="AWS Key",
            description="Found key", severity=Severity.CRITICAL,
            category=VulnerabilityCategory.SECRET_LEAK,
            file_path="config.py", line_number=10,
            snippet="AKIA***", recommendation="Rotate",
            cwe_id="CWE-798", confidence=0.9,
        )
        d = f.to_dict()
        assert d["rule_id"] == "SEC001"
        assert d["severity"] == "critical"
        assert d["category"] == "secret_leak"
        assert d["cwe_id"] == "CWE-798"
        assert d["confidence"] == 0.9


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------

class TestScanResult:
    def test_empty_scan_result(self):
        sr = ScanResult()
        assert sr.findings == []
        assert sr.files_scanned == 0
        d = sr.to_dict()
        assert d["total_findings"] == 0

    def test_by_severity(self):
        f1 = Finding(
            rule_id="A", title="A", description="",
            severity=Severity.HIGH, category=VulnerabilityCategory.INJECTION,
        )
        f2 = Finding(
            rule_id="B", title="B", description="",
            severity=Severity.LOW, category=VulnerabilityCategory.INSECURE_CONFIG,
        )
        f3 = Finding(
            rule_id="C", title="C", description="",
            severity=Severity.HIGH, category=VulnerabilityCategory.XSS,
        )
        sr = ScanResult(findings=[f1, f2, f3])
        highs = sr.by_severity(Severity.HIGH)
        assert len(highs) == 2
        lows = sr.by_severity(Severity.LOW)
        assert len(lows) == 1
        crits = sr.by_severity(Severity.CRITICAL)
        assert len(crits) == 0

    def test_to_dict(self):
        f = Finding(
            rule_id="X", title="X", description="",
            severity=Severity.MEDIUM, category=VulnerabilityCategory.CRYPTO_WEAKNESS,
        )
        sr = ScanResult(findings=[f], files_scanned=5, scan_duration_ms=123.4)
        d = sr.to_dict()
        assert d["files_scanned"] == 5
        assert d["total_findings"] == 1


# ---------------------------------------------------------------------------
# Secret pattern detection
# ---------------------------------------------------------------------------

class TestSecretPatterns:
    def _scan_line(self, line: str, ext: str = ".py") -> list:
        scanner = SecurityScanner()
        return scanner._check_secrets([line], f"test{ext}")

    def test_aws_access_key(self):
        findings = self._scan_line("key = 'AKIAIOSFODNN7EXAMPLE1'")
        assert any(f.rule_id == "SEC001" for f in findings)

    def test_github_token(self):
        findings = self._scan_line("token = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'")
        assert any(f.rule_id == "SEC002" for f in findings)

    def test_generic_api_key(self):
        findings = self._scan_line("api_key = 'abcdefghijklmnopqrstuvwxyz123456'")
        assert any(f.rule_id == "SEC003" for f in findings)

    def test_password_in_code(self):
        findings = self._scan_line("password = 'super_secret_password_123'")
        assert any(f.rule_id == "SEC004" for f in findings)

    def test_private_key(self):
        findings = self._scan_line("-----BEGIN RSA PRIVATE KEY-----")
        assert any(f.rule_id == "SEC005" for f in findings)

    def test_jwt_secret(self):
        findings = self._scan_line("jwt_secret = 'my_very_long_jwt_secret_value'")
        assert any(f.rule_id == "SEC006" for f in findings)

    def test_slack_token(self):
        findings = self._scan_line("token = 'xoxb-1234567890-1234567890-abcdefABCDEF'")
        assert any(f.rule_id == "SEC007" for f in findings)

    def test_no_false_positive_short_string(self):
        findings = self._scan_line("password = 'short'")
        # Password < 8 chars should not match SEC004
        assert not any(f.rule_id == "SEC004" for f in findings)

    def test_lower_confidence_in_test_files(self):
        scanner = SecurityScanner()
        findings = scanner._check_secrets(
            ["key = 'AKIAIOSFODNN7EXAMPLE1'"],
            "test_config.py",
        )
        for f in findings:
            if f.rule_id == "SEC001":
                assert f.confidence == 0.6  # lower for test files


# ---------------------------------------------------------------------------
# Code pattern detection
# ---------------------------------------------------------------------------

class TestCodePatterns:
    def _scan_line(self, line: str, ext: str = ".py") -> list:
        scanner = SecurityScanner()
        return scanner._check_code_patterns([line], f"file{ext}", ext)

    def test_sql_injection(self):
        findings = self._scan_line("cursor.execute(f\"SELECT * FROM {table}\")")
        assert any(f.rule_id == "SEC100" for f in findings)

    def test_command_injection(self):
        findings = self._scan_line("os.system(f'rm -rf {path}')")
        assert any(f.rule_id == "SEC101" for f in findings)

    def test_xss(self):
        findings = self._scan_line("document.write(userInput + '<script>')", ext=".js")
        assert any(f.rule_id == "SEC102" for f in findings)

    def test_path_traversal(self):
        findings = self._scan_line("open(request.args['filename'])")
        assert any(f.rule_id == "SEC103" for f in findings)

    def test_insecure_random(self):
        findings = self._scan_line("token = random.random()")
        assert any(f.rule_id == "SEC104" for f in findings)

    def test_ssrf(self):
        findings = self._scan_line("requests.get(request.args['url'])")
        assert any(f.rule_id == "SEC105" for f in findings)

    def test_weak_crypto(self):
        findings = self._scan_line("digest = md5(data)")
        assert any(f.rule_id == "SEC106" for f in findings)

    def test_insecure_cors(self):
        findings = self._scan_line("access-control-allow-origin: '*'", ext=".yaml")
        assert any(f.rule_id == "SEC107" for f in findings)

    def test_disabled_ssl(self):
        findings = self._scan_line("requests.get(url, verify=False)")
        assert any(f.rule_id == "SEC108" for f in findings)

    def test_file_type_filter(self):
        # SEC101 (command injection) only applies to .py
        findings = self._scan_line("os.system(f'rm -rf {path}')", ext=".html")
        assert not any(f.rule_id == "SEC101" for f in findings)


# ---------------------------------------------------------------------------
# scan_file
# ---------------------------------------------------------------------------

class TestScanFile:
    def test_scan_python_file(self, tmp_path):
        f = tmp_path / "app.py"
        f.write_text("password = 'verylongsecretpassword'\nos.system(f'ls {d}')\n")
        scanner = SecurityScanner()
        findings = scanner.scan_file(str(f))
        rule_ids = {f.rule_id for f in findings}
        assert "SEC004" in rule_ids  # password
        assert "SEC101" in rule_ids  # command injection

    def test_scan_missing_file(self):
        scanner = SecurityScanner()
        findings = scanner.scan_file("/nonexistent/file.py")
        assert findings == []

    def test_min_confidence_filter(self, tmp_path):
        f = tmp_path / "test_secret.py"
        f.write_text("key = 'AKIAIOSFODNN7EXAMPLE1'\n")
        # In a test file, confidence is 0.6
        scanner = SecurityScanner(min_confidence=0.8)
        findings = scanner.scan_file(str(f))
        # SEC001 in test file has confidence 0.6 < 0.8, so filtered out
        assert not any(fnd.rule_id == "SEC001" for fnd in findings)


# ---------------------------------------------------------------------------
# scan_diff
# ---------------------------------------------------------------------------

class TestScanDiff:
    def test_detects_secret_in_added_lines(self):
        diff = """\
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,3 +1,4 @@
 import os
+password = 'averysecretsecretpassword'

 DEBUG = True
"""
        scanner = SecurityScanner()
        findings = scanner.scan_diff(diff)
        assert any(f.rule_id == "SEC004" for f in findings)
        secret_finding = next(f for f in findings if f.rule_id == "SEC004")
        assert secret_finding.file_path == "config.py"

    def test_ignores_removed_lines(self):
        diff = """\
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1,3 +1,2 @@
-password = 'oldsecretpasswordhere123'
 import os
 DEBUG = True
"""
        scanner = SecurityScanner()
        findings = scanner.scan_diff(diff)
        assert not any(f.rule_id == "SEC004" for f in findings)

    def test_code_pattern_in_diff(self):
        diff = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 import os
+os.system(f'deploy {target}')
 print("done")
"""
        scanner = SecurityScanner()
        findings = scanner.scan_diff(diff)
        assert any(f.rule_id == "SEC101" for f in findings)

    def test_line_number_tracking(self):
        diff = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@
 line_a
 line_b
+password = 'anotherverylongpassword'
 line_c
"""
        scanner = SecurityScanner()
        findings = scanner.scan_diff(diff)
        pw_finding = next((f for f in findings if f.rule_id == "SEC004"), None)
        assert pw_finding is not None
        assert pw_finding.line_number == 12


# ---------------------------------------------------------------------------
# scan_directory
# ---------------------------------------------------------------------------

class TestScanDirectory:
    def test_scans_python_files(self, tmp_path):
        (tmp_path / "app.py").write_text("password = 'secretpassword12345'\n")
        (tmp_path / "utils.py").write_text("x = 1\n")
        scanner = SecurityScanner()
        result = scanner.scan_directory(str(tmp_path))
        assert result.files_scanned >= 1
        assert any(f.rule_id == "SEC004" for f in result.findings)

    def test_skips_excluded_dirs(self, tmp_path):
        node_dir = tmp_path / "node_modules"
        node_dir.mkdir()
        (node_dir / "pkg.js").write_text("password = 'verylongpassword123'\n")
        scanner = SecurityScanner()
        result = scanner.scan_directory(str(tmp_path))
        assert not any("node_modules" in f.file_path for f in result.findings)

    def test_nonexistent_directory(self):
        scanner = SecurityScanner()
        result = scanner.scan_directory("/no/such/dir")
        assert result.files_scanned == 0
        assert result.findings == []

    def test_summary_built(self, tmp_path):
        (tmp_path / "leak.py").write_text(
            "password = 'supersecretpassword'\nkey = 'AKIAIOSFODNN7EXAMPLE1'\n"
        )
        scanner = SecurityScanner()
        result = scanner.scan_directory(str(tmp_path))
        assert isinstance(result.summary, dict)
        assert result.scan_duration_ms > 0

    def test_scans_yaml_files(self, tmp_path):
        (tmp_path / "config.yaml").write_text("access-control-allow-origin: '*'\n")
        scanner = SecurityScanner()
        result = scanner.scan_directory(str(tmp_path))
        assert any(f.rule_id == "SEC107" for f in result.findings)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestRedaction:
    def test_redacts_long_secret(self):
        text = "key = AKIAIOSFODNN7EXAMPLE1"
        redacted = SecurityScanner._redact(text, r"AKIA[0-9A-Z]{16}")
        assert "***" in redacted
        # Original full key should not appear
        assert "AKIAIOSFODNN7EXAMPLE1" not in redacted

    def test_redacts_short_secret(self):
        text = "tok=ABCD"
        redacted = SecurityScanner._redact(text, r"ABCD")
        assert "***" in redacted

    def test_no_match_no_redaction(self):
        text = "safe value"
        redacted = SecurityScanner._redact(text, r"AKIA[0-9A-Z]{16}")
        assert redacted == "safe value"


# ---------------------------------------------------------------------------
# Extra patterns
# ---------------------------------------------------------------------------

class TestExtraPatterns:
    def test_custom_secret_pattern(self, tmp_path):
        scanner = SecurityScanner(extra_secret_patterns=[{
            "rule_id": "CUSTOM001",
            "title": "Internal Token",
            "pattern": r"INTERNAL_[A-Z]{10,}",
            "severity": Severity.HIGH,
            "cwe": "CWE-798",
        }])
        f = tmp_path / "cfg.py"
        f.write_text("token = 'INTERNAL_ABCDEFGHIJK'\n")
        findings = scanner.scan_file(str(f))
        assert any(fnd.rule_id == "CUSTOM001" for fnd in findings)

    def test_custom_code_pattern(self, tmp_path):
        scanner = SecurityScanner(extra_code_patterns=[{
            "rule_id": "CUSTOM100",
            "title": "Eval Usage",
            "pattern": r"\beval\s*\(",
            "severity": Severity.HIGH,
            "category": VulnerabilityCategory.INJECTION,
            "cwe": "CWE-95",
            "file_types": {".py"},
        }])
        f = tmp_path / "danger.py"
        f.write_text("result = eval(user_input)\n")
        findings = scanner.scan_file(str(f))
        assert any(fnd.rule_id == "CUSTOM100" for fnd in findings)
