@echo off
setlocal EnableDelayedExpansion
title Windows Agent Setup (User: slave)

:: --- 1. Admin Rights Check ---
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [INFO] Admin rights confirmed.
) else (
    echo [WARN] Admin rights required. Restarting...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ==========================================
echo   Environment Setup: Windows Agent
echo   User: slave
echo   Pass: 66668888
echo ==========================================
echo.

:: --- 2. OpenSSH Server Installation ---
echo [STEP 1/8] Installing OpenSSH Server...

set "OPENSSH_ZIP=%~dp0resources\OpenSSH-Win64.zip"
set "OPENSSH_DEST=C:\Program Files\OpenSSH"

if exist "!OPENSSH_DEST!\sshd.exe" (
    echo [INFO] OpenSSH already installed at !OPENSSH_DEST!
) else (
    if exist "!OPENSSH_ZIP!" (
        echo [INFO] Extracting OpenSSH...
        powershell -Command "Expand-Archive -Path '!OPENSSH_ZIP!' -DestinationPath 'C:\Program Files\' -Force"
        
        echo [INFO] Running OpenSSH install script...
        powershell -ExecutionPolicy Bypass -File "!OPENSSH_DEST!\install-sshd.ps1"
        
        echo [INFO] OpenSSH installed successfully.
    ) else (
        echo [ERROR] OpenSSH-Win64.zip NOT found in resources folder!
        echo         Download from: https://github.com/PowerShell/Win32-OpenSSH/releases
        pause
        exit /b 1
    )
)

:: --- 3. SSH Service Configuration ---
echo.
echo [STEP 2/8] Configuring sshd service...
sc config sshd start= auto >nul
net start sshd >nul 2>&1
echo [INFO] sshd service configured and started.

:: --- 4. Firewall Configuration ---
echo.
echo [STEP 3/8] Opening port 22 (Firewall)...
netsh advfirewall firewall show rule name="OpenSSH Server (sshd)" >nul 2>&1
if %errorLevel% neq 0 (
    netsh advfirewall firewall add rule name="OpenSSH Server (sshd)" dir=in action=allow protocol=TCP localport=22
    echo [INFO] Firewall rule for port 22 created.
) else (
    echo [INFO] Firewall rule for port 22 already exists.
)

:: --- 5. Default Shell Setup (cmd.exe) ---
echo.
echo [STEP 4/8] Setting cmd.exe as Default Shell for SSH...
reg add "HKLM\SOFTWARE\OpenSSH" /v DefaultShell /t REG_SZ /d "C:\Windows\System32\cmd.exe" /f >nul
if %errorLevel% equ 0 (
    echo [INFO] DefaultShell successfully set to cmd.exe.
) else (
    echo [ERROR] Failed to update registry for DefaultShell.
)

:: --- 6. Creating 'slave' User ---
echo.
echo [STEP 5/8] Creating user 'slave'...

:: Check if user exists
net user slave >nul 2>&1
if %errorLevel% equ 0 (
    echo [INFO] User 'slave' exists. Resetting password...
    net user slave 66668888
) else (
    echo [INFO] Creating new user...
    net user slave 66668888 /add /comment:"WiFi Test Automation Account" /passwordchg:no
)

:: Add to Administrators
net localgroup Administrators slave /add >nul 2>&1
echo [INFO] User 'slave' added to Administrators group.

:: Disable password expiration (via WMIC)
wmic useraccount where "Name='slave'" set PasswordExpires=FALSE >nul 2>&1
echo [INFO] Password expiration disabled for 'slave'.

:: --- 7. Python Check & Installation ---
echo.
echo [STEP 6/8] Checking Python installation...

python --version >nul 2>&1
if %errorLevel% equ 0 (
    for /f "tokens=*" %%i in ('python --version') do echo [INFO] Found %%i
) else (
    echo [WARN] Python not found. Attempting automatic installation...
    
    :: Path to the installer in the 'resources' folder relative to this script
    set "INSTALLER=%~dp0resources\python-3.12.6-amd64.exe"
    
    if exist "!INSTALLER!" (
        echo [INFO] Installer found at: !INSTALLER!
        echo [INFO] Installing Python 3.12.6... Please wait...
        
        :: Run installer quietly
        start /wait "" "!INSTALLER!" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
        
        if !errorLevel! equ 0 (
             echo [INFO] Installation completed successfully.
             
             :: Attempt to Verify (might fail until new session)
             python --version >nul 2>&1
             if !errorLevel! equ 0 (
                 echo [INFO] Python is now active in this session.
             ) else (
                 echo [INFO] Python installed. A restart/re-login may be required to update PATH.
             )
        ) else (
             echo [ERROR] Python installation failed with error code !errorLevel!.
        )
    ) else (
        echo [ERROR] Installer NOT found!
        echo         Expected path: !INSTALLER!
        echo         Please place 'python-3.12.6-amd64.exe' in the 'resources' folder next to this script.
    )
)

:: --- 8. iperf3 Setup ---
echo.
echo [STEP 7/8] Installing iperf3...

set "IPERF_EXE=%~dp0resources\iperf3.exe"
set "CYGWIN_DLL=%~dp0resources\cygwin1.dll"
set "IPERF_DEST=C:\Tools\iperf3"

:: Check both files exist
set "FILES_OK=1"
if not exist "!IPERF_EXE!" (
    echo [ERROR] iperf3.exe NOT found: !IPERF_EXE!
    set "FILES_OK=0"
)
if not exist "!CYGWIN_DLL!" (
    echo [ERROR] cygwin1.dll NOT found: !CYGWIN_DLL!
    set "FILES_OK=0"
)

if !FILES_OK! equ 1 (
    echo [INFO] Found iperf3.exe and cygwin1.dll in resources folder.
    
    :: Create directory if not exists
    if not exist "!IPERF_DEST!" (
        mkdir "!IPERF_DEST!"
        echo [INFO] Created directory: !IPERF_DEST!
    )
    
    :: Copy both files
    copy /Y "!IPERF_EXE!" "!IPERF_DEST!\iperf3.exe" >nul
    copy /Y "!CYGWIN_DLL!" "!IPERF_DEST!\cygwin1.dll" >nul
    echo [INFO] Copied iperf3.exe and cygwin1.dll to !IPERF_DEST!
    
    :: Add to System PATH using PowerShell (safe for long PATH values)
    powershell -Command "$target = 'C:\Tools\iperf3'; $path = [Environment]::GetEnvironmentVariable('Path', 'Machine'); if ($path -notlike \"*$target*\") { [Environment]::SetEnvironmentVariable('Path', \"$path;$target\", 'Machine'); Write-Host '[INFO] Added to System PATH.' } else { Write-Host '[INFO] Already in System PATH.' }"
    
    :: Verify installation
    "!IPERF_DEST!\iperf3.exe" --version >nul 2>&1
    if !errorLevel! equ 0 (
        echo [INFO] iperf3 installation verified successfully.
    ) else (
        echo [WARN] iperf3 copied. Logout/restart required for PATH activation.
    )
) else (
    echo [ERROR] Missing required files in resources folder!
)

echo.
echo ==========================================
echo   Setup Complete!
echo ==========================================
echo   Ready to connect:
echo   User: slave
echo   Pass: 66668888
echo.
pause