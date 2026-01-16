# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Modular testing framework for automated performance testing across multiple Device Under Test (DUT) units. The system orchestrates tests by:
- Remotely controlling OpenWrt router configuration via SSH (channel, WiFi standard switching)
- Deploying test agents to Windows/Linux devices via SSH/SCP
- Running iperf3 throughput tests across multiple bands, channels, and WiFi standards
- Generating HTML reports with incremental test results

Currently focused on WiFi testing, with architecture designed for future expansion (Bluetooth, Ethernet, etc.).

## Project Structure

The repository uses modular architecture with clear separation of concerns:

```
Test_station_scripts/
├── core/                      # Universal framework components
│   ├── config.py             # Global configuration (paths, network, logging)
│   ├── core_orchestrator.py  # Abstract base class for test orchestrators
│   ├── core_report.py        # Unified report generator (multi-module support)
│   ├── remote_executor.py    # SSH/SCP with plugin deployment system
│   ├── device_discovery.py   # Auto-discovers DUTs via router DHCP
│   └── router_manager.py     # OpenWrt UCI interface for router control
│
├── agent/                     # Universal agent deployed to DUTs
│   ├── agent.py              # Plugin loader system (CLI entry point)
│   ├── agent_device_manager.py  # Base OS operations (sleep, system info)
│   └── plugins/
│       └── wifi_plugin.py    # WiFi-specific commands (connect, iperf, reports)
│
├── modules/                   # Isolated test modules
│   └── wifi/
│       ├── wifi_config.py    # WiFi-specific constants (WIFI_MODES, NETWORKS)
│       └── wifi_orchestrator.py  # WiFi test coordinator (inherits CoreOrchestrator)
│
├── test_suite.py             # Global orchestrator (coordinates multiple modules)
├── resources/                # Shared resources (templates, profiles)
├── reports/                  # Generated HTML reports
│
└── WiFi/                     # Legacy structure (for backward compatibility)
    └── slave_setup/          # Windows DUT setup scripts
```

**Key directories:**
- `core/` - Reusable components for all test modules
- `agent/` - Plugin-based agent system deployed to DUTs
- `modules/wifi/` - WiFi-specific test logic and configuration
- `WiFi/` - Legacy files (maintained for backward compatibility)

## Architecture

### Modular Plugin-Based Architecture

**Three-Layer Design:**

1. **Core Layer** - Universal components:
   - `CoreOrchestrator` - Abstract base class with cleanup, emergency handlers
   - `CoreReportGenerator` - Multi-module report aggregation
   - `RemoteDeviceExecutor` - Plugin-aware SSH/SCP deployment
   - `RouterManager` - Router configuration (accepts mode configs from modules)

2. **Agent Layer** - Deployed to DUTs:
   - `agent.py` - Plugin loader (`python agent.py <plugin> <command> --args`)
   - `AgentDeviceManager` - Base OS operations (prevent_sleep, get_system_product_name)
   - Plugins - Module-specific commands (e.g., `wifi_plugin.py`)

3. **Module Layer** - Test-specific implementations:
   - `wifi_orchestrator.py` - Inherits `CoreOrchestrator`, implements abstract methods
   - `wifi_config.py` - WiFi-specific constants (WIFI_MODES, NETWORKS, WIFI_STANDARDS)

### Orchestrator-Agent Pattern

**Orchestrator (Master Station):**
- `test_suite.py` - Global coordinator (runs multiple modules)
- `modules/wifi/wifi_orchestrator.py` - WiFi test coordinator
- `core/router_manager.py` - SSH interface to OpenWrt router
- `core/remote_executor.py` - Deploys agents with specified plugins
- `core/device_discovery.py` - Auto-discovers DUTs via router DHCP

**Agent (Device Under Test):**
- `agent/agent.py` - Plugin loader system
- `agent/agent_device_manager.py` - Base OS operations
- `agent/plugins/wifi_plugin.py` - WiFi commands (connect, iperf, init_report, add_result)

