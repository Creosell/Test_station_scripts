"""
Universal test agent with plugin system.
Supports modular test commands via plugins.
"""

import argparse
import sys

PLUGINS = {}


def load_plugin(plugin_name):
    """Lazy load plugin."""
    if plugin_name not in PLUGINS:
        try:
            module = __import__(f'plugins.{plugin_name}_plugin', fromlist=[f'{plugin_name.capitalize()}Plugin'])
            plugin_class_name = f'{plugin_name.capitalize()}Plugin'
            PLUGINS[plugin_name] = getattr(module, plugin_class_name)()
        except Exception as e:
            print(f"ERROR: Failed to load plugin '{plugin_name}': {e}")
            sys.exit(1)
    return PLUGINS[plugin_name]


def main():
    parser = argparse.ArgumentParser(description="Universal Test Agent")
    parser.add_argument('plugin', help='Plugin name (wifi, bluetooth, etc.)')
    parser.add_argument('command', help='Plugin command')

    # Parse remaining args as --key value pairs
    args, unknown = parser.parse_known_args()

    try:
        plugin = load_plugin(args.plugin)
        result = plugin.execute(args.command, unknown)

        if result:
            print("RESULT:SUCCESS")
            if isinstance(result, str):
                print(result)
        else:
            print("RESULT:FAILURE")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR:{str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
