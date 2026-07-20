# ClassCtl - build standalone exes. Run once on a build machine.
# Requires: pip install pyinstaller
$ErrorActionPreference = "Stop"
pyinstaller --onefile --noconsole --name classctl-setup setup_wizard.py
pyinstaller --onefile --noconsole --name classctl        controller.py
pyinstaller --onefile --noconsole --name classctl-agent  agent.py
Write-Host "[OK] Built dist\classctl-setup.exe, dist\classctl.exe, dist\classctl-agent.exe" -ForegroundColor Green
Write-Host "  Publish them as a GitHub Release, then run: install_agent.ps1 -AgentExe classctl-agent.exe"