**Plugin Command Flow:**
```
Orchestrator → RemoteExecutor.run_plugin_command('wifi', 'connect', ssid='X', password='Y')
    ↓ SSH
DUT → python agent.py wifi connect --ssid X --password Y
    ↓ Load plugin
DUT → WifiPlugin.execute('connect', ['--ssid', 'X', '--password', 'Y'])
    ↓ Execute
DUT → WifiPlugin._connect(ssid='X', password='Y')
```

### Configuration Structure

**Global configuration:** `core/config.py`
- `Paths` - Path resolution with validation (uses `pathlib`)
- `NetworkConfig` - Router IP, SSIDs, credentials, iperf port pool
- `DUTConfig` - Device list with SSH credentials
- `RouterConfig` - Router access for device discovery
- `ReportPaths` - Local and remote report directories
- `Timings` - Timeout/delay constants
- `Limits` - Retry limits
- Logging classes: `ConsoleOverwriterHandler`, `MinimalFormatter`

**Module-specific configuration:** `modules/wifi/wifi_config.py`
- `WIFI_STANDARDS_2G/5G` - Testable WiFi modes
- `WIFI_MODES_2G/5G` - Mode mappings (hwmode, htmode, legacy_rates)
- `NETWORKS` - Band-specific channel/standard configurations

**Configuration separation principle:**
- Core config = Framework-level settings (paths, SSH, logging)
- Module config = Test-specific settings (WiFi standards, channels, thresholds)

### Device Discovery

`core/device_discovery.py` automatically detects DUTs:
1. Queries router DHCP leases via SSH
2. Attempts SSH probe to each client
3. Detects OS type (Windows/Linux) via command execution
4. Detects Python path (`python`, `python3`, `py`)
5. Generates `modules/wifi/discovered_devices.py` with DUT configs

**Output location:** `modules/wifi/discovered_devices.py` (auto-generated)
- Contains `DEVICES` list matching `DUTConfig.DEVICES` format
- Can be imported to replace manual device list in `config.py`
- Regenerated on each discovery run

Usage:
```bash
# Old way (WiFi module standalone)
python modules/wifi/wifi_orchestrator.py --auto-discover

# New way (via global orchestrator)
python test_suite.py --modules wifi --auto-discover
```

### DUT Setup (Windows)

**Automated setup script:** `WiFi/slave_setup/setup_agent.bat`

This script prepares Windows devices for testing (run with admin rights on each DUT):

**What it does:**
1. Installs OpenSSH Server (from `resources/OpenSSH-Win64.zip`)
2. Configures sshd service to auto-start
3. Opens firewall port 22
4. Sets cmd.exe as default SSH shell
5. Creates 'slave' user (password: 66668888) with admin rights
6. Disables password expiration for 'slave'
7. Installs Python 3.12.6 (from `resources/python-3.12.6-amd64.exe`)
8. Installs iperf3 to `C:\Tools\iperf3` (requires `resources/iperf3.exe` and `resources/cygwin1.dll`)

**Required resources in `WiFi/slave_setup/resources/`:**
- `OpenSSH-Win64.zip` - SSH server for Windows
- `python-3.12.6-amd64.exe` - Python installer
- `iperf3.exe` + `cygwin1.dll` - Performance testing tool

**After setup:** Device ready for SSH connection with credentials `slave:66668888`

### Critical Flow: WiFi Switching with Link Recovery

When `core/remote_executor.py` sends WiFi connection command that switches networks:
1. SSH connection WILL DROP (expected behavior)
2. `RemoteDeviceExecutor._wait_for_reconnection()` polls for device recovery
3. Once DUT reconnects, testing resumes

**This is NOT an error** - it's designed behavior. Do not "fix" this.

### Router Configuration

