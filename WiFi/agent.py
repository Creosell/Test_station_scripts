# agent.py
import argparse
import sys
import logging
from device_manager import DeviceManager

# Configure basic logging for the agent execution
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("Agent")


def main():
    parser = argparse.ArgumentParser(description="Remote WiFi Test Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: connect
    cmd_connect = subparsers.add_parser("connect")
    cmd_connect.add_argument("--ssid", required=True)
    cmd_connect.add_argument("--password", required=True)
    cmd_connect.add_argument("--cleanup", action="store_true", help="Forget other networks before connecting")

    # Command: iperf
    cmd_iperf = subparsers.add_parser("iperf")

    # Command: forget
    cmd_forget = subparsers.add_parser("forget")

    # Command: sysinfo
    cmd_sysinfo = subparsers.add_parser("sysinfo", help="Get system product name")

    args = parser.parse_args()

    # Initialize DeviceManager (it will detect OS locally)
    dm = DeviceManager()

    try:
        if args.command == "connect":
            success = dm.connect_wifi(args.ssid, args.password, cleanup=args.cleanup)
            if success:
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

        elif args.command == "iperf":
            output = dm.run_iperf()
            if output:
                print(f"IPERF_OUTPUT_START\n{output}\nIPERF_OUTPUT_END")
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

        elif args.command == "forget":
            dm.forget_all_networks()
            print("RESULT:SUCCESS")

        elif args.command == "sysinfo":
            product_name = dm.get_system_product_name()
            print(f"SYSTEM_PRODUCT:{product_name}")
            print("RESULT:SUCCESS")

    except Exception as e:
        print(f"ERROR:{str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()