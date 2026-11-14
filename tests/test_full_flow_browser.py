"""Browser → cookie auth → scheduler → real model HTTP → tools → artifact."""
import os
import subprocess

import pytest
from support.full_flow import FullFlowHub, ROOT
from support.strict_provider import StrictProvider, REPORT


def test_browser_account_model_tool_artifact_and_failure_journey(tmp_path, monkeypatch):
    if not os.environ.get('FLEET_PLAYWRIGHT_MODULE'):
        pytest.skip('FLEET_PLAYWRIGHT_MODULE is required for full-flow browser acceptance')
    monkeypatch.setenv('FLEET_FULL_FLOW_SECRET', 'full-flow-provider-secret')
    with StrictProvider() as provider:
        with FullFlowHub(tmp_path, provider, browser=True) as hub:
            # node 输出 UTF-8；text=True 默认 ANSI 代码页在 CI cp1252 上会让
            # _readerthread UnicodeDecodeError → stdout 为 None。
            result = subprocess.run(['node', str(ROOT / 'tests/fixtures/frontend/full_flow_browser.cjs'), hub.origin],
                                    capture_output=True, text=True, encoding="utf-8", timeout=90)
            assert result.returncode == 0, result.stdout + result.stderr
            # artifact 由 hub 以 UTF-8 写出；不带 encoding 的 read_text 在 nt 上
            # 走 ANSI 代码页（GBK/cp1252），中文内容被误读成 mojibake。
            assert (hub.workspace / 'flow.txt').read_text(encoding="utf-8") == REPORT
            assert provider.errors == []
            assert len(provider.requests) == 6  # Five success steps; one failed call, never replayed.