`core/router_manager.py` uses OpenWrt UCI commands:
- `change_standard(device, mode, mode_config)` - Accepts mode config dict from module
- WiFi mode configs defined in `modules/wifi/wifi_config.py`:
  - `WIFI_MODES_2G`: `11b/g/n`, `11b/g/n/ax`, `11g/n/ax`, `11n/ax`
  - `WIFI_MODES_5G`: `11a/n/ac/ax`, `11a/n/ac`, `11n/ac/ax`, `11ac/ax`
- Each config change requires `wifi reload` + verification loop
- Connection pooling: SSH kept alive with keepalive packets

**Mode config structure:**
```python
{
    "hwmode": "11g",        # Hardware mode
    "htmode": "HE40",       # Channel width/protocol
    "legacy_rates": "1",    # Enable legacy rates
    "require_mode": "n"     # Optional: minimum required mode
}
```

### Report Generation

**Incremental reporting on DUT:**
1. `agent.py wifi init_report` creates HTML from template on DUT
2. After each test: `agent.py wifi add_result` appends result to DUT report
3. `CoreReportGenerator.parse_iperf_output()` extracts bandwidth (sender line)
4. Results grouped by standard with avg/min/max statistics
5. CSS classes determined by `IperfResult.THRESHOLDS` (standard-specific)
6. At completion: report downloaded via SCP to `reports/`

**Thresholds (Mbps):**
- `11b`: excellent ≥6, good ≥5
- `11g`: excellent ≥22, good ≥18
- `11n`: excellent ≥80, good ≥50
- `11ac`: excellent ≥450, good ≥300
- `11ax`: excellent ≥120, good ≥80

### Logging System

Custom logging with `ConsoleOverwriterHandler` + `MinimalFormatter`:
- Colored output with level symbols: `•` (info), `⚠` (warning), `✖` (error)
- Overwrites current line for progress updates
- Paramiko logging suppressed to WARNING level
- Timestamp format: `HH:MM:SS`

## Running Tests

### Environment Setup
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux
pip install -r requirements.txt
```

### Execute Tests

**New Way (Recommended) - Via Global Orchestrator:**
```bash
# Sequential mode (one device at a time)
python test_suite.py --modules wifi

# Parallel mode (all devices test same channel simultaneously)
python test_suite.py --modules wifi --parallel

# With auto-discovery
python test_suite.py --modules wifi --auto-discover --parallel

# Future: Multiple modules
python test_suite.py --modules wifi,bluetooth --parallel
```

**Legacy Way - WiFi Module Standalone:**
```bash
# Sequential mode
python modules/wifi/wifi_orchestrator.py

# Parallel mode
python modules/wifi/wifi_orchestrator.py --parallel

