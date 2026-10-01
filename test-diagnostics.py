#!/usr/bin/env python3
"""Check diagnostic summaries and command acknowledgements without root."""
import io
from pathlib import Path
import runpy
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def main():
    module = runpy.run_path(str(Path(__file__).with_name("debug-binding-api.py")),
                            run_name="diagnostic_test")
    traffic = '''2026-10-01 10:20:00.000001 lo In IP 127.0.0.1.5589 > 127.0.0.1.34526: Flags [P.], seq 1:100, ack 1, win 512, length 99
....HTTP/1.1 101 Switching Protocols
Authorization: Bearer SECRET_VALUE
2026-10-01 10:20:00.000002 enp2s0 Out IP 192.0.2.10.5588 > 192.0.2.20.41132: Flags [R.], seq 100, ack 1, win 0, length 0
2026-10-01 10:20:00.000003 lo In IP 127.0.0.1.34526 > 127.0.0.1.5589: Flags [P.], seq 1:50, ack 1, win 512, length 49
....GET /?token=SECRET_QUERY HTTP/1.1
'''
    application = '''2026-10-01 10:20:00.000 DEBUG [WebModule.cpp] [handle_message:20] getNetworkState token=SECRET_VALUE
2026-10-01 10:20:01.000 WARN [Api.cpp] [request:51] retCode=403 user_id=PRIVATE_ID
'''
    output = io.StringIO()
    with redirect_stdout(output):
        module["summarize_traffic"](SimpleNamespace(read_text=lambda **_: traffic))
        module["summarize"](SimpleNamespace(read_text=lambda **_: application))
    summary = output.getvalue()
    for expected in ("HTTP_STATUS=101", "HTTP_GET", "flags=R.", "getNetworkState", "retCode=403"):
        assert expected in summary
    for secret in ("SECRET_VALUE", "SECRET_QUERY", "PRIVATE_ID", "Authorization"):
        assert secret not in summary
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.side_effect = [b"banner", b"Log level set ", b"to debug\n"]
    with patch("socket.create_connection", return_value=connection):
        module["set_log_level"]("debug")
    connection.recv.side_effect = [b"banner", b"Unknown command", b""]
    with patch("socket.create_connection", return_value=connection):
        try:
            module["set_log_level"]("debug")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Missing acknowledgement was accepted")
    print("PASS: diagnostic metadata omits credentials and checks command acknowledgement")


if __name__ == "__main__":
    main()
