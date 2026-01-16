import html
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("ReportGen")


class IperfResult:
    """
    Represents parsed iperf3 test results (bandwidth only).
    """

    THRESHOLDS = {
        '11b':  {'excellent': 6,   'good': 5},
        '11g':  {'excellent': 22,  'good': 18},
        '11n':  {'excellent': 80,  'good': 50},   # 2.4GHz
        '11a':  {'excellent': 22,  'good': 18},
        '11ac': {'excellent': 450, 'good': 300},
        '11ax': {'excellent': 120, 'good': 80},
    }# depends on band


    def __init__(self, bandwidth: float):
        """
        Initialize iperf result.

        :param bandwidth: Bandwidth in Mbits/sec
        """
        self.bandwidth = bandwidth

    def get_speed_class(self, standard: str) -> str:
        """
        Determine CSS class based on standard-specific thresholds.

        :param standard: WiFi standard (e.g., '11n', '11ac')
        :return: CSS class name
        """
        thresholds = self.THRESHOLDS.get(standard, {'excellent': 100, 'good': 50})

        if self.bandwidth >= thresholds['excellent']:
            return "speed-excellent"
        elif self.bandwidth >= thresholds['good']:
            return "speed-good"
        else:
            return "speed-poor"


class ReportGenerator:
    """
    Generates HTML test reports from template and test results.
    """

    def __init__(self, template_path: Path, output_path: Path):
        """
        Initialize report generator.

        :param template_path: Path to HTML template file
        :param output_path: Path where report will be saved
        """
        self.template_path = template_path
        self.output_path = output_path
        self.wifi_results: Dict[str, Dict] = {}  # {band: {ssid, tests: []}}

        # Load template
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")

        with open(template_path, 'r', encoding='utf-8') as f:
            self.template = f.read()

    @staticmethod
    def parse_iperf_output(output: str) -> Optional[IperfResult]:
        """
        Parse iperf3 output to extract bandwidth (sender).

        Expected format:
        [ ID] Interval           Transfer     Bandwidth
        [  4]   0.00-10.00  sec  64.2 MBytes  53.9 Mbits/sec                  sender

        :param output: Raw iperf3 stdout
        :return: IperfResult object or None if parsing failed
        """
        try:
            # Match sender line for bandwidth
            pattern = r'\[\s*\d+\]\s+[\d\.\-]+\s+sec\s+[\d\.]+\s+[MGK]Bytes\s+([\d\.]+)\s+[MGK]bits/sec\s+sender'
            match = re.search(pattern, output)

            if match:
                bandwidth = float(match.group(1))
                logger.info(f"Parsed iperf: {bandwidth} Mbits/sec")
                return IperfResult(bandwidth)
            else:
                logger.warning("Could not parse iperf output")
                return None
        except Exception as e:
            logger.error(f"Error parsing iperf output: {e}")
            return None

    def add_wifi_test(self, band: str, ssid: str, standard: str, channel: int,
                      iperf_output: str) -> None:
        """
        Add WiFi test result to report.

        :param band: Frequency band (e.g., "2.4 GHz", "5 GHz")
        :param ssid: Network SSID
        :param standard: WiFi standard (e.g., "802.11n", "802.11ac")
        :param channel: WiFi channel number
        :param iperf_output: Raw iperf3 output
        """
        if band not in self.wifi_results:
            self.wifi_results[band] = {
                'ssid': ssid,
                'tests': []
            }

        result = self.parse_iperf_output(iperf_output)

        self.wifi_results[band]['tests'].append({
            'standard': standard,
            'channel': channel,
            'result': result
        })

        logger.info(f"Added test: {band} / {standard} / Ch{channel}")

    def _generate_wifi_content(self) -> str:
        """
        Generate WiFi section HTML from collected test results.

        :return: HTML string for WiFi section
        """
        if not self.wifi_results:
            return '<div class="no-data">No WiFi test data available</div>'

        html_parts = []

        for band, data in sorted(self.wifi_results.items()):
            ssid = data['ssid']
            tests = data['tests']

            # Group tests by standard
            tests_by_standard = {}
            for test in tests:
                standard = test['standard']
                if standard not in tests_by_standard:
                    tests_by_standard[standard] = []
                tests_by_standard[standard].append(test)

            # Band container
            html_parts.append(f'''
            <div class="band-container">
                <h3 class="band-title">{band} <span class="ssid">({ssid})</span></h3>
            ''')

            # Process each standard
            for standard in sorted(tests_by_standard.keys()):
                standard_tests = tests_by_standard[standard]

                # Sort tests by channel
                standard_tests_sorted = sorted(standard_tests, key=lambda x: x['channel'])

                # Calculate statistics
                bandwidths = [t['result'].bandwidth for t in standard_tests if t['result']]
                if bandwidths:
                    avg_bw = sum(bandwidths) / len(bandwidths)
                    avg_class = self._get_speed_class_from_value(avg_bw)
                else:
                    avg_bw = 0
                    avg_class = 'speed-poor'

                # Standard subheader
                html_parts.append(f'''
                <div class="standard-group">
                    <div class="standard-header">
                        <span class="standard-name">{standard}</span>
                    </div>
                    <table>
                        <thead>
                            <tr>
                ''')

                # Channel headers
                for test in standard_tests_sorted:
                    html_parts.append(f'<th>Ch {test["channel"]}</th>')

                html_parts.append('<th>Avg</th>')
                html_parts.append('</tr></thead><tbody><tr>')

                # Channel data
                for test in standard_tests_sorted:
                    result = test['result']
                    if result:
                        speed_class = result.get_speed_class(test['standard'])
                        html_parts.append(
                            f'<td><span class="speed-indicator {speed_class}">{result.bandwidth:.1f}</span></td>')
                    else:
                        html_parts.append('<td><span class="speed-indicator speed-poor">—</span></td>')

                # Average column
                html_parts.append(f'<td><span class="speed-indicator {avg_class}">{avg_bw:.1f}</span></td>')

                html_parts.append('''
                            </tr>
                        </tbody>
                    </table>
                </div>
                ''')

            html_parts.append('</div>')

        return ''.join(html_parts)

    @staticmethod
    def _get_speed_class_from_value(bandwidth: float) -> str:
        """
        Determine CSS class based on bandwidth value.

        :param bandwidth: Bandwidth in Mbits/sec
        :return: CSS class name
        """
        if bandwidth >= 100:
            return "speed-excellent"
        elif bandwidth >= 50:
            return "speed-good"
        else:
            return "speed-poor"

    def generate(self, device_name: str, ip_address: str) -> None:
        """
        Generate final HTML report and save to file.

        :param device_name: Device system product name
        :param ip_address: Device IP address
        """

        safe_device_name = html.escape(device_name)
        safe_ip = html.escape(ip_address)

        wifi_content = self._generate_wifi_content()

        html_content = self.template.replace('{DEVICE_NAME}', safe_device_name)
        html_content = html_content.replace('{IP_ADDRESS}', safe_ip)
        html_content = html_content.replace('{TIMESTAMP}', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        html_content = html_content.replace('{WIFI_CONTENT}', wifi_content)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        logger.info(f"Report generated: {self.output_path}")

    @staticmethod
    def generate_report_filename(device_name: str, ip_address: str) -> str:
        """
        Generate unique report filename.

        :param device_name: Device system product name
        :param ip_address: Device IP address
        :return: Filename string (e.g., "ThinkPad-X1_192-168-50-178_20250113-141530.html")
        """
        # Sanitize device name (remove spaces, special chars)
        safe_name = re.sub(r'[^\w\-]', '_', device_name)
        safe_ip = ip_address.replace('.', '-')
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')

        return f"{safe_name}_{safe_ip}_{timestamp}.html"