# With auto-discovery
python modules/wifi/wifi_orchestrator.py --auto-discover --parallel
```

**Note:** Both methods produce identical results. Global orchestrator provides unified interface for multiple modules.

### Configuration

**Global config:** `core/config.py`
- `NetworkConfig.ROUTER_IP` - Router address
- `NetworkConfig.SSID_2G/5G` - Network names
- `NetworkConfig.WIFI_PASSWORD` - Network password
- `NetworkConfig.IPERF_PORTS` - Port pool for parallel testing (5201-5221)
- `DUTConfig.DEVICES` - Manual device list (or use `--auto-discover`)

**WiFi module config:** `modules/wifi/wifi_config.py`
- `NETWORKS["2G"]["channels"]` - 2.4 GHz channels to test
- `NETWORKS["5G"]["channels"]` - 5 GHz channels to test
- `WIFI_STANDARDS_2G/5G` - WiFi modes to test
- `WIFI_MODES_2G/5G` - Mode configuration mappings

## Key Behaviors

### Sequential testing Mode (Default - Recommended)
Each device tested individually with full router resources.
- Provides accurate peak performance measurements
- Results directly comparable between devices
- Use for: device comparison, certification, benchmark

### Parallel testing Mode (Stress Testing)
All devices test simultaneously on same channel.
- Tests router stability under load
- Results reflect shared bandwidth scenario
- Use for: multi-client stress tests, QoS validation
- ⚠️ **Not suitable for measuring individual device performance**

**2.4 GHz Warning:** Parallel testing on 2.4 GHz highly discouraged due to:
- Limited non-overlapping channels (1, 6, 11)
- High collision rate
- Unpredictable airtime sharing

### Parallel Testing Synchronization

In parallel mode (`run_parallel_suite`):
- All devices test SAME channel before router switches to next channel
- Each device gets dedicated iperf port from `IPERF_PORTS` pool
- Devices with 3+ consecutive failures excluded from further testing
- Timeouts prevent one frozen device from blocking others

### Script Deployment

`core/remote_executor.py` plugin-based deployment process:

**Core files deployed to all DUTs:**
- `agent/agent.py` → `agent.py` (plugin loader)
- `agent/agent_device_manager.py` → `agent_device_manager.py` (base OS ops)
- `core/config.py` → `config.py` (global config)
- `core/core_report.py` → `report_generator.py` (report generation)

**Plugin files deployed per module:**
- `agent/plugins/wifi_plugin.py` → `plugins/wifi_plugin.py` (WiFi commands)
- Future: `agent/plugins/bluetooth_plugin.py` → `plugins/bluetooth_plugin.py`

**Deployment locations:**
- Windows: `C:\Temp\wifi_test_agent`
- Linux: `/tmp/wifi_test_agent`

**Resources:**
- `resources/` folder recursively uploaded (WiFi profiles, report templates)

**Method signature:**
```python
executor.deploy_agent(plugins=['wifi'])  # Deploys agent with WiFi plugin
executor.run_plugin_command('wifi', 'connect', ssid='X', password='Y')
```

### Path Validation

`Paths.get_validated_path()` ensures required files exist before deployment:
- Raises `FileNotFoundError` immediately if resource missing
- Prevents silent failures during remote execution
- Uses `pathlib.Path` for cross-platform compatibility

## Adding New Test Modules

To add a new test module (e.g., Bluetooth):

1. **Create module directory:** `modules/bluetooth/`
2. **Create module config:** `modules/bluetooth/bluetooth_config.py`
3. **Create orchestrator:** `modules/bluetooth/bluetooth_orchestrator.py` (inherit `CoreOrchestrator`)
4. **Create agent plugin:** `agent/plugins/bluetooth_plugin.py`
5. **Register in test_suite.py:** Add to `self.modules` dict
6. **Update remote_executor.py:** Add plugin to deployment list if needed

**Orchestrator requirements:**
- Inherit from `core.core_orchestrator.CoreOrchestrator`
- Implement `run_full_suite()` → returns results dict
- Implement `run_parallel_suite(max_workers=None)` → returns results dict
- Call `super().__init__(report_generator)` in `__init__`

**Plugin requirements:**
- Class named `<Module>Plugin` (e.g., `BluetoothPlugin`)
- Method `execute(command, args)` → returns result or raises exception
- Import `agent_device_manager.AgentDeviceManager` for base OS ops

## Dependencies

- `paramiko` - SSH client for router and DUT communication
- `scp` - File transfer to DUTs
- `tenacity` - Retry logic for SSH connections
- `iperf3` - Must be installed on DUTs and router for throughput testing

## Important Notes

- Router must be OpenWrt-based with UCI interface
- DUTs must have SSH enabled with credentials in `DUTConfig.DEVICES`
- Windows DUTs require Python in PATH (specified in `python_path` config)
- iperf3 server must run on router or designated server
- WiFi standard names must match `WIFI_MODES_2G`/`WIFI_MODES_5G` keys exactly
- Report template location: `resources/report_template.html`
- GL-MT3000 router uses MediaTek chipset with device names `mt798111` (2.4G) / `mt798112` (5G)
- Legacy `WiFi/` directory maintained for backward compatibility; new code should use modular structure
