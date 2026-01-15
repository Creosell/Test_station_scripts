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
    cmd_iperf.add_argument("--port", type=int, default=5201, help="Port to connect to")

    # Command: forget
    cmd_forget = subparsers.add_parser("forget")

    # Command: prevent_sleep
    cmd_prevent_sleep = subparsers.add_parser("prevent_sleep", help="Prevent system sleep/screen timeout")

    # Command: allow_sleep
    cmd_allow_sleep = subparsers.add_parser("allow_sleep", help="Re-enable system sleep/screen timeout")

    # Command: init_report
    cmd_init_report = subparsers.add_parser("init_report", help="Initialize HTML report on DUT")
    cmd_init_report.add_argument("--device_name", required=True)
    cmd_init_report.add_argument("--ip_address", required=True)
    cmd_init_report.add_argument("--report_dir", required=True)

    # Command: add_result
    cmd_add_result = subparsers.add_parser("add_result", help="Add test result to report")
    cmd_add_result.add_argument("--report_path", required=True)
    cmd_add_result.add_argument("--band", required=True)
    cmd_add_result.add_argument("--ssid", required=True)
    cmd_add_result.add_argument("--standard", required=True)
    cmd_add_result.add_argument("--channel", type=int, required=True)
    cmd_add_result.add_argument("--iperf_output", required=True)

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
            output = dm.run_iperf(port=args.port)
            if output:
                print(f"IPERF_OUTPUT_START\n{output}\nIPERF_OUTPUT_END")
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

        elif args.command == "forget":
            dm.forget_all_networks()
            print("RESULT:SUCCESS")

        elif args.command == "prevent_sleep":
            success = dm.prevent_sleep()
            if success:
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

        elif args.command == "allow_sleep":
            success = dm.allow_sleep()
            if success:
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

        elif args.command == "init_report":
            report_path = dm.initialize_report(args.device_name, args.ip_address, args.report_dir)
            if report_path:
                print(f"REPORT_PATH:{report_path}")
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

        elif args.command == "add_result":
            success = dm.add_test_result(
                args.report_path,
                args.band,
                args.ssid,
                args.standard,
                args.channel,
                args.iperf_output
            )
            if success:
                print("RESULT:SUCCESS")
            else:
                print("RESULT:FAILURE")
                sys.exit(1)

    except Exception as e:
        print(f"ERROR:{str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()