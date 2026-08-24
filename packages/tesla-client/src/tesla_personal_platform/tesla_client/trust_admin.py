"""Prepare a CA-only Fleet Telemetry server trust profile from the live chain."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import NoReturn

from tesla_personal_platform.tesla_client.telemetry import ca_profile_from_served_chain


def _fail(parser: argparse.ArgumentParser, error: Exception) -> NoReturn:
    parser.exit(1, f"error: {error}\n")


def prepare_trust_profile_main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract and validate CA-only trust from the live Fleet Telemetry TLS chain"
    )
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=443)
    arguments = parser.parse_args()
    if arguments.output.exists():
        _fail(parser, ValueError("output already exists; choose a new path"))
    try:
        openssl = shutil.which("openssl")
        if openssl is None:
            raise ValueError("openssl is required to inspect the public TLS chain")
        result = subprocess.run(  # noqa: S603 -- fixed executable, argv only, and no shell
            [
                openssl,
                "s_client",
                "-connect",
                f"{arguments.hostname}:{arguments.port}",
                "-servername",
                arguments.hostname,
                "-showcerts",
                "-verify_return_error",
            ],
            input=b"",
            check=False,
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise ValueError("the public telemetry TLS chain did not validate")
        profile = ca_profile_from_served_chain(
            arguments.profile_id,
            arguments.hostname,
            arguments.port,
            (result.stdout + result.stderr).decode("ascii"),
        )
        arguments.output.write_text(profile.ca_pem, encoding="ascii", newline="\n")
    except (OSError, UnicodeDecodeError, ValueError, subprocess.SubprocessError) as error:
        _fail(parser, error)
    print(
        json.dumps(
            {
                "certificate_count": profile.ca_pem.count("-----BEGIN CERTIFICATE-----"),
                "hostname": profile.hostname,
                "output": str(arguments.output),
                "port": profile.port,
                "profile_id": profile.profile_id,
                "sha256": profile.ca_hash,
            },
            sort_keys=True,
        )
    )
    return 0
