import sys

MISSING_CLI_EXTRA = (
    "The biblo command needs the CLI extra: pip install 'biblo[cli]' "
    "or uvx --from 'biblo[cli]' biblo"
)


def main() -> None:
    try:
        from .cli import main as cli_main
    except ModuleNotFoundError as err:
        if err.name not in ("typer", "rich"):
            raise
        print(MISSING_CLI_EXTRA, file=sys.stderr)
        sys.exit(1)
    cli_main()


if __name__ == "__main__":
    main()
