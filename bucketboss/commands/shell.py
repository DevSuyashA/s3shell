import os


def do_exit(app, *args):
    """Exit the shell."""
    print("Saving cache...")
    app._save_cache()
    print("Exiting...")
    return False


def do_clear(app, *args):
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def do_help(app, *args):
    """Show available commands."""
    print("\nAvailable commands:")
    for cmd in sorted(app.commands.keys()):
        print(f"  {cmd}")
    print("\nUse TAB for completion.")
