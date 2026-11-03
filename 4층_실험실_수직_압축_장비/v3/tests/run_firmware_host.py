"""Windows MSVC + build/toolchain에 설치한 실제 AccelStepper로 펌웨어 로직을 검사한다."""
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
visual_studio = subprocess.check_output([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"], text=True).strip()
vcvars = Path(visual_studio) / "VC/Auxiliary/Build/vcvars64.bat"
output = root / "build/firmware_host"
output.mkdir(parents=True, exist_ok=True)
environment_batch = output / "msvc_env.cmd"
environment_batch.write_text(f'@echo off\ncall "{vcvars}" >nul\nset\n', encoding="ascii")
environment_text = subprocess.check_output(["cmd.exe", "/d", "/c", str(environment_batch)], text=True)
environment = dict(os.environ)
environment.update((key.upper(), value) for line in environment_text.splitlines() if "=" in line and not line.startswith("=") for key, value in [line.split("=", 1)])
compiler = sorted((Path(visual_studio) / "VC/Tools/MSVC").glob("*/bin/Hostx64/x64/cl.exe"))[-1]
library = root / "build/toolchain/user/libraries/AccelStepper/src"
if not (library / "AccelStepper.cpp").exists():
    raise SystemExit("build/toolchain/user에 Arduino CLI로 AccelStepper를 먼저 설치하세요.")
subprocess.run([
    str(compiler), "/nologo", "/std:c++14", "/EHsc", "/utf-8", "/DARDUINO=10819",
    "/I" + str(root / "tests/firmware"), "/I" + str(library),
    str(root / "tests/firmware/test_diagnostics.cpp"), str(library / "AccelStepper.cpp"),
    "/Fe:" + str(output / "test_diagnostics.exe"),
], cwd=output, env=environment, check=True)
subprocess.run([str(output / "test_diagnostics.exe"), str(output / "trace.txt")], check=True)
