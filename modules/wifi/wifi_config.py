"""WiFi module configuration."""

from core.config import NetworkConfig

# WiFi standards to test
WIFI_STANDARDS_2G = ["11b/g/n", "11n/ax"]
WIFI_STANDARDS_5G = ["11a/n/ac", "11ac/ax"]

# Override with actual test standards
#WIFI_STANDARDS_2G = ["11b/g/n"]
#WIFI_STANDARDS_5G = ["11a/n/ac"]

# WiFi mode mappings (from router_manager.py)
WIFI_MODES_2G = {
    "11b/g/n": {
        "hwmode": "11g",
        "htmode": "HT40",
        "legacy_rates": "1"
    },
    "11b/g/n/ax": {
        "hwmode": "11g",
        "htmode": "HE40",
        "legacy_rates": "1"
    },
    "11g/n/ax": {
        "hwmode": "11g",
        "htmode": "HE40",
        "legacy_rates": "0"
    },
    "11n/ax": {
        "hwmode": "11g",
        "htmode": "HE40",
        "legacy_rates": "0",
        "require_mode": "n"
    }
}

WIFI_MODES_5G = {
    "11a/n/ac/ax": {
        "hwmode": "11a",
        "htmode": "HE80",
        "legacy_rates": "0"
    },
    "11a/n/ac": {
        "hwmode": "11a",
        "htmode": "VHT80",
        "legacy_rates": "0"
    },
    "11n/ac/ax": {
        "hwmode": "11a",
        "htmode": "HE80",
        "legacy_rates": "0",
        "require_mode": "n"
    },
    "11ac/ax": {
        "hwmode": "11a",
        "htmode": "HE80",
        "legacy_rates": "0",
        "require_mode": "ac"
    }
}

# Network configurations
NETWORKS = {
    "2G": {
        "ssid": NetworkConfig.SSID_2G,
        "password": NetworkConfig.WIFI_PASSWORD,
        "device": NetworkConfig.DEVICE_2G,
        "encryption": "psk2",
        "channels": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        #"channels": [13]
    },
    "5G": {
        "ssid": NetworkConfig.SSID_5G,
        "password": NetworkConfig.WIFI_PASSWORD,
        "device": NetworkConfig.DEVICE_5G,
        "encryption": "psk2",
        "channels": [36, 40, 44, 48, 149, 153, 157, 161, 165]
        #"channels": [165]
    }
}
